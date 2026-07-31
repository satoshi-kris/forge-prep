"""
Corpus Cleaner — deduplicates, scrubs PII, filters low-quality files,
and outputs a clean, Forge-ready corpus.
"""

import hashlib
import os
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from forge_prep import pii
from forge_prep.auditor import SUPPORTED_EXTENSIONS


@dataclass
class CleaningResult:
    status: str = "ok"  # "ok" | "no_supported_files"
    files_processed: int = 0
    files_kept: int = 0
    files_removed: int = 0
    files_unreadable: int = 0
    duplicates_removed: int = 0
    pii_scrubbed_files: int = 0
    pii_replacements: int = 0
    bytes_before: int = 0
    bytes_after: int = 0
    actions_log: list = field(default_factory=list)
    files_present: int = 0
    files_skipped_by_reason: dict = field(default_factory=dict)
    skipped_extensions: dict = field(default_factory=dict)
    supported_extensions: list = field(default_factory=list)

    @property
    def reduction_pct(self) -> float:
        if self.bytes_before == 0:
            return 0
        return (1 - self.bytes_after / self.bytes_before) * 100


class CorpusCleaner:
    """Cleans a corpus directory and writes output to a new directory."""

    def __init__(
        self,
        input_path: str,
        output_path: str,
        dedup: bool = True,
        scrub_pii: bool = True,
        min_chars: int = 100,
        min_text_density: float = 0.3,
        max_repetition_ratio: float = 0.5,
        ip_mode: str = "public",
        context_denylist: frozenset | None = None,
        include_ext: set | None = None,
        exclude_ext: set | None = None,
        strict_pii: bool = False,
    ):
        self.input_path = Path(input_path)
        self.output_path = Path(output_path)
        self.dedup = dedup
        self.scrub_pii = scrub_pii
        self.min_chars = min_chars
        self.min_text_density = min_text_density
        self.max_repetition_ratio = max_repetition_ratio
        self.ip_mode = ip_mode
        self.context_denylist = context_denylist
        self.extensions = (SUPPORTED_EXTENSIONS | (include_ext or set())) - (exclude_ext or set())
        self.strict_pii = strict_pii

    def clean(self) -> CleaningResult:
        result = CleaningResult()
        files, files_present, skipped_ext_counter = self._discover_files()
        result.files_present = files_present
        result.skipped_extensions = dict(skipped_ext_counter)

        if not files:
            result.status = "no_supported_files"
            result.supported_extensions = sorted(self.extensions)
            result.files_skipped_by_reason = {
                "unsupported_extension": sum(skipped_ext_counter.values()),
                "oversized": 0,
                "unreadable": 0,
            }
            return result

        self.output_path.mkdir(parents=True, exist_ok=True)
        seen_hashes: dict[str, str] = {}

        for fpath in files:
            result.files_processed += 1
            result.bytes_before += fpath.stat().st_size

            # rel stays a Path — used below for the actual filesystem write,
            # which must use the platform's native separators. rel_str is the
            # forward-slash form used anywhere a path is recorded as text
            # (actions_log, dedup lookups), so logs/output are identical
            # across platforms regardless of what OS produced them.
            rel = fpath.relative_to(self.input_path)
            rel_str = rel.as_posix()

            try:
                text = fpath.read_text(encoding="utf-8", errors="replace")
            except Exception:
                result.files_removed += 1
                result.files_unreadable += 1
                result.actions_log.append(f"SKIP (unreadable): {rel_str}")
                continue

            # --- Quality filter ---
            if len(text) < self.min_chars:
                result.files_removed += 1
                result.actions_log.append(f"SKIP (too short: {len(text)} chars): {rel_str}")
                continue

            sample = text[:10_000]
            if len(sample) > 0:
                alpha_ratio = sum(c.isalpha() for c in sample) / len(sample)
                if alpha_ratio < self.min_text_density:
                    result.files_removed += 1
                    result.actions_log.append(f"SKIP (low text density: {alpha_ratio:.2f}): {rel_str}")
                    continue

            # Repetition check
            lines = text.strip().split("\n")
            if len(lines) > 20:
                unique_ratio = len(set(lines)) / len(lines)
                if unique_ratio < self.max_repetition_ratio:
                    result.files_removed += 1
                    result.actions_log.append(f"SKIP (high repetition: {unique_ratio:.2f}): {rel_str}")
                    continue

            # --- Deduplication ---
            if self.dedup:
                content_hash = hashlib.sha256(text.encode(), usedforsecurity=False).hexdigest()
                if content_hash in seen_hashes:
                    result.duplicates_removed += 1
                    result.files_removed += 1
                    result.actions_log.append(f"DEDUP (duplicate of {seen_hashes[content_hash]}): {rel_str}")
                    continue
                seen_hashes[content_hash] = rel_str

            # --- PII scrubbing (shared validated detection path with the auditor) ---
            if self.scrub_pii:
                text, counts = pii.redact(
                    text, ip_mode=self.ip_mode, context_denylist=self.context_denylist,
                    file_path=fpath, strict_pii=self.strict_pii,
                )
                pii_count = sum(counts.values())
                if pii_count > 0:
                    result.pii_scrubbed_files += 1
                    result.pii_replacements += pii_count
                    result.actions_log.append(f"PII_SCRUB ({pii_count} replacements): {rel_str}")

            # --- Write clean file ---
            out_file = self.output_path / rel
            out_file.parent.mkdir(parents=True, exist_ok=True)
            out_file.write_text(text, encoding="utf-8")
            result.files_kept += 1
            result.bytes_after += len(text.encode("utf-8"))

        result.files_skipped_by_reason = {
            "unsupported_extension": sum(skipped_ext_counter.values()),
            "oversized": 0,
            "unreadable": result.files_unreadable,
        }

        return result

    def _discover_files(self) -> tuple[list[Path], int, Counter]:
        """Return (supported files, total files seen, Counter of skipped extensions)."""
        supported = []
        files_present = 0
        skipped_ext_counter = Counter()
        for root, _dirs, filenames in os.walk(self.input_path):
            for fname in filenames:
                fpath = Path(root) / fname
                files_present += 1
                ext = fpath.suffix.lower()
                if ext in self.extensions:
                    supported.append(fpath)
                else:
                    skipped_ext_counter[ext or "(no extension)"] += 1
        return sorted(supported), files_present, skipped_ext_counter

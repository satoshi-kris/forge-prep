"""
Corpus Cleaner — deduplicates, scrubs PII, filters low-quality files,
and outputs a clean, Forge-ready corpus.
"""

import os
import re
import json
import shutil
import hashlib
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

from forge_prep.auditor import PII_PATTERNS, SUPPORTED_EXTENSIONS


@dataclass
class CleaningResult:
    files_processed: int = 0
    files_kept: int = 0
    files_removed: int = 0
    duplicates_removed: int = 0
    pii_scrubbed_files: int = 0
    pii_replacements: int = 0
    bytes_before: int = 0
    bytes_after: int = 0
    actions_log: list = field(default_factory=list)

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
    ):
        self.input_path = Path(input_path)
        self.output_path = Path(output_path)
        self.dedup = dedup
        self.scrub_pii = scrub_pii
        self.min_chars = min_chars
        self.min_text_density = min_text_density
        self.max_repetition_ratio = max_repetition_ratio

    def clean(self) -> CleaningResult:
        result = CleaningResult()
        self.output_path.mkdir(parents=True, exist_ok=True)

        seen_hashes: dict[str, str] = {}
        files = self._discover_files()

        for fpath in files:
            result.files_processed += 1
            result.bytes_before += fpath.stat().st_size

            try:
                text = fpath.read_text(encoding="utf-8", errors="replace")
            except Exception:
                result.files_removed += 1
                result.actions_log.append(f"SKIP (unreadable): {fpath}")
                continue

            rel = fpath.relative_to(self.input_path)

            # --- Quality filter ---
            if len(text) < self.min_chars:
                result.files_removed += 1
                result.actions_log.append(f"SKIP (too short: {len(text)} chars): {rel}")
                continue

            sample = text[:10_000]
            if len(sample) > 0:
                alpha_ratio = sum(c.isalpha() for c in sample) / len(sample)
                if alpha_ratio < self.min_text_density:
                    result.files_removed += 1
                    result.actions_log.append(f"SKIP (low text density: {alpha_ratio:.2f}): {rel}")
                    continue

            # Repetition check
            lines = text.strip().split("\n")
            if len(lines) > 20:
                unique_ratio = len(set(lines)) / len(lines)
                if unique_ratio < self.max_repetition_ratio:
                    result.files_removed += 1
                    result.actions_log.append(f"SKIP (high repetition: {unique_ratio:.2f}): {rel}")
                    continue

            # --- Deduplication ---
            if self.dedup:
                content_hash = hashlib.md5(text.encode()).hexdigest()
                if content_hash in seen_hashes:
                    result.duplicates_removed += 1
                    result.files_removed += 1
                    result.actions_log.append(f"DEDUP (duplicate of {seen_hashes[content_hash]}): {rel}")
                    continue
                seen_hashes[content_hash] = str(rel)

            # --- PII scrubbing ---
            if self.scrub_pii:
                text, pii_count = self._scrub_pii(text)
                if pii_count > 0:
                    result.pii_scrubbed_files += 1
                    result.pii_replacements += pii_count
                    result.actions_log.append(f"PII_SCRUB ({pii_count} replacements): {rel}")

            # --- Write clean file ---
            out_file = self.output_path / rel
            out_file.parent.mkdir(parents=True, exist_ok=True)
            out_file.write_text(text, encoding="utf-8")
            result.files_kept += 1
            result.bytes_after += len(text.encode("utf-8"))

        return result

    def _discover_files(self) -> list[Path]:
        files = []
        for root, _dirs, filenames in os.walk(self.input_path):
            for fname in filenames:
                fpath = Path(root) / fname
                if fpath.suffix.lower() in SUPPORTED_EXTENSIONS:
                    files.append(fpath)
        return sorted(files)

    def _scrub_pii(self, text: str) -> tuple[str, int]:
        total_replacements = 0
        replacement_map = {
            "email": "[EMAIL_REDACTED]",
            "phone_intl": "[PHONE_REDACTED]",
            "ip_address": "[IP_REDACTED]",
            "credit_card": "[CC_REDACTED]",
            "ssn_us": "[SSN_REDACTED]",
            "iban": "[IBAN_REDACTED]",
            "french_nir": "[NIR_REDACTED]",
        }
        for pii_type, pattern in PII_PATTERNS.items():
            matches = pattern.findall(text)
            if matches:
                total_replacements += len(matches)
                text = pattern.sub(replacement_map[pii_type], text)
        return text, total_replacements

"""
Corpus Auditor — scans a directory of documents and produces
a structured audit of data quality, diversity, and Forge readiness.
"""

import hashlib
import json
import os
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from forge_prep import pii


@dataclass
class FileAudit:
    path: str
    size_bytes: int
    extension: str
    encoding: str | None = None
    language: str | None = None
    token_estimate: int = 0
    token_estimate_low: int = 0
    token_estimate_high: int = 0
    token_estimate_confident: bool = True
    char_count: int = 0
    line_count: int = 0
    word_count: int = 0
    is_duplicate: bool = False
    duplicate_of: str | None = None
    quality_flags: list = field(default_factory=list)
    pii_detected: list = field(default_factory=list)
    pii_scan_truncated: bool = False


@dataclass
class CorpusAuditResult:
    timestamp: str
    corpus_path: str
    total_files: int = 0
    total_size_bytes: int = 0
    total_tokens_estimate: int = 0
    total_tokens_estimate_low: int = 0
    total_tokens_estimate_high: int = 0
    tokens_estimate_uncertain: bool = False
    file_type_distribution: dict = field(default_factory=dict)
    language_distribution: dict = field(default_factory=dict)
    duplicate_count: int = 0
    duplicate_bytes: int = 0
    pii_files_count: int = 0
    quality_issues: dict = field(default_factory=dict)
    file_audits: list = field(default_factory=list)
    recommendations: list = field(default_factory=list)

    def to_dict(self):
        return asdict(self)

    def to_json(self, path: str):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)


# PII_PATTERNS re-exported for backward compatibility — the actual detection
# and validation logic lives in forge_prep.pii (shared by auditor and cleaner).
PII_PATTERNS = pii.PII_PATTERNS

# --- Language detection heuristics (no external deps) ---
LANG_MARKERS = {
    "fr": ["le", "la", "les", "de", "du", "des", "un", "une", "est", "sont", "dans", "pour", "avec", "sur", "par", "cette", "ces", "qui", "que", "nous", "vous", "leur"],
    "en": ["the", "is", "are", "was", "were", "have", "has", "been", "will", "would", "could", "should", "this", "that", "with", "from", "they", "their", "which"],
    "de": ["der", "die", "das", "ein", "eine", "ist", "sind", "und", "oder", "aber", "nicht", "haben", "werden", "nach", "über", "unter"],
    "es": ["el", "la", "los", "las", "un", "una", "es", "son", "está", "están", "con", "para", "por", "como", "pero", "más", "muy"],
}

SUPPORTED_EXTENSIONS = {
    ".txt", ".md", ".csv", ".json", ".jsonl", ".xml",
    ".html", ".htm", ".py", ".js", ".ts", ".yaml", ".yml",
    ".rst", ".tex", ".log", ".tsv", ".sql", ".sh",
}

# Token-count multipliers, in characters per token (token_estimate =
# char_count / multiplier). Fit against a real tokenizer (tiktoken
# cl100k_base) on examples/sample-corpus/, weighted sum(chars)/sum(tokens)
# per content-type group.
#
# Characters were chosen over whitespace-split words as the estimation unit
# because words-per-token is far less stable across files: on the fitting
# corpus, coefficient of variation was 0.83 for structured data (CSV/JSONL)
# vs. 0.05 for chars-per-token on the same files, and 0.12 vs. 0.10 for
# prose. "Words" from whitespace splitting breaks down on structured
# formats with little whitespace, where one "word" can be an entire row.
#
# Extensions not listed fall back to DEFAULT_TOKEN_MULTIPLIER, the
# corpus-wide weighted average, since they weren't individually fit.
# See docs/methodology.md for the full fitting + held-out evaluation.
TOKEN_MULTIPLIERS = {
    ".txt": 4.12, ".md": 4.12,  # prose
    ".py": 3.91,  # code
    ".csv": 2.70, ".jsonl": 2.70, ".json": 2.70,  # structured
}
DEFAULT_TOKEN_MULTIPLIER = 3.86

EXTENSION_TOKEN_GROUP = {
    ".txt": "prose", ".md": "prose",
    ".py": "code",
    ".csv": "structured", ".jsonl": "structured", ".json": "structured",
}

# Held-out mean-absolute-error per group, measured on files that were never
# used for fitting (tests/fixtures/measure_token_estimator.py). A group at
# or above 0.25 (25%) does not get a trustworthy point estimate — FileAudit
# and the aggregate result carry a token_estimate_low/high range instead,
# and token_estimate_confident is False, so callers can decide how to
# display it rather than silently trusting a number known to be unreliable.
# Extensions with no group mapping at all (never measured, not even on the
# fitting corpus) get the same conservative treatment as "structured" —
# absence of evidence isn't evidence of accuracy.
TOKEN_ERROR_BOUND = {
    "prose": 0.15,       # held-out MAE 8.7%, rounded up for margin
    "code": 0.20,        # held-out MAE 10.8% (one 57-word file hit +35%; small files are noisier)
    "structured": 0.60,  # held-out MAE 57.1% — CSV/JSONL error stays large even after the unit fix
}
DEFAULT_TOKEN_ERROR_BOUND = 0.60


class CorpusAuditor:
    """Audits a directory of text files for Forge training readiness."""

    def __init__(
        self,
        corpus_path: str,
        sample_size: int = 500,
        pii_scan_limit: int = 0,
        ip_mode: str = "public",
        context_denylist: frozenset | None = None,
    ):
        self.corpus_path = Path(corpus_path)
        self.sample_size = sample_size
        self.pii_scan_limit = pii_scan_limit
        self.ip_mode = ip_mode
        self.context_denylist = context_denylist
        self._hashes: dict[str, str] = {}

    def audit(self) -> CorpusAuditResult:
        result = CorpusAuditResult(
            timestamp=datetime.now(timezone.utc).isoformat(),
            corpus_path=str(self.corpus_path),
        )

        if not self.corpus_path.exists():
            result.recommendations.append(f"Path does not exist: {self.corpus_path}")
            return result

        files = self._discover_files()
        result.total_files = len(files)

        ext_counter = Counter()
        lang_counter = Counter()
        quality_counter = Counter()

        for fpath in files:
            audit = self._audit_file(fpath)
            result.file_audits.append(audit)
            result.total_size_bytes += audit.size_bytes
            result.total_tokens_estimate += audit.token_estimate
            result.total_tokens_estimate_low += audit.token_estimate_low
            result.total_tokens_estimate_high += audit.token_estimate_high
            if not audit.token_estimate_confident and audit.token_estimate > 0:
                result.tokens_estimate_uncertain = True
            ext_counter[audit.extension] += 1

            if audit.language:
                lang_counter[audit.language] += 1
            if audit.is_duplicate:
                result.duplicate_count += 1
                result.duplicate_bytes += audit.size_bytes
            if audit.pii_detected:
                result.pii_files_count += 1
            for flag in audit.quality_flags:
                quality_counter[flag] += 1

        result.file_type_distribution = dict(ext_counter)
        result.language_distribution = dict(lang_counter)
        result.quality_issues = dict(quality_counter)
        result.recommendations = self._generate_recommendations(result)

        return result

    def _discover_files(self) -> list[Path]:
        files = []
        for root, _dirs, filenames in os.walk(self.corpus_path):
            for fname in filenames:
                fpath = Path(root) / fname
                if fpath.suffix.lower() in SUPPORTED_EXTENSIONS:
                    files.append(fpath)
        return sorted(files)

    def _audit_file(self, fpath: Path) -> FileAudit:
        stat = fpath.stat()
        audit = FileAudit(
            path=str(fpath.relative_to(self.corpus_path)),
            size_bytes=stat.st_size,
            extension=fpath.suffix.lower(),
        )

        try:
            text = fpath.read_text(encoding="utf-8", errors="replace")
            audit.encoding = "utf-8"
        except Exception:
            audit.quality_flags.append("unreadable")
            return audit

        audit.char_count = len(text)
        audit.line_count = text.count("\n") + 1
        words = text.split()
        audit.word_count = len(words)

        multiplier = TOKEN_MULTIPLIERS.get(audit.extension, DEFAULT_TOKEN_MULTIPLIER)
        audit.token_estimate = int(audit.char_count / multiplier)
        group = EXTENSION_TOKEN_GROUP.get(audit.extension)
        error_bound = TOKEN_ERROR_BOUND.get(group, DEFAULT_TOKEN_ERROR_BOUND)
        audit.token_estimate_confident = error_bound < 0.25
        audit.token_estimate_low = int(audit.token_estimate * (1 - error_bound))
        audit.token_estimate_high = int(audit.token_estimate * (1 + error_bound))

        # --- Duplicate detection (content hash) ---
        content_hash = hashlib.sha256(text.encode(), usedforsecurity=False).hexdigest()
        if content_hash in self._hashes:
            audit.is_duplicate = True
            audit.duplicate_of = self._hashes[content_hash]
        else:
            self._hashes[content_hash] = str(fpath.relative_to(self.corpus_path))

        # --- Language detection ---
        sample_words = [w.lower().strip(".,;:!?\"'()") for w in words[:self.sample_size]]
        audit.language = self._detect_language(sample_words)

        # --- PII scan (full file, chunked so we never hold more than
        # one chunk of PII-scan text in memory at a time) ---
        pii_types, truncated = pii.scan_file_chunked(
            fpath,
            scan_limit=self.pii_scan_limit,
            ip_mode=self.ip_mode,
            context_denylist=self.context_denylist,
        )
        audit.pii_detected = pii_types
        audit.pii_scan_truncated = truncated

        # --- Quality flags ---
        if audit.char_count < 100:
            audit.quality_flags.append("too_short")
        if audit.char_count > 0:
            alpha_ratio = sum(c.isalpha() for c in text[:10_000]) / min(len(text), 10_000)
            if alpha_ratio < 0.3:
                audit.quality_flags.append("low_text_density")
        if audit.line_count > 0 and audit.word_count / audit.line_count < 2:
            audit.quality_flags.append("sparse_lines")

        # Check for boilerplate / repetition
        if audit.line_count > 20:
            lines = text.strip().split("\n")
            unique_lines = set(lines)
            if len(unique_lines) / len(lines) < 0.5:
                audit.quality_flags.append("high_repetition")

        return audit

    def _detect_language(self, words: list[str]) -> str | None:
        if not words:
            return None
        scores = {}
        for lang, markers in LANG_MARKERS.items():
            marker_set = set(markers)
            score = sum(1 for w in words if w in marker_set)
            scores[lang] = score
        best = max(scores, key=scores.get)
        if scores[best] < 3:
            return "unknown"
        return best

    def _generate_recommendations(self, result: CorpusAuditResult) -> list[str]:
        recs = []

        if result.total_files == 0:
            recs.append("No supported files found. Ensure your corpus contains text files (.txt, .md, .json, .jsonl, .csv, etc.).")
            return recs

        # Duplicates
        dup_pct = (result.duplicate_count / result.total_files * 100) if result.total_files else 0
        if dup_pct > 10:
            recs.append(
                f"High duplication: {result.duplicate_count} files ({dup_pct:.1f}%) are exact duplicates. "
                f"Run `forge-prep clean --dedup` to remove {result.duplicate_bytes / 1024 / 1024:.1f} MB of redundant data."
            )

        # PII
        if result.pii_files_count > 0:
            pii_pct = result.pii_files_count / result.total_files * 100
            recs.append(
                f"PII detected in {result.pii_files_count} files ({pii_pct:.1f}%). "
                f"Run `forge-prep clean` before uploading to Forge."
            )

        truncated_files = [fa for fa in result.file_audits if fa.pii_scan_truncated]
        if truncated_files:
            recs.append(
                f"WARNING: PII scan was truncated by --pii-scan-limit on {len(truncated_files)} file(s). "
                f"These files were not fully scanned and may contain undetected PII. "
                f"Re-run with --pii-scan-limit 0 (unlimited) before trusting the Privacy score."
            )

        # Quality
        qi = result.quality_issues
        if qi.get("too_short", 0) > result.total_files * 0.2:
            recs.append("Over 20% of files are very short (<100 chars). Consider merging small files or filtering them out.")
        if qi.get("low_text_density", 0) > result.total_files * 0.1:
            recs.append("Over 10% of files have low text density. These may be data tables, logs, or binary-encoded content. Review and filter.")
        if qi.get("high_repetition", 0) > 0:
            recs.append(f"{qi['high_repetition']} files have high line repetition. This can degrade model training quality.")

        # Language diversity — excludes "unknown" so this stays consistent
        # with ReadinessScorer._score_diversity's num_langs.
        langs = result.language_distribution
        num_langs = len([lang for lang in langs if lang != "unknown"])
        if num_langs > 2:
            recs.append(
                f"Corpus contains {num_langs} detected languages. "
                f"If training a domain-specific model, consider filtering to your target language(s)."
            )

        # Size guidance
        total_mb = result.total_size_bytes / 1024 / 1024
        if total_mb < 10:
            recs.append(
                f"Corpus is only {total_mb:.1f} MB. For effective Forge pre-training, aim for 1+ GB of clean, deduplicated text."
            )
        elif total_mb < 100:
            recs.append(
                f"Corpus is {total_mb:.1f} MB — suitable for fine-tuning but may be insufficient for full pre-training. "
                f"Evaluate whether Forge SFT (supervised fine-tuning) is more cost-effective than full pre-training."
            )

        # Token estimate
        if result.total_tokens_estimate > 0:
            recs.append(
                f"Estimated {result.total_tokens_estimate:,} tokens. "
                f"Forge pre-training typically requires 10B+ tokens for domain coverage; "
                f"fine-tuning can be effective with 100K–10M tokens."
            )

        return recs

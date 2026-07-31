"""
Corpus Auditor — scans a directory of documents and produces
a structured audit of data quality, diversity, and Forge readiness.
"""

import os
import json
import hashlib
import re
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional
from collections import Counter
from datetime import datetime


@dataclass
class FileAudit:
    path: str
    size_bytes: int
    extension: str
    encoding: Optional[str] = None
    language: Optional[str] = None
    token_estimate: int = 0
    char_count: int = 0
    line_count: int = 0
    word_count: int = 0
    is_duplicate: bool = False
    duplicate_of: Optional[str] = None
    quality_flags: list = field(default_factory=list)
    pii_detected: list = field(default_factory=list)


@dataclass
class CorpusAuditResult:
    timestamp: str
    corpus_path: str
    total_files: int = 0
    total_size_bytes: int = 0
    total_tokens_estimate: int = 0
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


# --- PII patterns (lightweight, no ML dependency) ---
PII_PATTERNS = {
    "email": re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+"),
    "phone_intl": re.compile(r"\+\d{1,3}[\s.-]\d[\s.\d-]{6,15}\d"),
    "ip_address": re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"),
    "credit_card": re.compile(r"\b(?:\d{4}[-\s]?){3}\d{4}\b"),
    "ssn_us": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "iban": re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{4}\d{7}([A-Z0-9]?){0,16}\b"),
    "french_nir": re.compile(r"\b[12]\s?\d{2}\s?\d{2}\s?\d{2}\s?\d{3}\s?\d{3}\s?\d{2}\b"),
}

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


class CorpusAuditor:
    """Audits a directory of text files for Forge training readiness."""

    def __init__(self, corpus_path: str, sample_size: int = 500):
        self.corpus_path = Path(corpus_path)
        self.sample_size = sample_size
        self._hashes: dict[str, str] = {}

    def audit(self) -> CorpusAuditResult:
        result = CorpusAuditResult(
            timestamp=datetime.utcnow().isoformat() + "Z",
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
        # Rough token estimate: ~0.75 tokens per word for English, ~1.1 for French
        audit.token_estimate = int(len(words) * 0.85)

        # --- Duplicate detection (content hash) ---
        content_hash = hashlib.md5(text.encode()).hexdigest()
        if content_hash in self._hashes:
            audit.is_duplicate = True
            audit.duplicate_of = self._hashes[content_hash]
        else:
            self._hashes[content_hash] = str(fpath.relative_to(self.corpus_path))

        # --- Language detection ---
        sample_words = [w.lower().strip(".,;:!?\"'()") for w in words[:self.sample_size]]
        audit.language = self._detect_language(sample_words)

        # --- PII scan ---
        sample_text = text[:50_000]  # scan first 50k chars
        for pii_type, pattern in PII_PATTERNS.items():
            if pattern.search(sample_text):
                audit.pii_detected.append(pii_type)

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

    def _detect_language(self, words: list[str]) -> Optional[str]:
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
                f"Run `forge-prep clean --scrub-pii` before uploading to Forge."
            )

        # Quality
        qi = result.quality_issues
        if qi.get("too_short", 0) > result.total_files * 0.2:
            recs.append("Over 20% of files are very short (<100 chars). Consider merging small files or filtering them out.")
        if qi.get("low_text_density", 0) > result.total_files * 0.1:
            recs.append("Over 10% of files have low text density. These may be data tables, logs, or binary-encoded content. Review and filter.")
        if qi.get("high_repetition", 0) > 0:
            recs.append(f"{qi['high_repetition']} files have high line repetition. This can degrade model training quality.")

        # Language diversity
        langs = result.language_distribution
        if len(langs) > 2:
            recs.append(
                f"Corpus contains {len(langs)} detected languages. "
                f"If training a domain-specific model, consider filtering to your target language(s)."
            )

        # Size guidance
        total_mb = result.total_size_bytes / 1024 / 1024
        if total_mb < 10:
            recs.append(
                f"Corpus is only {total_mb:.1f} MB. For effective Forge pre-training, aim for 1+ GB of clean, deduplicated text. "
                f"Consider synthetic data augmentation with `forge-prep synth`."
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

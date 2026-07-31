"""
Readiness Scorer — computes a 0–100 "Forge Readiness Score"
based on corpus audit results, with per-dimension breakdown.
"""

from dataclasses import dataclass, field, asdict
from forge_prep.auditor import CorpusAuditResult


@dataclass
class DimensionScore:
    name: str
    score: float  # 0–100
    weight: float
    details: str


@dataclass
class ReadinessScore:
    overall: float  # 0–100
    grade: str  # A, B, C, D, F
    dimensions: list = field(default_factory=list)
    forge_recommendation: str = ""

    def to_dict(self):
        return asdict(self)


GRADE_THRESHOLDS = [
    (90, "A", "Excellent — your corpus is Forge-ready for pre-training or fine-tuning."),
    (75, "B", "Good — minor cleanup recommended before Forge training."),
    (60, "C", "Fair — significant data quality work needed before Forge will be effective."),
    (40, "D", "Poor — major data preparation required. Start with cleaning and augmentation."),
    (0, "F", "Not ready — corpus needs fundamental restructuring before any model training."),
]


class ReadinessScorer:
    """Scores a corpus audit result on multiple quality dimensions."""

    def score(self, audit: CorpusAuditResult) -> ReadinessScore:
        dimensions = [
            self._score_volume(audit),
            self._score_quality(audit),
            self._score_deduplication(audit),
            self._score_privacy(audit),
            self._score_diversity(audit),
            self._score_format(audit),
        ]

        total_weight = sum(d.weight for d in dimensions)
        weighted_sum = sum(d.score * d.weight for d in dimensions)
        overall = weighted_sum / total_weight if total_weight > 0 else 0

        grade = "F"
        recommendation = ""
        for threshold, g, rec in GRADE_THRESHOLDS:
            if overall >= threshold:
                grade = g
                recommendation = rec
                break

        return ReadinessScore(
            overall=round(overall, 1),
            grade=grade,
            dimensions=dimensions,
            forge_recommendation=recommendation,
        )

    def _score_volume(self, audit: CorpusAuditResult) -> DimensionScore:
        """Score based on corpus size and token count."""
        tokens = audit.total_tokens_estimate
        mb = audit.total_size_bytes / 1024 / 1024

        if tokens >= 10_000_000:
            score = 100
            detail = f"{tokens:,} tokens ({mb:.0f} MB) — excellent volume for Forge training."
        elif tokens >= 1_000_000:
            score = 80
            detail = f"{tokens:,} tokens ({mb:.0f} MB) — good for fine-tuning, consider augmentation for pre-training."
        elif tokens >= 100_000:
            score = 55
            detail = f"{tokens:,} tokens ({mb:.0f} MB) — sufficient for supervised fine-tuning only."
        elif tokens >= 10_000:
            score = 30
            detail = f"{tokens:,} tokens ({mb:.0f} MB) — very limited. Augmentation strongly recommended."
        else:
            score = 10
            detail = f"{tokens:,} tokens ({mb:.0f} MB) — insufficient for any Forge training stage."

        return DimensionScore(name="Volume", score=score, weight=0.25, details=detail)

    def _score_quality(self, audit: CorpusAuditResult) -> DimensionScore:
        """Score based on quality flags prevalence."""
        if audit.total_files == 0:
            return DimensionScore(name="Quality", score=0, weight=0.25, details="No files to assess.")

        issues = audit.quality_issues
        total_flags = sum(issues.values())
        flag_ratio = total_flags / audit.total_files

        if flag_ratio < 0.05:
            score = 95
            detail = "Minimal quality issues detected."
        elif flag_ratio < 0.15:
            score = 75
            detail = f"{total_flags} quality flags across {audit.total_files} files — some cleanup needed."
        elif flag_ratio < 0.35:
            score = 50
            detail = f"Significant quality issues: {dict(issues)}"
        else:
            score = 20
            detail = f"Pervasive quality issues ({flag_ratio:.0%} flag rate). Major filtering needed."

        return DimensionScore(name="Quality", score=score, weight=0.25, details=detail)

    def _score_deduplication(self, audit: CorpusAuditResult) -> DimensionScore:
        """Score based on duplicate content ratio."""
        if audit.total_files == 0:
            return DimensionScore(name="Deduplication", score=0, weight=0.15, details="No files.")

        dup_ratio = audit.duplicate_count / audit.total_files
        if dup_ratio < 0.02:
            score = 100
            detail = "Virtually no duplicates detected."
        elif dup_ratio < 0.1:
            score = 75
            detail = f"{audit.duplicate_count} duplicates ({dup_ratio:.1%}) — minor dedup needed."
        elif dup_ratio < 0.3:
            score = 45
            detail = f"{audit.duplicate_count} duplicates ({dup_ratio:.1%}) — significant dedup required."
        else:
            score = 15
            detail = f"{audit.duplicate_count} duplicates ({dup_ratio:.1%}) — severe duplication problem."

        return DimensionScore(name="Deduplication", score=score, weight=0.15, details=detail)

    def _score_privacy(self, audit: CorpusAuditResult) -> DimensionScore:
        """Score based on PII detection."""
        if audit.total_files == 0:
            return DimensionScore(name="Privacy", score=0, weight=0.2, details="No files.")

        pii_ratio = audit.pii_files_count / audit.total_files
        if pii_ratio == 0:
            score = 100
            detail = "No PII detected — ready for Forge upload."
        elif pii_ratio < 0.05:
            score = 70
            detail = f"PII found in {audit.pii_files_count} files ({pii_ratio:.1%}). Scrubbing recommended."
        elif pii_ratio < 0.2:
            score = 40
            detail = f"PII in {audit.pii_files_count} files ({pii_ratio:.1%}). Scrubbing required before Forge."
        else:
            score = 10
            detail = f"Widespread PII ({audit.pii_files_count} files, {pii_ratio:.1%}). Critical: scrub before any training."

        return DimensionScore(name="Privacy", score=score, weight=0.2, details=detail)

    def _score_diversity(self, audit: CorpusAuditResult) -> DimensionScore:
        """Score based on language and format diversity (or focus)."""
        langs = audit.language_distribution
        num_langs = len([l for l in langs if l != "unknown"])

        if num_langs == 1:
            score = 90
            detail = f"Single-language corpus ({list(langs.keys())[0]}) — focused and consistent."
        elif num_langs == 2:
            score = 80
            detail = f"Bilingual corpus — consider if both languages are needed for your domain."
        elif num_langs <= 4:
            score = 60
            detail = f"{num_langs} languages detected. Filter to target languages unless multilingual is intentional."
        else:
            score = 40
            detail = f"{num_langs} languages detected — high diversity may dilute domain focus. Filter recommended."

        return DimensionScore(name="Language Focus", score=score, weight=0.1, details=detail)

    def _score_format(self, audit: CorpusAuditResult) -> DimensionScore:
        """Score based on file format consistency."""
        formats = audit.file_type_distribution
        num_formats = len(formats)

        if num_formats <= 2:
            score = 90
            detail = "Consistent file formats — easy to process."
        elif num_formats <= 5:
            score = 70
            detail = f"{num_formats} file formats. Consider standardizing to .txt or .jsonl for Forge."
        else:
            score = 45
            detail = f"{num_formats} different formats. Standardization strongly recommended."

        return DimensionScore(name="Format Consistency", score=score, weight=0.05, details=detail)

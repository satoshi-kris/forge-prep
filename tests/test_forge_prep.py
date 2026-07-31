"""
Unit tests for forge-prep
Run with: python -m pytest tests/ -v
Or without pytest: python -m unittest tests/test_forge_prep.py -v
"""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from forge_prep.auditor import CorpusAuditor, CorpusAuditResult
from forge_prep.cleaner import CorpusCleaner
from forge_prep.report import ReadinessReport
from forge_prep.scorer import ReadinessScorer


class TestCorpusAuditor(unittest.TestCase):
    """Tests for the corpus auditing pipeline."""

    def setUp(self):
        """Create a temporary corpus directory for each test."""
        self.test_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def _write_file(self, relative_path: str, content: str):
        fpath = Path(self.test_dir) / relative_path
        fpath.parent.mkdir(parents=True, exist_ok=True)
        fpath.write_text(content, encoding="utf-8")
        return fpath

    # --- Basic auditing ---

    def test_audit_empty_directory(self):
        auditor = CorpusAuditor(self.test_dir)
        result = auditor.audit()
        self.assertEqual(result.total_files, 0)
        self.assertEqual(result.total_tokens_estimate, 0)
        self.assertIsInstance(result.recommendations, list)

    def test_audit_nonexistent_path(self):
        auditor = CorpusAuditor("/nonexistent/path/12345")
        result = auditor.audit()
        self.assertEqual(result.total_files, 0)
        self.assertTrue(len(result.recommendations) > 0)

    def test_audit_single_file(self):
        self._write_file("doc.txt", "This is a simple English document with enough words to pass the minimum character threshold for quality checks.")
        auditor = CorpusAuditor(self.test_dir)
        result = auditor.audit()
        self.assertEqual(result.total_files, 1)
        self.assertGreater(result.total_tokens_estimate, 0)
        self.assertEqual(result.duplicate_count, 0)

    def test_audit_counts_tokens(self):
        # .txt uses the prose multiplier (1.54); word count here comfortably clears 10 either way
        self._write_file("doc.txt", " ".join(["word"] * 20) + " extra padding to reach minimum character count for the quality threshold check")
        auditor = CorpusAuditor(self.test_dir)
        result = auditor.audit()
        self.assertGreater(result.total_tokens_estimate, 10)

    # --- Duplicate detection ---

    def test_detects_exact_duplicates(self):
        content = "This is a document with enough content to be considered valid by the auditor quality checks and filters."
        self._write_file("original.txt", content)
        self._write_file("copy.txt", content)
        auditor = CorpusAuditor(self.test_dir)
        result = auditor.audit()
        self.assertEqual(result.duplicate_count, 1)
        self.assertEqual(result.total_files, 2)

    def test_different_files_not_flagged_as_duplicates(self):
        self._write_file("a.txt", "First document about machine learning and neural networks in production environments for enterprise.")
        self._write_file("b.txt", "Second document about data engineering pipelines and ETL processes in modern cloud architectures.")
        auditor = CorpusAuditor(self.test_dir)
        result = auditor.audit()
        self.assertEqual(result.duplicate_count, 0)

    # --- PII detection ---

    def test_detects_email_pii(self):
        self._write_file("doc.txt", "Contact us at john.doe@example.com for more information about our products and services offered globally.")
        auditor = CorpusAuditor(self.test_dir)
        result = auditor.audit()
        self.assertEqual(result.pii_files_count, 1)
        file_audit = result.file_audits[0]
        self.assertIn("email", file_audit.pii_detected)

    def test_detects_phone_pii(self):
        self._write_file("doc.txt", "Call our Paris office at +33 1 42 68 53 00 for support. We are available Monday through Friday during business hours.")
        auditor = CorpusAuditor(self.test_dir)
        result = auditor.audit()
        file_audit = result.file_audits[0]
        self.assertIn("phone_intl", file_audit.pii_detected)

    def test_detects_public_ip_address(self):
        # Public IPs are PII by default; RFC1918/loopback ranges are not
        # (see test_ignores_private_ip_by_default) since ip_mode defaults to "public".
        self._write_file("doc.txt", "SSH into the gateway at 8.8.8.8 to access the external network and run diagnostic commands on the server cluster.")
        auditor = CorpusAuditor(self.test_dir)
        result = auditor.audit()
        file_audit = result.file_audits[0]
        self.assertIn("ip_address", file_audit.pii_detected)

    def test_ignores_private_ip_by_default(self):
        self._write_file("doc.txt", "SSH into the gateway at 192.168.1.100 to access the internal network and run diagnostic commands on the server cluster.")
        auditor = CorpusAuditor(self.test_dir)
        result = auditor.audit()
        file_audit = result.file_audits[0]
        self.assertNotIn("ip_address", file_audit.pii_detected)

    def test_detects_private_ip_with_all_mode(self):
        self._write_file("doc.txt", "SSH into the gateway at 192.168.1.100 to access the internal network and run diagnostic commands on the server cluster.")
        auditor = CorpusAuditor(self.test_dir, ip_mode="all")
        result = auditor.audit()
        file_audit = result.file_audits[0]
        self.assertIn("ip_address", file_audit.pii_detected)

    def test_detects_iban(self):
        self._write_file("doc.txt", "Please wire the payment to our account FR7630006000011234567890189 at BNP Paribas for the invoice amount due this month.")
        auditor = CorpusAuditor(self.test_dir)
        result = auditor.audit()
        file_audit = result.file_audits[0]
        self.assertIn("iban", file_audit.pii_detected)

    def test_no_pii_in_clean_file(self):
        self._write_file("doc.txt", "The machine learning model achieved 94 percent accuracy on the validation set using cross-fold evaluation methodology.")
        auditor = CorpusAuditor(self.test_dir)
        result = auditor.audit()
        self.assertEqual(result.pii_files_count, 0)

    # --- Quality flags ---

    def test_flags_short_files(self):
        self._write_file("tiny.txt", "TODO: write this")
        auditor = CorpusAuditor(self.test_dir)
        result = auditor.audit()
        file_audit = result.file_audits[0]
        self.assertIn("too_short", file_audit.quality_flags)

    def test_flags_repetitive_content(self):
        content = "\n".join(["Log entry: system check passed OK"] * 100)
        self._write_file("log.txt", content)
        auditor = CorpusAuditor(self.test_dir)
        result = auditor.audit()
        file_audit = result.file_audits[0]
        self.assertIn("high_repetition", file_audit.quality_flags)

    def test_no_flags_on_good_content(self):
        content = """
        Machine learning has transformed how enterprises approach data analysis.
        Modern pipelines combine feature engineering with automated model selection.
        The key challenge remains data quality and governance at scale.
        Organizations must invest in proper data infrastructure before adopting AI.
        This includes data catalogs, lineage tracking, and quality monitoring.
        """
        self._write_file("good.txt", content)
        auditor = CorpusAuditor(self.test_dir)
        result = auditor.audit()
        file_audit = result.file_audits[0]
        self.assertEqual(file_audit.quality_flags, [])

    # --- Language detection ---

    def test_detects_english(self):
        self._write_file("en.txt", "The company has been working on this project for several months and they have made significant progress with their new approach.")
        auditor = CorpusAuditor(self.test_dir)
        result = auditor.audit()
        self.assertIn("en", result.language_distribution)

    def test_detects_french(self):
        self._write_file("fr.txt", "Le tableau de bord principal affiche les metriques de vos installations avec le taux de disponibilite et les alertes dans cette vue.")
        auditor = CorpusAuditor(self.test_dir)
        result = auditor.audit()
        self.assertIn("fr", result.language_distribution)

    # --- File type filtering ---

    def test_ignores_unsupported_extensions(self):
        self._write_file("image.png", "not a real image")
        self._write_file("doc.txt", "This is a valid text file with enough content to pass quality checks in the auditor pipeline.")
        auditor = CorpusAuditor(self.test_dir)
        result = auditor.audit()
        self.assertEqual(result.total_files, 1)

    def test_supports_multiple_extensions(self):
        self._write_file("data.csv", "col1,col2\nval1,val2\nval3,val4\nval5,val6\nval7,val8\n" * 20)
        self._write_file("data.jsonl", '{"key":"value"}\n' * 20)
        self._write_file("script.py", "# Python script\n" + "x = 1\n" * 30)
        auditor = CorpusAuditor(self.test_dir)
        result = auditor.audit()
        self.assertEqual(result.total_files, 3)
        self.assertIn(".csv", result.file_type_distribution)
        self.assertIn(".jsonl", result.file_type_distribution)
        self.assertIn(".py", result.file_type_distribution)

    # --- Serialization ---

    def test_audit_result_to_json(self):
        self._write_file("doc.txt", "A valid document with sufficient content for testing the JSON serialization of audit results correctly.")
        auditor = CorpusAuditor(self.test_dir)
        result = auditor.audit()
        json_path = os.path.join(self.test_dir, "result.json")
        result.to_json(json_path)
        with open(json_path) as f:
            data = json.load(f)
        self.assertEqual(data["total_files"], 1)
        self.assertIn("file_audits", data)


class TestCorpusCleaner(unittest.TestCase):
    """Tests for the corpus cleaning pipeline."""

    def setUp(self):
        self.input_dir = tempfile.mkdtemp()
        self.output_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.input_dir)
        shutil.rmtree(self.output_dir)

    def _write_file(self, relative_path: str, content: str):
        fpath = Path(self.input_dir) / relative_path
        fpath.parent.mkdir(parents=True, exist_ok=True)
        fpath.write_text(content, encoding="utf-8")

    def test_clean_removes_duplicates(self):
        content = "This is a valid document with enough content for the cleaner to process it properly through all stages."
        self._write_file("original.txt", content)
        self._write_file("copy.txt", content)
        cleaner = CorpusCleaner(self.input_dir, self.output_dir, dedup=True, scrub_pii=False)
        result = cleaner.clean()
        self.assertEqual(result.duplicates_removed, 1)
        self.assertEqual(result.files_kept, 1)

    def test_clean_scrubs_email_pii(self):
        self._write_file("doc.txt", "Contact john.doe@example.com for details about the project and its current development status within the team.")
        cleaner = CorpusCleaner(self.input_dir, self.output_dir, dedup=False, scrub_pii=True)
        result = cleaner.clean()
        self.assertEqual(result.pii_scrubbed_files, 1)
        self.assertGreater(result.pii_replacements, 0)
        # Check the output file has redacted PII
        out_text = (Path(self.output_dir) / "doc.txt").read_text()
        self.assertIn("[EMAIL_REDACTED]", out_text)
        self.assertNotIn("john.doe@example.com", out_text)

    def test_clean_scrubs_phone_pii(self):
        self._write_file("doc.txt", "Call us at +33 1 42 68 53 00 for more information about our services and available support packages today.")
        cleaner = CorpusCleaner(self.input_dir, self.output_dir, dedup=False, scrub_pii=True)
        cleaner.clean()
        out_text = (Path(self.output_dir) / "doc.txt").read_text()
        self.assertIn("[PHONE_REDACTED]", out_text)

    def test_clean_filters_short_files(self):
        self._write_file("tiny.txt", "TODO")
        self._write_file("good.txt", "This document has enough content to pass the minimum character threshold that the cleaner applies during processing.")
        cleaner = CorpusCleaner(self.input_dir, self.output_dir, min_chars=100)
        result = cleaner.clean()
        self.assertEqual(result.files_kept, 1)
        self.assertEqual(result.files_removed, 1)
        self.assertFalse((Path(self.output_dir) / "tiny.txt").exists())
        self.assertTrue((Path(self.output_dir) / "good.txt").exists())

    def test_clean_filters_repetitive_files(self):
        self._write_file("repetitive.txt", "\n".join(["same line"] * 100))
        cleaner = CorpusCleaner(self.input_dir, self.output_dir)
        result = cleaner.clean()
        self.assertEqual(result.files_kept, 0)

    def test_clean_preserves_directory_structure(self):
        self._write_file("docs/guide.txt", "A complete guide with sufficient content for the cleaner to keep it in the output directory structure.")
        self._write_file("data/export.txt", "This is a data export file with enough words per line to pass the sparse lines check in the quality filter pipeline.")
        cleaner = CorpusCleaner(self.input_dir, self.output_dir, dedup=False, scrub_pii=False)
        cleaner.clean()
        self.assertTrue((Path(self.output_dir) / "docs" / "guide.txt").exists())
        self.assertTrue((Path(self.output_dir) / "data" / "export.txt").exists())

    def test_clean_with_all_options_disabled(self):
        content = "Contact support@test.com for help with your account setup and configuration across all environments."
        self._write_file("a.txt", content)
        self._write_file("b.txt", content)
        cleaner = CorpusCleaner(self.input_dir, self.output_dir, dedup=False, scrub_pii=False, min_chars=0)
        result = cleaner.clean()
        self.assertEqual(result.files_kept, 2)  # no dedup
        out_text = (Path(self.output_dir) / "a.txt").read_text()
        self.assertIn("support@test.com", out_text)  # no PII scrub

    def test_reduction_percentage(self):
        self._write_file("big.txt", "Valid content here. " * 100)
        self._write_file("tiny.txt", "x")
        cleaner = CorpusCleaner(self.input_dir, self.output_dir)
        result = cleaner.clean()
        self.assertGreater(result.reduction_pct, 0)


class TestReadinessScorer(unittest.TestCase):
    """Tests for the scoring engine."""

    def _make_audit(self, **overrides):
        defaults = dict(
            timestamp="2026-01-01T00:00:00Z",
            corpus_path="/test",
            total_files=100,
            total_size_bytes=50_000_000,
            total_tokens_estimate=5_000_000,
            file_type_distribution={".txt": 80, ".jsonl": 20},
            language_distribution={"en": 100},
            duplicate_count=2,
            duplicate_bytes=10000,
            pii_files_count=3,
            quality_issues={"too_short": 2},
            file_audits=[],
            recommendations=[],
        )
        defaults.update(overrides)
        return CorpusAuditResult(**defaults)

    def test_high_quality_corpus_scores_well(self):
        audit = self._make_audit(
            total_tokens_estimate=50_000_000,
            duplicate_count=0,
            pii_files_count=0,
            quality_issues={},
        )
        scorer = ReadinessScorer()
        score = scorer.score(audit)
        self.assertGreater(score.overall, 80)
        self.assertIn(score.grade, ["A", "B"])

    def test_empty_corpus_scores_zero(self):
        audit = self._make_audit(
            total_files=0,
            total_size_bytes=0,
            total_tokens_estimate=0,
            file_type_distribution={},
            language_distribution={},
            duplicate_count=0,
            pii_files_count=0,
            quality_issues={},
        )
        scorer = ReadinessScorer()
        score = scorer.score(audit)
        self.assertLess(score.overall, 15)
        self.assertEqual(score.grade, "F")

    def test_high_pii_lowers_score(self):
        clean = self._make_audit(pii_files_count=0)
        dirty = self._make_audit(pii_files_count=80)
        scorer = ReadinessScorer()
        clean_score = scorer.score(clean)
        dirty_score = scorer.score(dirty)
        self.assertGreater(clean_score.overall, dirty_score.overall)

    def test_high_duplication_lowers_score(self):
        clean = self._make_audit(duplicate_count=0)
        duped = self._make_audit(duplicate_count=50)
        scorer = ReadinessScorer()
        self.assertGreater(scorer.score(clean).overall, scorer.score(duped).overall)

    def test_score_has_all_dimensions(self):
        audit = self._make_audit()
        scorer = ReadinessScorer()
        score = scorer.score(audit)
        dimension_names = {d.name for d in score.dimensions}
        self.assertIn("Volume", dimension_names)
        self.assertIn("Quality", dimension_names)
        self.assertIn("Deduplication", dimension_names)
        self.assertIn("Privacy", dimension_names)
        self.assertIn("Language Focus", dimension_names)
        self.assertIn("Format Consistency", dimension_names)

    def test_grade_assignment(self):
        scorer = ReadinessScorer()
        # Very high quality should get A
        excellent = self._make_audit(
            total_tokens_estimate=100_000_000,
            duplicate_count=0, pii_files_count=0, quality_issues={},
        )
        self.assertEqual(scorer.score(excellent).grade, "A")

    def test_score_is_bounded_0_100(self):
        audit = self._make_audit()
        scorer = ReadinessScorer()
        score = scorer.score(audit)
        self.assertGreaterEqual(score.overall, 0)
        self.assertLessEqual(score.overall, 100)
        for d in score.dimensions:
            self.assertGreaterEqual(d.score, 0)
            self.assertLessEqual(d.score, 100)


class TestReadinessReport(unittest.TestCase):
    """Tests for report generation."""

    def test_markdown_report_contains_key_sections(self):
        audit = CorpusAuditResult(
            timestamp="2026-04-01T00:00:00Z", corpus_path="/test",
            total_files=10, total_size_bytes=1024000,
            total_tokens_estimate=50000,
            file_type_distribution={".txt": 10},
            language_distribution={"en": 10},
            duplicate_count=1, duplicate_bytes=1024,
            pii_files_count=2, quality_issues={"too_short": 1},
            file_audits=[], recommendations=["Fix PII issues."],
        )
        scorer = ReadinessScorer()
        score = scorer.score(audit)
        report = ReadinessReport(audit, score)
        md = report.to_markdown()
        self.assertIn("Forge Readiness Report", md)
        self.assertIn("Corpus Overview", md)
        self.assertIn("Dimension Scores", md)
        self.assertIn("Recommendations", md)
        self.assertIn("forge-prep", md)

    def test_json_report_is_valid(self):
        audit = CorpusAuditResult(
            timestamp="2026-04-01T00:00:00Z", corpus_path="/test",
            total_files=5, total_size_bytes=5000,
            total_tokens_estimate=500,
            file_type_distribution={".txt": 5},
            language_distribution={"en": 5},
            duplicate_count=0, duplicate_bytes=0,
            pii_files_count=0, quality_issues={},
            file_audits=[], recommendations=[],
        )
        scorer = ReadinessScorer()
        score = scorer.score(audit)
        report = ReadinessReport(audit, score)
        data = report.to_json()
        self.assertIn("audit", data)
        self.assertIn("score", data)
        # Should be JSON serializable
        json_str = json.dumps(data)
        self.assertIsInstance(json_str, str)

    def test_save_creates_files(self):
        audit = CorpusAuditResult(
            timestamp="2026-04-01T00:00:00Z", corpus_path="/test",
            total_files=1, total_size_bytes=100,
            total_tokens_estimate=10,
            file_type_distribution={".txt": 1},
            language_distribution={"en": 1},
            duplicate_count=0, duplicate_bytes=0,
            pii_files_count=0, quality_issues={},
            file_audits=[], recommendations=[],
        )
        scorer = ReadinessScorer()
        score = scorer.score(audit)
        report = ReadinessReport(audit, score)
        output_dir = tempfile.mkdtemp()
        try:
            md_path, json_path = report.save(output_dir)
            self.assertTrue(md_path.exists())
            self.assertTrue(json_path.exists())
            # Verify JSON is parseable
            with open(json_path) as f:
                data = json.load(f)
            self.assertIn("audit", data)
        finally:
            shutil.rmtree(output_dir)


class TestEndToEnd(unittest.TestCase):
    """End-to-end integration tests."""

    def test_full_audit_then_clean_pipeline(self):
        """Simulate a real workflow: audit → clean → re-audit."""
        input_dir = tempfile.mkdtemp()
        clean_dir = tempfile.mkdtemp()
        try:
            # Create a messy corpus
            Path(input_dir, "good.txt").write_text(
                "This is a high quality document about data engineering best practices for enterprise AI model training pipelines."
            )
            Path(input_dir, "with_pii.txt").write_text(
                "Please contact john@example.com or call +33 1 23 45 67 89 for assistance with your account setup and billing."
            )
            Path(input_dir, "duplicate.txt").write_text(
                "This is a high quality document about data engineering best practices for enterprise AI model training pipelines."
            )
            Path(input_dir, "tiny.txt").write_text("x")

            # Step 1: Audit
            auditor = CorpusAuditor(input_dir)
            audit1 = auditor.audit()
            self.assertEqual(audit1.total_files, 4)
            self.assertEqual(audit1.duplicate_count, 1)
            self.assertGreater(audit1.pii_files_count, 0)

            # Step 2: Score
            scorer = ReadinessScorer()
            score1 = scorer.score(audit1)
            self.assertIsNotNone(score1.grade)

            # Step 3: Clean
            cleaner = CorpusCleaner(input_dir, clean_dir)
            clean_result = cleaner.clean()
            self.assertGreater(clean_result.files_removed, 0)
            self.assertGreater(clean_result.pii_replacements, 0)

            # Step 4: Re-audit the clean corpus
            auditor2 = CorpusAuditor(clean_dir)
            audit2 = auditor2.audit()
            self.assertEqual(audit2.duplicate_count, 0)
            self.assertEqual(audit2.pii_files_count, 0)

            # Score should improve
            score2 = scorer.score(audit2)
            self.assertGreaterEqual(score2.overall, score1.overall)

        finally:
            shutil.rmtree(input_dir)
            shutil.rmtree(clean_dir)


if __name__ == "__main__":
    unittest.main()

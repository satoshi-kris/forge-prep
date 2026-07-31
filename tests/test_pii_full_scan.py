"""
Regression tests for the full-file, chunked PII scan (forge_prep/pii.py).

Covers the bug the external audit found: the auditor used to scan only
text[:50_000] while the cleaner scanned the whole file, so a 336KB file
with an email past the 50k mark audited as pii_detected: [] while the
cleaner found and redacted it. Also covers the chunk-boundary overlap
logic and the --pii-scan-limit truncation flag.
"""

import shutil
import tempfile
import unittest
from pathlib import Path

from forge_prep import pii
from forge_prep.auditor import CorpusAuditor


class TestFullFileScan(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def _write_file(self, relative_path: str, content: str) -> Path:
        fpath = Path(self.test_dir) / relative_path
        fpath.parent.mkdir(parents=True, exist_ok=True)
        fpath.write_text(content, encoding="utf-8")
        return fpath

    def test_email_past_50k_chars_is_detected(self):
        # Reproduces the audit's exact scenario: a ~336KB file with the
        # only email far past the old 50k-char scan window.
        filler = "The quarterly report contains routine operational text. " * 6000  # ~336,000 chars
        self.assertGreaterEqual(len(filler), 336_000)
        content = filler + "Final contact for follow-up: audit.regression@example.com\n"
        fpath = self._write_file("big_report.txt", content)

        pii_types, truncated = pii.scan_file_chunked(fpath)
        self.assertIn("email", pii_types)
        self.assertFalse(truncated)

    def test_auditor_detects_pii_past_50k_via_full_pipeline(self):
        filler = "Standard boilerplate line without any sensitive content. " * 6000  # ~360,000 chars
        content = filler + "Escalate to compliance.officer@example.com if needed.\n"
        self._write_file("late_pii.txt", content)

        auditor = CorpusAuditor(self.test_dir)
        result = auditor.audit()
        file_audit = result.file_audits[0]
        self.assertIn("email", file_audit.pii_detected)
        self.assertGreater(result.pii_files_count, 0)

    def test_match_spanning_chunk_boundary_is_not_missed(self):
        # Force a tiny chunk size so the email address straddles a
        # chunk/overlap boundary, and confirm the overlap logic still
        # catches it.
        prefix = "x" * 950
        email = "boundary.case@example.com"
        suffix = "y" * 950
        content = prefix + email + suffix
        fpath = Path(self.test_dir) / "boundary.txt"
        fpath.write_text(content, encoding="utf-8")

        pii_types, truncated = pii.scan_file_chunked(fpath, chunk_size=100, overlap=50)
        self.assertIn("email", pii_types)
        self.assertFalse(truncated)

    def test_scan_limit_truncates_and_flags(self):
        filler = "Nothing sensitive appears in this padding text. " * 3000  # ~150,000 chars
        content = filler + "hidden.email@example.com\n"
        fpath = self._write_file("truncated.txt", content)

        pii_types, truncated = pii.scan_file_chunked(fpath, scan_limit=1000)
        self.assertTrue(truncated)
        self.assertNotIn("email", pii_types)  # the email is past the scan limit

    def test_scan_limit_not_triggered_when_file_fits(self):
        content = "Short file with an email test.case@example.com inside.\n"
        fpath = self._write_file("small.txt", content)

        pii_types, truncated = pii.scan_file_chunked(fpath, scan_limit=10_000)
        self.assertFalse(truncated)
        self.assertIn("email", pii_types)

    def test_auditor_pii_scan_limit_flag_sets_file_audit_field(self):
        filler = "Padding text repeated many times to exceed the scan limit. " * 3000
        content = filler + "late.email@example.com\n"
        self._write_file("limited.txt", content)

        auditor = CorpusAuditor(self.test_dir, pii_scan_limit=500)
        result = auditor.audit()
        file_audit = result.file_audits[0]
        self.assertTrue(file_audit.pii_scan_truncated)
        self.assertTrue(any("truncated" in r.lower() for r in result.recommendations))

    def test_default_scan_limit_is_unlimited(self):
        auditor = CorpusAuditor(self.test_dir)
        self.assertEqual(auditor.pii_scan_limit, 0)


if __name__ == "__main__":
    unittest.main()

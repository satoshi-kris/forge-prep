"""
Tests for forge_prep.cli — subcommands, flags, exit codes, output formats,
and the honesty/robustness fixes from the Phase 1 remediation:
  - argparse replaces hand-rolled argv parsing (no more raw IndexError)
  - unknown commands exit 2
  - color is suppressed for non-TTY / NO_COLOR / --format json
  - --output can't resolve inside the input path
  - --fail-under drives CI-style exit codes
"""

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from forge_prep import cli

SAMPLE_CORPUS = Path(__file__).parent.parent / "examples" / "sample-corpus"


def run_cli(argv):
    """Run forge_prep.cli.main() in-process and capture (exit_code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    exit_code = 0
    with mock.patch.object(sys, "argv", ["forge-prep"] + argv):
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                cli.main()
            except SystemExit as e:
                exit_code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
    return exit_code, out.getvalue(), err.getvalue()


class TestVersionAndHelp(unittest.TestCase):
    def test_version_flag(self):
        code, out, _ = run_cli(["--version"])
        self.assertEqual(code, 0)
        self.assertIn("forge-prep", out)

    def test_help_flag_exit_zero(self):
        code, out, _ = run_cli(["--help"])
        self.assertEqual(code, 0)
        self.assertIn("audit", out)
        self.assertIn("clean", out)

    def test_help_does_not_advertise_dashboard(self):
        code, out, _ = run_cli(["--help"])
        self.assertNotIn("dashboard", out.lower())

    def test_no_args_prints_help_and_exits_zero(self):
        code, out, _ = run_cli([])
        self.assertEqual(code, 0)
        self.assertIn("usage", out.lower())

    def test_unknown_command_exits_two(self):
        code, out, err = run_cli(["dashboard"])
        self.assertEqual(code, 2)
        self.assertIn("invalid choice", err.lower())

    def test_bogus_unknown_command_exits_two(self):
        code, _, err = run_cli(["frobnicate"])
        self.assertEqual(code, 2)


class TestAuditCommand(unittest.TestCase):
    def setUp(self):
        self.output_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.output_dir, ignore_errors=True)

    def test_output_flag_with_no_value_exits_two_not_indexerror(self):
        # This used to crash with a raw IndexError before the argparse rewrite.
        code, out, err = run_cli(["audit", str(SAMPLE_CORPUS), "--output"])
        self.assertEqual(code, 2)
        self.assertIn("expected one argument", err.lower())

    def test_audit_nonexistent_path_exits_one(self):
        code, out, err = run_cli(["audit", "/nonexistent/path/xyz", "--output", self.output_dir])
        self.assertEqual(code, 1)

    def test_audit_text_output_writes_reports(self):
        code, out, err = run_cli(["audit", str(SAMPLE_CORPUS), "--output", self.output_dir])
        self.assertEqual(code, 0)
        self.assertIn("Forge Readiness Score", out)
        self.assertTrue((Path(self.output_dir) / "forge_readiness_report.md").exists())
        self.assertTrue((Path(self.output_dir) / "forge_readiness_report.json").exists())

    def test_audit_json_format_prints_valid_json_only(self):
        code, out, err = run_cli(["audit", str(SAMPLE_CORPUS), "--output", self.output_dir, "--format", "json"])
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertIn("audit", data)
        self.assertIn("score", data)
        self.assertNotIn("\033[", out)  # no ANSI escapes in JSON output

    def test_audit_quiet_suppresses_banner(self):
        code, out, err = run_cli(["audit", str(SAMPLE_CORPUS), "--output", self.output_dir, "--quiet"])
        self.assertEqual(code, 0)
        self.assertEqual(out, "")

    def test_fail_under_below_threshold_exits_one(self):
        code, out, err = run_cli(
            ["audit", str(SAMPLE_CORPUS), "--output", self.output_dir, "--fail-under", "99", "--quiet"]
        )
        self.assertEqual(code, 1)

    def test_fail_under_above_threshold_exits_zero(self):
        code, out, err = run_cli(
            ["audit", str(SAMPLE_CORPUS), "--output", self.output_dir, "--fail-under", "0", "--quiet"]
        )
        self.assertEqual(code, 0)

    def test_output_inside_input_is_rejected(self):
        input_dir = tempfile.mkdtemp()
        try:
            nested_output = str(Path(input_dir) / "out")
            code, out, err = run_cli(["audit", input_dir, "--output", nested_output])
            self.assertEqual(code, 2)
            self.assertIn("resolves inside", err)
            self.assertFalse(Path(nested_output).exists())
        finally:
            shutil.rmtree(input_dir, ignore_errors=True)

    def test_output_equal_to_input_is_rejected(self):
        input_dir = tempfile.mkdtemp()
        try:
            code, out, err = run_cli(["audit", input_dir, "--output", input_dir])
            self.assertEqual(code, 2)
        finally:
            shutil.rmtree(input_dir, ignore_errors=True)

    def test_pii_scan_limit_flag_accepted(self):
        code, out, err = run_cli(
            ["audit", str(SAMPLE_CORPUS), "--output", self.output_dir, "--pii-scan-limit", "1000", "--quiet"]
        )
        self.assertEqual(code, 0)

    def test_ip_mode_flag_accepted(self):
        code, out, err = run_cli(
            ["audit", str(SAMPLE_CORPUS), "--output", self.output_dir, "--ip-mode", "all", "--quiet"]
        )
        self.assertEqual(code, 0)

    def test_ip_mode_invalid_choice_exits_two(self):
        code, out, err = run_cli(
            ["audit", str(SAMPLE_CORPUS), "--output", self.output_dir, "--ip-mode", "bogus"]
        )
        self.assertEqual(code, 2)


class TestCleanCommand(unittest.TestCase):
    def setUp(self):
        self.input_dir = tempfile.mkdtemp()
        self.output_dir = tempfile.mkdtemp()
        Path(self.input_dir, "doc.txt").write_text(
            "Contact john.doe@example.com for details about the ongoing project and its current status "
            "as we move toward the next milestone in the release schedule.",
            encoding="utf-8",
        )
        shutil.rmtree(self.output_dir)  # cleaner creates it

    def tearDown(self):
        shutil.rmtree(self.input_dir, ignore_errors=True)
        shutil.rmtree(self.output_dir, ignore_errors=True)

    def test_clean_nonexistent_path_exits_one(self):
        code, out, err = run_cli(["clean", "/nonexistent/xyz", "--output", self.output_dir])
        self.assertEqual(code, 1)

    def test_clean_writes_output_and_exits_zero(self):
        code, out, err = run_cli(["clean", self.input_dir, "--output", self.output_dir])
        self.assertEqual(code, 0)
        self.assertIn("Cleaning Results", out)
        self.assertTrue((Path(self.output_dir) / "doc.txt").exists())

    def test_clean_json_format(self):
        code, out, err = run_cli(["clean", self.input_dir, "--output", self.output_dir, "--format", "json"])
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertIn("files_kept", data)
        self.assertIn("reduction_pct", data)

    def test_clean_no_dedup_and_no_pii_flags(self):
        code, out, err = run_cli(
            ["clean", self.input_dir, "--output", self.output_dir, "--no-dedup", "--no-pii", "--quiet"]
        )
        self.assertEqual(code, 0)
        out_text = (Path(self.output_dir) / "doc.txt").read_text()
        self.assertIn("john.doe@example.com", out_text)

    def test_clean_threshold_flags_accepted(self):
        code, out, err = run_cli(
            [
                "clean", self.input_dir, "--output", self.output_dir,
                "--min-chars", "5", "--min-text-density", "0.1", "--max-repetition-ratio", "0.9", "--quiet",
            ]
        )
        self.assertEqual(code, 0)

    def test_clean_output_inside_input_is_rejected(self):
        nested_output = str(Path(self.input_dir) / "clean-out")
        code, out, err = run_cli(["clean", self.input_dir, "--output", nested_output])
        self.assertEqual(code, 2)


class TestColorHandling(unittest.TestCase):
    def test_no_color_when_format_json(self):
        self.assertFalse(cli._use_color("json", quiet=False))

    def test_no_color_when_quiet(self):
        self.assertFalse(cli._use_color("text", quiet=True))

    def test_no_color_when_not_a_tty(self):
        with mock.patch.object(sys.stdout, "isatty", return_value=False):
            self.assertFalse(cli._use_color("text", quiet=False))

    def test_no_color_when_no_color_env_set(self):
        with mock.patch.object(sys.stdout, "isatty", return_value=True):
            with mock.patch.dict(os.environ, {"NO_COLOR": "1"}):
                self.assertFalse(cli._use_color("text", quiet=False))

    def test_color_when_tty_and_no_no_color(self):
        with mock.patch.object(sys.stdout, "isatty", return_value=True):
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("NO_COLOR", None)
                self.assertTrue(cli._use_color("text", quiet=False))

    def test_colors_class_disabled_yields_empty_codes(self):
        c = cli._Colors(False)
        self.assertEqual(c.BOLD, "")
        self.assertEqual(c.RED, "")

    def test_colors_class_enabled_yields_codes(self):
        c = cli._Colors(True)
        self.assertNotEqual(c.BOLD, "")


class TestGoldenSampleCorpusAudit(unittest.TestCase):
    """Pins the audit JSON for examples/sample-corpus/ against a checked-in snapshot."""

    GOLDEN_PATH = Path(__file__).parent / "fixtures" / "golden_sample_corpus_audit.json"
    VOLATILE_AUDIT_FIELDS = ("timestamp", "corpus_path")

    def test_audit_json_matches_golden_snapshot(self):
        output_dir = tempfile.mkdtemp()
        try:
            code, out, err = run_cli(["audit", str(SAMPLE_CORPUS), "--output", output_dir, "--format", "json"])
            self.assertEqual(code, 0, err)
            data = json.loads(out)
            for field in self.VOLATILE_AUDIT_FIELDS:
                data["audit"].pop(field, None)

            with open(self.GOLDEN_PATH, encoding="utf-8") as f:
                golden = json.load(f)

            self.assertEqual(
                json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True),
                json.dumps(golden, indent=2, ensure_ascii=False, sort_keys=True),
                "Audit JSON for examples/sample-corpus/ drifted from the golden snapshot. "
                "If this is an intentional behavior change, regenerate "
                "tests/fixtures/golden_sample_corpus_audit.json.",
            )
        finally:
            shutil.rmtree(output_dir, ignore_errors=True)


class TestAuditPathsArePlatformIndependent(unittest.TestCase):
    """
    Path.relative_to(...) yields OS-native separators — backslashes on
    Windows. auditor.py must use .as_posix() everywhere a path is recorded
    as a string, so a report produced on Windows is byte-identical (for
    path fields) to one produced on Linux/macOS and can be consumed by
    tooling on any platform.
    """

    def test_sample_corpus_audit_json_has_no_backslashes(self):
        output_dir = tempfile.mkdtemp()
        try:
            code, out, err = run_cli(["audit", str(SAMPLE_CORPUS), "--output", output_dir, "--format", "json"])
            self.assertEqual(code, 0, err)
            data = json.loads(out)
            for file_audit in data["audit"]["file_audits"]:
                self.assertNotIn("\\", file_audit["path"], f"backslash in path: {file_audit['path']!r}")
                if file_audit["duplicate_of"]:
                    self.assertNotIn(
                        "\\", file_audit["duplicate_of"],
                        f"backslash in duplicate_of: {file_audit['duplicate_of']!r}",
                    )
        finally:
            shutil.rmtree(output_dir, ignore_errors=True)

    def test_nested_file_path_uses_forward_slash(self):
        from forge_prep.auditor import CorpusAuditor

        input_dir = tempfile.mkdtemp()
        try:
            nested = Path(input_dir) / "sub" / "dir" / "doc.txt"
            nested.parent.mkdir(parents=True)
            nested.write_text(
                "A nested document with enough content to clear the minimum character threshold for quality checks.",
                encoding="utf-8",
            )
            result = CorpusAuditor(input_dir).audit()
            self.assertEqual(result.file_audits[0].path, "sub/dir/doc.txt")
            self.assertNotIn("\\", result.file_audits[0].path)
        finally:
            shutil.rmtree(input_dir, ignore_errors=True)

    def test_duplicate_of_uses_forward_slash(self):
        from forge_prep.auditor import CorpusAuditor

        input_dir = tempfile.mkdtemp()
        try:
            content = "Duplicate content nested in a subdirectory, long enough to clear the quality threshold checks."
            # "sub/original.txt" must sort before "zzz_copy.txt" (files are
            # processed in sorted order) so the nested file is the one
            # recorded as duplicate_of, exercising the .as_posix() path.
            first = Path(input_dir) / "sub" / "original.txt"
            second = Path(input_dir) / "zzz_copy.txt"
            first.parent.mkdir(parents=True)
            first.write_text(content, encoding="utf-8")
            second.write_text(content, encoding="utf-8")

            result = CorpusAuditor(input_dir).audit()
            dup = next(fa for fa in result.file_audits if fa.is_duplicate)
            self.assertEqual(dup.duplicate_of, "sub/original.txt")
            self.assertNotIn("\\", dup.duplicate_of)
        finally:
            shutil.rmtree(input_dir, ignore_errors=True)

    def test_cleaner_actions_log_uses_forward_slash(self):
        from forge_prep.cleaner import CorpusCleaner

        input_dir = tempfile.mkdtemp()
        output_dir = tempfile.mkdtemp()
        shutil.rmtree(output_dir)
        try:
            nested = Path(input_dir) / "sub" / "tiny.txt"
            nested.parent.mkdir(parents=True)
            nested.write_text("x", encoding="utf-8")  # too short, triggers a SKIP log line

            result = CorpusCleaner(input_dir, output_dir).clean()
            self.assertTrue(result.actions_log)
            for entry in result.actions_log:
                self.assertNotIn("\\", entry, f"backslash in actions_log entry: {entry!r}")
        finally:
            shutil.rmtree(input_dir, ignore_errors=True)
            shutil.rmtree(output_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()

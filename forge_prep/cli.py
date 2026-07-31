"""
forge-prep CLI — command-line interface for corpus auditing,
cleaning, and Forge readiness scoring.
"""

import argparse
import json
import os
import sys
import time
from dataclasses import asdict
from pathlib import Path

from forge_prep._version import get_version
from forge_prep.auditor import CorpusAuditor
from forge_prep.cleaner import CorpusCleaner
from forge_prep.pii import IP_MODES
from forge_prep.report import ReadinessReport
from forge_prep.scorer import ReadinessScorer

BAR_FULL = "█"
BAR_EMPTY = "░"
BOX_WIDTH = 52


class _Colors:
    """Resolved ANSI color codes — all empty strings when color is disabled."""

    def __init__(self, enabled: bool):
        codes = dict(
            BOLD="\033[1m", DIM="\033[2m", GREEN="\033[92m",
            YELLOW="\033[93m", RED="\033[91m", CYAN="\033[96m", RESET="\033[0m",
        )
        for name, code in codes.items():
            setattr(self, name, code if enabled else "")

    def grade(self, grade: str) -> str:
        return {"A": self.GREEN, "B": self.GREEN, "C": self.YELLOW, "D": self.RED, "F": self.RED}.get(grade, self.RESET)


def _use_color(fmt: str, quiet: bool) -> bool:
    if fmt == "json" or quiet:
        return False
    if os.environ.get("NO_COLOR"):
        return False
    return sys.stdout.isatty()


def _score_bar(score: float, c: _Colors, width: int = 20) -> str:
    filled = int(score / 100 * width)
    return f"{c.GREEN}{BAR_FULL * filled}{c.DIM}{BAR_EMPTY * (width - filled)}{c.RESET}"


def _print_banner(c: _Colors):
    v = get_version()
    top = "╔" + "═" * BOX_WIDTH + "╗"
    bottom = "╚" + "═" * BOX_WIDTH + "╝"
    line1 = "║" + f"forge-prep v{v}".center(BOX_WIDTH) + "║"
    line2 = "║" + "Data Readiness Toolkit for Mistral Forge".center(BOX_WIDTH) + "║"
    print(f"\n{c.BOLD}{c.CYAN}{top}\n{line1}\n{line2}\n{bottom}{c.RESET}\n")


def _check_output_not_inside_input(input_path: Path, output_path: Path):
    """Refuse to write output inside the input directory — a re-run would re-ingest prior output."""
    input_resolved = input_path.resolve()
    output_resolved = output_path.resolve()
    if output_resolved == input_resolved or input_resolved in output_resolved.parents:
        print(
            f"Error: --output ({output_resolved}) resolves inside the input path ({input_resolved}). "
            f"Re-running would re-ingest the prior output. Choose a different --output directory.",
            file=sys.stderr,
        )
        sys.exit(2)


def _print_audit_text(audit_result, score_result, c: _Colors, elapsed: float, md_path, json_path):
    mb = audit_result.total_size_bytes / 1024 / 1024

    print(f"\n{c.BOLD}═══ Forge Readiness Score ═══{c.RESET}\n")
    print(
        f"  {_score_bar(score_result.overall, c, 30)}  {c.BOLD}{score_result.overall}/100{c.RESET}  "
        f"Grade: {c.grade(score_result.grade)}{c.BOLD}{score_result.grade}{c.RESET}"
    )
    print(f"  {c.DIM}{score_result.forge_recommendation}{c.RESET}\n")

    print(f"{c.BOLD}  Corpus Summary{c.RESET}")
    print(f"  Files:    {audit_result.total_files:,}")
    print(f"  Size:     {mb:.1f} MB")
    if audit_result.tokens_estimate_uncertain:
        print(
            f"  Tokens:   ~{audit_result.total_tokens_estimate:,}  {c.YELLOW}(uncertain — range "
            f"{audit_result.total_tokens_estimate_low:,}–{audit_result.total_tokens_estimate_high:,}, "
            f"see docs/methodology.md){c.RESET}"
        )
    else:
        print(f"  Tokens:   ~{audit_result.total_tokens_estimate:,}")
    print(f"  Dupes:    {audit_result.duplicate_count:,}")
    print(f"  PII hits: {audit_result.pii_files_count:,}")
    print()

    print(f"{c.BOLD}  Dimension Breakdown{c.RESET}")
    for d in score_result.dimensions:
        bar = _score_bar(d.score, c, 15)
        print(f"  {d.name:<20s} {bar} {d.score:5.1f}  {c.DIM}{d.details[:80]}{c.RESET}")
    print()

    truncated_files = [fa for fa in audit_result.file_audits if fa.pii_scan_truncated]
    if truncated_files:
        print(
            f"{c.RED}{c.BOLD}WARNING:{c.RESET}{c.RED} PII scan was truncated by --pii-scan-limit on "
            f"{len(truncated_files)} file(s). These files were not fully scanned for PII — "
            f"the Privacy score above may be inaccurate. Re-run with --pii-scan-limit 0 to scan in full.{c.RESET}\n"
        )

    print(f"{c.GREEN}Reports saved:{c.RESET}")
    print(f"  Markdown: {md_path}")
    print(f"  JSON:     {json_path}")
    print(f"\n{c.DIM}Completed in {elapsed:.2f}s{c.RESET}\n")


def cmd_audit(args: argparse.Namespace) -> int:
    color_enabled = _use_color(args.format, args.quiet)
    c = _Colors(color_enabled)
    corpus = Path(args.corpus_path)

    if not corpus.exists():
        print(f"{c.RED}Error: Path '{args.corpus_path}' does not exist.{c.RESET}", file=sys.stderr)
        return 1

    output_dir = Path(args.output)
    _check_output_not_inside_input(corpus, output_dir)

    if args.format == "text" and not args.quiet:
        _print_banner(c)
        print(f"{c.BOLD}Auditing:{c.RESET} {corpus}")

    start = time.time()
    auditor = CorpusAuditor(
        str(corpus),
        pii_scan_limit=args.pii_scan_limit,
        ip_mode=args.ip_mode,
    )
    audit_result = auditor.audit()

    scorer = ReadinessScorer()
    score_result = scorer.score(audit_result)
    elapsed = time.time() - start

    report = ReadinessReport(audit_result, score_result)
    md_path, json_path = report.save(str(output_dir))

    if args.format == "json":
        print(json.dumps(report.to_json(), indent=2, ensure_ascii=False))
    elif not args.quiet:
        _print_audit_text(audit_result, score_result, c, elapsed, md_path, json_path)

    if args.fail_under is not None and score_result.overall < args.fail_under:
        if args.format != "json" and not args.quiet:
            print(
                f"{c.RED}FAIL: score {score_result.overall} is below --fail-under {args.fail_under}{c.RESET}",
                file=sys.stderr,
            )
        return 1

    return 0


def _clean_result_to_dict(result) -> dict:
    data = asdict(result)
    data["reduction_pct"] = result.reduction_pct
    return data


def cmd_clean(args: argparse.Namespace) -> int:
    color_enabled = _use_color(args.format, args.quiet)
    c = _Colors(color_enabled)
    input_path = Path(args.input_path)

    if not input_path.exists():
        print(f"{c.RED}Error: Path '{args.input_path}' does not exist.{c.RESET}", file=sys.stderr)
        return 1

    output_path = Path(args.output)
    _check_output_not_inside_input(input_path, output_path)

    dedup = not args.no_dedup
    scrub_pii = not args.no_pii

    if args.format == "text" and not args.quiet:
        _print_banner(c)
        print(f"{c.BOLD}Cleaning:{c.RESET} {input_path} → {output_path}")
        print(f"  Dedup: {'ON' if dedup else 'OFF'}  |  PII Scrub: {'ON' if scrub_pii else 'OFF'}")

    start = time.time()
    cleaner = CorpusCleaner(
        str(input_path),
        str(output_path),
        dedup=dedup,
        scrub_pii=scrub_pii,
        min_chars=args.min_chars,
        min_text_density=args.min_text_density,
        max_repetition_ratio=args.max_repetition_ratio,
        ip_mode=args.ip_mode,
    )
    result = cleaner.clean()
    elapsed = time.time() - start

    if args.format == "json":
        print(json.dumps(_clean_result_to_dict(result), indent=2, ensure_ascii=False))
    elif not args.quiet:
        print(f"\n{c.BOLD}═══ Cleaning Results ═══{c.RESET}\n")
        print(f"  Processed:  {result.files_processed:,} files")
        print(f"  Kept:       {c.GREEN}{result.files_kept:,}{c.RESET}")
        print(f"  Removed:    {c.RED}{result.files_removed:,}{c.RESET}")
        print(f"  Deduped:    {result.duplicates_removed:,}")
        print(f"  PII scrubs: {result.pii_scrubbed_files:,} files ({result.pii_replacements:,} replacements)")
        print(
            f"  Size:       {result.bytes_before / 1024 / 1024:.1f} MB → "
            f"{result.bytes_after / 1024 / 1024:.1f} MB ({result.reduction_pct:.1f}% reduction)"
        )
        print(f"\n{c.GREEN}Clean corpus written to: {output_path}{c.RESET}")
        print(f"{c.DIM}Completed in {elapsed:.2f}s{c.RESET}\n")

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="forge-prep",
        description="Data readiness toolkit for Mistral Forge — audit, clean, and prepare enterprise data for custom model training.",
    )
    parser.add_argument("--version", action="version", version=f"forge-prep {get_version()}")

    subparsers = parser.add_subparsers(dest="command", metavar="command")

    audit_p = subparsers.add_parser("audit", help="Analyze a corpus and produce a Forge Readiness Score")
    audit_p.add_argument("corpus_path", help="Path to the corpus directory to audit")
    audit_p.add_argument("--output", default="./forge-prep-output", metavar="DIR", help="Directory to write reports to")
    audit_p.add_argument(
        "--pii-scan-limit", type=int, default=0, metavar="BYTES",
        help="Max characters to scan per file for PII (0 = unlimited, default: 0)",
    )
    audit_p.add_argument(
        "--ip-mode", choices=sorted(IP_MODES), default="public",
        help="How to treat IP addresses: 'public' (default, ignores RFC1918/loopback/link-local), 'all', or 'off'",
    )
    audit_p.add_argument("--fail-under", type=float, default=None, metavar="N", help="Exit 1 if the readiness score is below N")
    audit_p.add_argument("--format", choices=["text", "json"], default="text", help="Output format (default: text)")
    audit_p.add_argument("--quiet", action="store_true", help="Suppress non-essential output")
    audit_p.set_defaults(func=cmd_audit)

    clean_p = subparsers.add_parser("clean", help="Deduplicate, scrub PII, and filter low-quality files")
    clean_p.add_argument("input_path", help="Path to the corpus directory to clean")
    clean_p.add_argument("--output", default="./forge-prep-clean", metavar="DIR", help="Directory to write the clean corpus to")
    clean_p.add_argument("--min-chars", type=int, default=100, metavar="N", help="Minimum character count to keep a file (default: 100)")
    clean_p.add_argument("--min-text-density", type=float, default=0.3, metavar="RATIO", help="Minimum alpha-character ratio to keep a file (default: 0.3)")
    clean_p.add_argument("--max-repetition-ratio", type=float, default=0.5, metavar="RATIO", help="Minimum unique-line ratio to keep a file (default: 0.5)")
    clean_p.add_argument("--no-dedup", action="store_true", help="Disable exact-duplicate removal")
    clean_p.add_argument("--no-pii", action="store_true", help="Disable PII scrubbing")
    clean_p.add_argument(
        "--ip-mode", choices=sorted(IP_MODES), default="public",
        help="How to treat IP addresses: 'public' (default, ignores RFC1918/loopback/link-local), 'all', or 'off'",
    )
    clean_p.add_argument("--format", choices=["text", "json"], default="text", help="Output format (default: text)")
    clean_p.add_argument("--quiet", action="store_true", help="Suppress non-essential output")
    clean_p.set_defaults(func=cmd_clean)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    sys.exit(args.func(args))


if __name__ == "__main__":
    main()

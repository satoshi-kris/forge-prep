"""
forge-prep CLI — command-line interface for corpus auditing,
cleaning, and Forge readiness scoring.
"""

import sys
import json
import time
from pathlib import Path

from forge_prep.auditor import CorpusAuditor
from forge_prep.cleaner import CorpusCleaner
from forge_prep.scorer import ReadinessScorer
from forge_prep.report import ReadinessReport


BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
CYAN = "\033[96m"
RESET = "\033[0m"
BAR_FULL = "█"
BAR_EMPTY = "░"


def _grade_color(grade: str) -> str:
    return {"A": GREEN, "B": GREEN, "C": YELLOW, "D": RED, "F": RED}.get(grade, RESET)


def _score_bar(score: float, width: int = 20) -> str:
    filled = int(score / 100 * width)
    return f"{GREEN}{BAR_FULL * filled}{DIM}{BAR_EMPTY * (width - filled)}{RESET}"


def print_banner():
    print(f"""
{BOLD}{CYAN}╔══════════════════════════════════════════════════╗
║              forge-prep v0.1.0                    ║
║    Data Readiness Toolkit for Mistral Forge       ║
╚══════════════════════════════════════════════════╝{RESET}
""")


def cmd_audit(corpus_path: str, output_dir: str = "./forge-prep-output"):
    """Audit a corpus and produce a readiness report."""
    print_banner()
    corpus = Path(corpus_path)
    if not corpus.exists():
        print(f"{RED}Error: Path '{corpus_path}' does not exist.{RESET}")
        sys.exit(1)

    print(f"{BOLD}Auditing:{RESET} {corpus}")
    start = time.time()

    auditor = CorpusAuditor(corpus_path)
    audit_result = auditor.audit()

    scorer = ReadinessScorer()
    score_result = scorer.score(audit_result)

    elapsed = time.time() - start

    # Print summary
    mb = audit_result.total_size_bytes / 1024 / 1024
    grade_c = _grade_color(score_result.grade)

    print(f"\n{BOLD}═══ Forge Readiness Score ═══{RESET}\n")
    print(f"  {_score_bar(score_result.overall, 30)}  {BOLD}{score_result.overall}/100{RESET}  Grade: {grade_c}{BOLD}{score_result.grade}{RESET}")
    print(f"  {DIM}{score_result.forge_recommendation}{RESET}\n")

    print(f"{BOLD}  Corpus Summary{RESET}")
    print(f"  Files:    {audit_result.total_files:,}")
    print(f"  Size:     {mb:.1f} MB")
    print(f"  Tokens:   ~{audit_result.total_tokens_estimate:,}")
    print(f"  Dupes:    {audit_result.duplicate_count:,}")
    print(f"  PII hits: {audit_result.pii_files_count:,}")
    print()

    print(f"{BOLD}  Dimension Breakdown{RESET}")
    for d in score_result.dimensions:
        bar = _score_bar(d.score, 15)
        print(f"  {d.name:<20s} {bar} {d.score:5.1f}  {DIM}{d.details[:80]}{RESET}")
    print()

    # Save reports
    report = ReadinessReport(audit_result, score_result)
    md_path, json_path = report.save(output_dir)
    print(f"{GREEN}Reports saved:{RESET}")
    print(f"  Markdown: {md_path}")
    print(f"  JSON:     {json_path}")
    print(f"\n{DIM}Completed in {elapsed:.2f}s{RESET}\n")


def cmd_clean(input_path: str, output_path: str = "./forge-prep-clean", dedup: bool = True, scrub_pii: bool = True):
    """Clean a corpus: deduplicate, scrub PII, filter low quality."""
    print_banner()
    print(f"{BOLD}Cleaning:{RESET} {input_path} → {output_path}")
    print(f"  Dedup: {'ON' if dedup else 'OFF'}  |  PII Scrub: {'ON' if scrub_pii else 'OFF'}")
    start = time.time()

    cleaner = CorpusCleaner(input_path, output_path, dedup=dedup, scrub_pii=scrub_pii)
    result = cleaner.clean()
    elapsed = time.time() - start

    print(f"\n{BOLD}═══ Cleaning Results ═══{RESET}\n")
    print(f"  Processed:  {result.files_processed:,} files")
    print(f"  Kept:       {GREEN}{result.files_kept:,}{RESET}")
    print(f"  Removed:    {RED}{result.files_removed:,}{RESET}")
    print(f"  Deduped:    {result.duplicates_removed:,}")
    print(f"  PII scrubs: {result.pii_scrubbed_files:,} files ({result.pii_replacements:,} replacements)")
    print(f"  Size:       {result.bytes_before / 1024 / 1024:.1f} MB → {result.bytes_after / 1024 / 1024:.1f} MB ({result.reduction_pct:.1f}% reduction)")
    print(f"\n{GREEN}Clean corpus written to: {output_path}{RESET}")
    print(f"{DIM}Completed in {elapsed:.2f}s{RESET}\n")


def main():
    """Simple arg-based CLI (no external deps required)."""
    args = sys.argv[1:]

    if not args or args[0] in ("-h", "--help", "help"):
        print_banner()
        print(f"""{BOLD}Usage:{RESET}
  forge-prep audit <corpus_path> [--output <dir>]
  forge-prep clean <input_path> [--output <dir>] [--no-dedup] [--no-pii]
  forge-prep dashboard <report.json>

{BOLD}Commands:{RESET}
  audit       Analyze a corpus and produce a Forge Readiness Score
  clean       Deduplicate, scrub PII, and filter low-quality files
  dashboard   Launch the interactive readiness dashboard

{BOLD}Examples:{RESET}
  forge-prep audit ./my-data/
  forge-prep audit ./my-data/ --output ./reports/
  forge-prep clean ./my-data/ --output ./clean-data/
  forge-prep clean ./my-data/ --no-pii
""")
        sys.exit(0)

    command = args[0]

    if command == "audit":
        if len(args) < 2:
            print(f"{RED}Error: audit requires a corpus path.{RESET}")
            sys.exit(1)
        corpus_path = args[1]
        output_dir = "./forge-prep-output"
        if "--output" in args:
            idx = args.index("--output")
            output_dir = args[idx + 1]
        cmd_audit(corpus_path, output_dir)

    elif command == "clean":
        if len(args) < 2:
            print(f"{RED}Error: clean requires an input path.{RESET}")
            sys.exit(1)
        input_path = args[1]
        output_path = "./forge-prep-clean"
        dedup = "--no-dedup" not in args
        scrub_pii = "--no-pii" not in args
        if "--output" in args:
            idx = args.index("--output")
            output_path = args[idx + 1]
        cmd_clean(input_path, output_path, dedup=dedup, scrub_pii=scrub_pii)

    else:
        print(f"{RED}Unknown command: {command}{RESET}")
        print(f"Run 'forge-prep --help' for usage.")
        sys.exit(1)


if __name__ == "__main__":
    main()

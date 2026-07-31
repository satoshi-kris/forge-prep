# forge-prep

**Data readiness toolkit for [Mistral Forge](https://mistral.ai/news/forge) — audit, clean, and prepare enterprise data for custom model training.**

> forge-prep is an independent open-source project. It is not affiliated with, endorsed by, or sponsored by Mistral AI.

42% of enterprise teams spend more than half their time maintaining and organizing data rather than using it productively ([Futurum 1H 2026](https://futurumgroup.com/insights/mistral-forge-takes-aim-at-rag-but-who-actually-needs-custom-models/)). Forge is an incredible platform, but most enterprises can't use it because their data isn't ready.

**forge-prep** bridges that gap.

The output below is the real, unedited result of running `forge-prep audit examples/sample-corpus/` against the demo corpus checked into this repo — clone it and run the same command to reproduce it exactly.

```
$ forge-prep audit examples/sample-corpus

╔════════════════════════════════════════════════════╗
║                 forge-prep v0.1.0                  ║
║      Data Readiness Toolkit for Mistral Forge      ║
╚════════════════════════════════════════════════════╝

Auditing: examples/sample-corpus

═══ Forge Readiness Score ═══

  ████████░░░░░░░░░░░░░░░░░░░░░░  27.8/100  Grade: F
  Not ready — corpus needs fundamental restructuring before any model training.

  Corpus Summary
  Files:    10
  Size:     0.0 MB
  Tokens:   ~2,873  (uncertain — range 2,206–3,530, see docs/methodology.md)
  Dupes:    1
  PII hits: 6

  Dimension Breakdown
  Volume               █░░░░░░░░░░░░░░  10.0  2,873 tokens (0 MB) — insufficient for any Forge training stage. Token count is
  Quality              ███░░░░░░░░░░░░  20.0  Pervasive quality issues (40% flag rate). Major filtering needed.
  Deduplication        ██████░░░░░░░░░  45.0  1 duplicates (10.0%) — significant dedup required.
  Privacy              █░░░░░░░░░░░░░░  10.0  Widespread PII (6 files, 60.0%). Critical: scrub before any training.
  Language Focus       ████████████░░░  80.0  Bilingual corpus — consider if both languages are needed for your domain.
  Format Consistency   ██████████░░░░░  70.0  4 file formats. Consider standardizing to .txt or .jsonl for Forge.

Reports saved:
  Markdown: forge-prep-output/forge_readiness_report.md
  JSON:     forge-prep-output/forge_readiness_report.json
```

The scoring weights and grade bands above are heuristics chosen by this project's author, not requirements published by Mistral — see [`docs/methodology.md`](docs/methodology.md) for the full formula and the reasoning behind it, and [`docs/limitations.md`](docs/limitations.md) for what the tool does *not* catch before you rely on it.

## What It Does

| Command | Description |
|---------|-------------|
| `forge-prep audit <path>` | Scan a corpus and produce a 0–100 Forge Readiness Score with per-dimension breakdown |
| `forge-prep clean <path>` | Deduplicate, scrub PII, and filter low-quality files into a Forge-ready corpus |

Both commands support `--format {text,json}` and `--quiet` for scripting, and `audit` supports `--fail-under N` to make the exit code CI-friendly (`forge-prep audit ./data --fail-under 70` exits 1 if the score is below 70).

### Audit Dimensions

- **Volume** — Is there enough data for Forge pre-training vs. fine-tuning? Token counts are estimated from character counts with a per-file-type multiplier, not run through a real tokenizer. For file types where that estimate's held-out error exceeds ±25% (currently CSV/JSONL), the report shows a range instead of a single number — see [`docs/methodology.md`](docs/methodology.md) for exactly how much to trust it.
- **Quality** — Short files, low text density, high repetition, encoding issues
- **Deduplication** — Exact content duplicates detected via SHA-256 hashing
- **Privacy** — PII detection across the *entire* file (email, phone, IP, credit card, SSN, IBAN, French NIR), each validated with a checksum or context check to cut down on false positives — see [`docs/limitations.md`](docs/limitations.md) for what it can't see
- **Language Focus** — Multilingual corpus detection with automatic language identification
- **Format Consistency** — File type distribution and standardization recommendations

PII scanning covers the full file by default (in bounded, overlapping chunks — never the whole file in memory at once). Use `--pii-scan-limit BYTES` to cap the scan per file for very large corpora; any file the limit truncates is flagged `pii_scan_truncated: true` in the report and called out with a warning, so a partial scan is never silently reported as "clean." Use `--ip-mode {public,all,off}` to control whether private/loopback IP ranges count as PII (default: `public`, meaning private ranges are ignored since they're not personal data on their own).

### Cleaning Pipeline

```
$ forge-prep clean examples/sample-corpus --output ./forge-ready/

═══ Cleaning Results ═══

  Processed:  10 files
  Kept:       7
  Removed:    3
  Deduped:    1
  PII scrubs: 5 files (25 replacements)
  Size:       0.0 MB → 0.0 MB (40.0% reduction)
```

The cleaner:
- Removes exact-content duplicates
- Replaces PII with typed placeholders (`[EMAIL_REDACTED]`, `[PHONE_REDACTED]`, etc.), using the exact same validated detection logic as `audit` (they share one code path in `forge_prep/pii.py`, so they can't disagree)
- Filters files below quality thresholds (too short, low text density, high repetition) — thresholds are configurable via `--min-chars`, `--min-text-density`, and `--max-repetition-ratio`
- Outputs a clean, Forge-compatible directory structure

Redaction here is **pseudonymisation, not anonymisation** — see [`docs/limitations.md`](docs/limitations.md) before treating cleaned output as safe to share without a lawful basis.

## Installation

```bash
# From source (zero external dependencies)
git clone https://github.com/satoshi-kris/forge-prep.git
cd forge-prep
pip install -e .

# Then run
forge-prep audit ./your-data/
```

**Zero external dependencies.** The core toolkit uses only Python 3.10+ stdlib. No pip install wall, no version conflicts, no ML frameworks required.

## Output

Every audit produces:
- `forge_readiness_report.md` — Human-readable Markdown report
- `forge_readiness_report.json` — Machine-readable JSON for CI/CD integration (`forge-prep audit ./data --format json` prints the same JSON straight to stdout, no file needed)

## Architecture

```
forge-prep/
├── forge_prep/
│   ├── __init__.py          # Package exports
│   ├── auditor.py           # Corpus scanning & file-level analysis
│   ├── cleaner.py           # Dedup, PII scrub, quality filter
│   ├── pii.py                # Shared PII detection + validation (used by auditor and cleaner)
│   ├── scorer.py            # 0–100 readiness scoring engine
│   ├── report.py            # Markdown + JSON report generation
│   └── cli.py               # Command-line interface (argparse)
├── examples/
│   └── sample-corpus/       # Demo enterprise corpus
├── docs/
│   ├── methodology.md       # Scoring formula, weights, and their provenance
│   └── limitations.md       # What the PII detector does not catch
├── pyproject.toml
└── README.md
```

## Why This Exists

Mistral Forge gives enterprises the power to train custom models on their own data. But there's a prerequisite most announcements skip: **the data has to be ready.**

The typical enterprise corpus contains:
- Duplicated documents across departments
- PII scattered through emails, tickets, and CRM exports  
- Low-quality files (auto-generated logs, boilerplate, placeholder docs)
- Mixed languages without intentional multilingual strategy
- Inconsistent formats that require different parsing pipelines

**forge-prep** is the pre-flight checklist before you commit Forge compute budget. It tells you exactly where your data stands, what to fix, and whether fine-tuning or full pre-training is the right call for your corpus size.

## Roadmap

Nothing below is built yet — none of it is claimed elsewhere in this README.

- [ ] Near-duplicate detection (MinHash/LSH over shingles) — exact-hash dedup catches almost nothing in real corpora where documents differ by a header or footer
- [ ] JSONL training-format converter (chat, instruction, completion schemas)
- [ ] GitHub Action wrapping `--fail-under` / `--format json` with a job summary
- [ ] PDF/DOCX text extraction (optional `[docs]` extra)
- [ ] Report diffing (`forge-prep diff old.json new.json`) to track corpus health over time
- [ ] Optional real tokenizer (`forge-prep[tokens]`, `mistral-common` or `tiktoken`) in place of the word-count heuristic
- [ ] Interactive dashboard for visual exploration of a report — not built, and not shipped in this repo today
- [ ] Forge API connector (direct upload of clean corpus)

## License

Apache 2.0

---

*Built by [Kris](https://github.com/satoshi-kris) — M.S. Data Science & Business Intelligence, EDC Paris Business School (2026)*

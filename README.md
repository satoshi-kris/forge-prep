# forge-prep

**Data readiness toolkit for [Mistral Forge](https://mistral.ai/news/forge) — audit, clean, and prepare enterprise data for custom model training.**

42% of enterprise teams spend more than half their time maintaining and organizing data rather than using it productively ([Futurum 1H 2026](https://futurumgroup.com/insights/mistral-forge-takes-aim-at-rag-but-who-actually-needs-custom-models/)). Forge is an incredible platform, but most enterprises can't use it because their data isn't ready.

**forge-prep** bridges that gap.

```
$ forge-prep audit ./enterprise-corpus/

╔══════════════════════════════════════════════════╗
║              forge-prep v0.1.0                    ║
║    Data Readiness Toolkit for Mistral Forge       ║
╚══════════════════════════════════════════════════╝

═══ Forge Readiness Score ═══

  ████████░░░░░░░░░░░░░░  27.8/100  Grade: F
  Not ready — corpus needs fundamental restructuring before any model training.

  Dimension Breakdown
  Volume               █░░░░░░░░░░░░░░  10.0
  Quality              ███░░░░░░░░░░░░  20.0
  Deduplication        ██████░░░░░░░░░  45.0
  Privacy              █░░░░░░░░░░░░░░  10.0
  Language Focus       ████████████░░░  80.0
  Format Consistency   ██████████░░░░░  70.0
```

## What It Does

| Command | Description |
|---------|-------------|
| `forge-prep audit <path>` | Scan a corpus and produce a 0–100 Forge Readiness Score with per-dimension breakdown |
| `forge-prep clean <path>` | Deduplicate, scrub PII, and filter low-quality files into a Forge-ready corpus |

### Audit Dimensions

- **Volume** — Is there enough data for Forge pre-training vs. fine-tuning?
- **Quality** — Short files, low text density, high repetition, encoding issues
- **Deduplication** — Exact content duplicates detected via MD5 hashing
- **Privacy** — PII detection (email, phone, IP, credit card, SSN, IBAN, French NIR)
- **Language Focus** — Multilingual corpus detection with automatic language identification
- **Format Consistency** — File type distribution and standardization recommendations

### Cleaning Pipeline

```
$ forge-prep clean ./raw-data/ --output ./forge-ready/

═══ Cleaning Results ═══

  Processed:  10 files
  Kept:       7
  Removed:    3
  Deduped:    1
  PII scrubs: 5 files (23 replacements)
  Size:       48.2 MB → 29.1 MB (39.7% reduction)
```

The cleaner:
- Removes exact-content duplicates
- Replaces PII with typed placeholders (`[EMAIL_REDACTED]`, `[PHONE_REDACTED]`, etc.)
- Filters files below quality thresholds (too short, low text density, high repetition)
- Outputs a clean, Forge-compatible directory structure

## Installation

```bash
# From source (zero external dependencies)
git clone https://github.com/kris/forge-prep.git
cd forge-prep
pip install -e .

# Then run
forge-prep audit ./your-data/
```

**Zero external dependencies.** The core toolkit uses only Python 3.10+ stdlib. No pip install wall, no version conflicts, no ML frameworks required.

## Output

Every audit produces:
- `forge_readiness_report.md` — Human-readable Markdown report
- `forge_readiness_report.json` — Machine-readable JSON for dashboards and CI/CD integration

The JSON report powers an interactive React dashboard (included in `/dashboard`) for visual exploration of corpus health.

## Architecture

```
forge-prep/
├── forge_prep/
│   ├── __init__.py          # Package exports
│   ├── auditor.py           # Corpus scanning & file-level analysis
│   ├── cleaner.py           # Dedup, PII scrub, quality filter
│   ├── scorer.py            # 0–100 readiness scoring engine
│   ├── report.py            # Markdown + JSON report generation
│   └── cli.py               # Command-line interface
├── examples/
│   └── sample-corpus/       # Demo enterprise corpus
├── dashboard/               # Interactive React dashboard
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

- [ ] PDF/DOCX text extraction (via `pdfplumber` / `python-docx`)
- [ ] Semantic deduplication (MinHash / SimHash for near-duplicates)
- [ ] Mistral API integration for synthetic eval dataset generation
- [ ] JSONL training format converter (chat, instruction, completion)
- [ ] Cost estimator (estimated Forge compute hours based on corpus size)
- [ ] CI/CD integration (GitHub Action for automated corpus audits)
- [ ] Forge API connector (direct upload of clean corpus)

## License

Apache 2.0

---

*Built by [Kris](https://github.com/kris) — M.S. Data Science & Business Intelligence, EDC Paris Business School (2026)*

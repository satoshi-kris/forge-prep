# Contributing to forge-prep

Thanks for considering contributing! Here's how to get started.

## Setup

```bash
git clone https://github.com/YOUR_USERNAME/forge-prep.git
cd forge-prep
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Running Tests

```bash
# All tests
pytest

# With coverage
pytest --cov=forge_prep --cov-report=term-missing

# Single test file
pytest tests/test_forge_prep.py -v
```

## Linting

```bash
ruff check forge_prep/
ruff format forge_prep/
```

## What We're Looking For

Check the [roadmap in the README](README.md#roadmap) for planned features. High-impact contributions:

- **PDF/DOCX text extraction** — `pdfplumber` and `python-docx` integration in the auditor
- **Semantic deduplication** — MinHash or SimHash for near-duplicate detection
- **Mistral API integration** — synthetic eval dataset generation using Mistral models
- **JSONL converter** — transform clean corpus into Forge-compatible training formats
- **More PII patterns** — German tax IDs, UK NI numbers, etc.
- **Better language detection** — integrate `langdetect` or `fasttext` as optional dependency

## Pull Request Process

1. Fork the repo and create a branch from `main`
2. Add tests for any new functionality
3. Make sure all tests pass: `pytest`
4. Make sure linting passes: `ruff check forge_prep/`
5. Update the README if you've added user-facing features
6. Open a PR with a clear description of what and why

## Code Style

- Python 3.10+ features are fine (type hints, match statements, etc.)
- Keep external dependencies to zero for core functionality
- Optional dependencies go in `[project.optional-dependencies]`
- Every public function needs a docstring

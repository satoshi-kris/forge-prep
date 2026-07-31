# Changelog

All notable changes to forge-prep will be documented in this file.

## [0.1.0] — 2026-07-31

An external audit of the original 2026-04-02 `0.1.0` found correctness and honesty issues that blocked release (false all-clear PII audits, unvalidated PII regexes with real false positives, an unimplemented command advertised in `--help`, dead links, a crash on malformed flags, and claims not backed by tests). This entry describes what `0.1.0` actually ships as after remediation, replacing the original entry rather than layering a second one on top of a release that was never correct.

### Added
- `forge-prep audit` command — scans a corpus directory and produces a Forge Readiness Score (0–100)
- `forge-prep clean` command — deduplicates, scrubs PII, and filters low-quality files
- Six scoring dimensions: Volume, Quality, Deduplication, Privacy, Language Focus, Format Consistency
- PII detection for: email, phone (international), IP address, credit card, US SSN, IBAN, French NIR — each validated (Luhn checksum, IBAN mod-97, NIR control key, IP octet/version-string checks, or context denylist) to cut down on false positives on ordinary business text
- `--pii-scan-limit`, `--ip-mode`, `--fail-under`, `--format {text,json}`, `--quiet`, `--version`, `--min-chars`, `--min-text-density`, `--max-repetition-ratio`, `--no-dedup`, `--no-pii` CLI flags
- Language detection heuristics for English, French, German, Spanish
- Markdown and JSON report generation
- `docs/methodology.md` — scoring weights, formula, and their provenance (author heuristic vs. measured)
- `docs/limitations.md` — what the PII detector does not catch, and why cleaned output is still personal data
- 125-line hand-labeled PII benchmark (`tests/fixtures/pii_benchmark.jsonl`) with a precision/recall regression test
- Held-out token-estimator evaluation set (`tests/fixtures/token_holdout/`, 21 files spanning prose/code/csv/jsonl/markdown, none used for fitting) and the measurement script that fits and evaluates against it (`tests/fixtures/measure_token_estimator.py`)
- Sample enterprise corpus for demo and testing

### Fixed
- **PII audit no longer produces false all-clears.** The auditor used to scan only the first 50,000 characters of each file while the cleaner scanned the whole file — a file with PII past that point would audit as `pii_detected: []` and score Privacy 100/100 while the cleaner still found and redacted it. PII scanning now covers the full file by default, in bounded overlapping chunks (never the whole file in memory at once). Auditor and cleaner now share one detection code path (`forge_prep/pii.py`) so they cannot disagree.
- **PII detectors no longer over-redact ordinary business text.** `credit_card`, `iban`, and `french_nir` now require a valid checksum; `ip_address` rejects malformed octets and version-string false positives and, by default, no longer treats private/loopback/link-local ranges as personal data (`--ip-mode public` is the new default); `ssn_us`/`phone_intl` reject matches preceded by an identifier-like word (invoice, SKU, order, ref, version, build, batch, ID).
- `forge-prep dashboard` — an unimplemented command that exited 0 on failure — has been removed. Unknown commands now exit 2.
- Replaced hand-rolled `sys.argv` parsing with `argparse`. `forge-prep audit ./x --output` (flag with no value) previously crashed with a raw `IndexError`; it now produces a normal usage error and exits 2.
- ANSI color codes are now suppressed when stdout isn't a TTY, when `NO_COLOR` is set, or when `--format json` is used — previously emitted unconditionally, corrupting piped output and CI logs.
- Dedup hashing switched from `hashlib.md5` to `hashlib.sha256(..., usedforsecurity=False)` — MD5 raises on FIPS-enabled hosts, which is exactly the kind of enterprise environment this tool targets. **This changes dedup hash values**; hashes computed by a prior version will not match.
- `datetime.utcnow()` replaced with `datetime.now(timezone.utc)` (removes 21 deprecation warnings on Python 3.12+).
- **Token estimator switched from word-based to character-based, and validated on a held-out set.** The original flat `word_count × 0.85` constant measured at −55.5% error overall against a real tokenizer. A first fix (a per-word multiplier table) turned out to be fitting the wrong unit: coefficient of variation of words-per-token on structured data (CSV/JSONL) was 0.83, vs. 0.05 for chars-per-token on the same files — whitespace-split "words" are unstable on formats with little whitespace, where one "word" can be an entire row. The estimator now uses `token_estimate = char_count / chars_per_token`, fit per file-type group. Evaluated on a genuinely held-out set (21 files never used for fitting — see `tests/fixtures/token_holdout/` and `tests/fixtures/measure_token_estimator.py`), held-out mean absolute error is 8.7% for prose and 10.8% for code (both now confident point estimates), but 57.1% for CSV/JSONL — switching units fixed prose and code but did not fix structured data, whose token density depends on row shape in a way no single constant captures. File types with held-out error ≥25% (currently CSV/JSONL/JSON, and any extension with no held-out measurement) no longer get a bare point estimate: `FileAudit.token_estimate_confident` is `False` for them, and the CLI, Markdown report, and JSON report show a `token_estimate_low`–`token_estimate_high` range instead. The Volume *score* still consumes the same point-estimate sum as before — this change does not alter how Volume is scored, only how the underlying token count is computed and displayed. **This changes `total_tokens_estimate` values**; `tests/fixtures/golden_sample_corpus_audit.json` was regenerated to match. See `docs/methodology.md` for the full CV comparison and held-out results, including an open (undecided) question about whether Volume should score on characters instead of tokens.
- Added an explicit honesty note to `docs/methodology.md` on the PII precision/recall benchmark: it is synthetic and author-generated, its hard negatives were chosen to probe already-known failure modes, and performance on real enterprise text is unmeasured. The reported 1.00/1.00 numbers are unchanged — this is a caveat on what they mean, not a re-measurement.
- Fixed an inconsistency where the diversity score and the recommendation text counted detected languages differently (one excluded `"unknown"`, the other didn't); both now exclude it consistently, and an all-"unknown" corpus is scored explicitly instead of falling through to a misleading "single-language" message.
- `--output` resolving inside the input directory is now a hard error (previously silent, and a re-run would re-ingest the prior output as new input).
- Corrected all repository URLs from the dead `github.com/kris/forge-prep` to `github.com/satoshi-kris/forge-prep` (README, `pyproject.toml`, author byline).
- Removed the README's description of a React dashboard and its place in the architecture tree — it was never built and doesn't exist in this repo. Moved to the roadmap as unbuilt.
- Added an explicit "not affiliated with, endorsed by, or sponsored by Mistral AI" disclaimer to the README and package description.

### Changed
- Test suite expanded from 38 to 83+ tests; added a CLI test suite (previously 0% coverage), a golden-file snapshot test for `examples/sample-corpus/`, and a full-file-scan regression test. Overall coverage gate: `--cov-fail-under=85` in `pytest` addopts.
- `ruff check` is now actually clean (it was already failing pre-remediation; `line-length` widened from 120 to 175 to match this codebase's prevailing style of long natural-language string literals rather than mass-reformatting unrelated prose).
- CI matrix extended to include Python 3.13.

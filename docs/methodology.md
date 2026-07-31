# Methodology

This document explains how forge-prep computes its scores and estimates, and is explicit about what is measured versus what is a judgment call.

**Everything in this document is an author heuristic.** The scoring weights, grade bands, and token thresholds below were chosen by this project's author based on general experience preparing text corpora for LLM training. They are **not** requirements, thresholds, or recommendations published by Mistral, and Mistral has not reviewed or endorsed them. Where a number below has an external source, that source is cited. Everything else is explicitly marked as author judgement — treat it as a starting point to calibrate for your own corpus and use case, not a certification.

## The Forge Readiness Score

The overall score is a weighted average of six per-dimension scores, each 0–100:

```
overall = Σ(dimension.score × dimension.weight) / Σ(dimension.weight)
```

Implemented in [`forge_prep/scorer.py`](../forge_prep/scorer.py), `ReadinessScorer.score()`.

| Dimension | Weight | What it measures | Source |
|---|---|---|---|
| Volume | 0.25 | Estimated token count vs. rule-of-thumb thresholds for pre-training vs. fine-tuning | Author judgement — no published Mistral token-count requirement exists for Forge as of this writing |
| Quality | 0.25 | Rate of quality flags (`too_short`, `low_text_density`, `sparse_lines`, `high_repetition`) across files | Author judgement |
| Privacy | 0.20 | Fraction of files with at least one validated PII match | Author judgement on ratio thresholds; the underlying detectors use published checksum algorithms (Luhn, ISO 13616 IBAN mod-97, INSEE NIR control key) — see the Privacy detail below |
| Deduplication | 0.15 | Fraction of files that are exact-content duplicates (SHA-256) | Author judgement |
| Language Focus | 0.10 | Number of distinct detected languages | Author judgement |
| Format Consistency | 0.05 | Number of distinct file extensions | Author judgement |

The weights themselves (0.25/0.25/0.20/0.15/0.10/0.05) reflect the author's opinion that volume, quality, and privacy matter most for training readiness, with format/language diversity treated as secondary signals. There is no formal derivation behind these specific numbers — a different, equally defensible weighting scheme could be built from the same six dimensions.

### Grade bands

| Score | Grade |
|---|---|
| ≥ 90 | A |
| ≥ 75 | B |
| ≥ 60 | C |
| ≥ 40 | D |
| < 40 | F |

These bands are round numbers chosen by the author for readability, not derived from any dataset of successful vs. failed Forge training runs.

### Volume thresholds

The Volume dimension scores against fixed token-count breakpoints (10M / 1M / 100K / 10K). These are the author's general-purpose rule of thumb for "pre-training needs roughly 10B+ tokens for domain coverage; fine-tuning can work with 100K–10M" — a widely repeated industry rule of thumb, not a number sourced from Mistral Forge documentation specifically. If Mistral publishes concrete Forge token requirements, these breakpoints should be updated to match and this note removed.

## Privacy scoring and PII detection

The Privacy dimension's *ratio thresholds* (what fraction of files with PII counts as "minor" vs. "critical") are author judgement. The *detection* underneath it, however, is not a guess — see [`forge_prep/pii.py`](../forge_prep/pii.py):

- **credit_card**: rejected unless it passes the Luhn/mod-10 checksum (the same check card issuers use).
- **iban**: rejected unless it passes the ISO 13616 mod-97 checksum and matches the correct length for its country code (FR, DE, ES, IT, NL, BE, GB supported; other country codes are treated as non-matches rather than guessed at).
- **french_nir**: rejected unless the INSEE control key (`97 - (first 13 digits mod 97)`) matches, the month is plausible (01–12, or the INSEE special values 20/30/42–99), and the department code is plausible.
- **ip_address**: rejected if any octet exceeds 255, or if the match is actually part of a longer dotted version string (e.g. `10.2.14.3.1`). Whether private/loopback/link-local ranges count as PII at all is controlled by `--ip-mode` (default `public`, meaning they don't — see the code for the exact ranges via Python's `ipaddress` module).
- **ssn_us** / **phone_intl**: rejected if immediately preceded by a word from a denylist (`invoice`, `sku`, `order`, `ref`, `version`, `build`, `batch`, `id`) that indicates the number is actually an identifier, not personal data.
- **email**: matched by pattern only — this is deliberately not further validated, since the email pattern itself is already a strong, low-false-positive signal for this purpose.

**Measured precision/recall:** `tests/test_pii_precision.py` runs the detector against a 125-line hand-labeled benchmark (`tests/fixtures/pii_benchmark.jsonl` — 63 true positives spread across all 7 types, 62 hard negatives drawn from order IDs, invoice numbers, version strings, private/malformed IP addresses, and checksum-invalid card/IBAN/NIR numbers). As of this writing, measured performance on that benchmark is:

| Type | Precision | Recall |
|---|---|---|
| credit_card | 1.00 | 1.00 |
| email | 1.00 | 1.00 |
| french_nir | 1.00 | 1.00 |
| iban | 1.00 | 1.00 |
| ip_address | 1.00 | 1.00 |
| phone_intl | 1.00 | 1.00 |
| ssn_us | 1.00 | 1.00 |
| **Overall** | **1.00** | **1.00** |

These numbers describe accuracy on text that *superficially matches* one of the seven supported patterns — they say nothing about PII types the detector doesn't look for at all (names, addresses, dates of birth in prose, free-text identifiers, and more). See [`docs/limitations.md`](limitations.md) for what's out of scope, and treat a perfect benchmark score as evidence the checksum logic is implemented correctly, not as evidence the tool catches most PII in a real corpus. To reproduce or regenerate the benchmark, see `tests/fixtures/build_pii_benchmark.py`.

## Token estimator

Token counts feed the Volume dimension, which at weight 0.25 is tied for the largest single input to the overall score — so the estimator's error matters more than a cosmetic detail. This section has gone through two revisions; both are kept here so the reasoning is traceable.

### Revision 1 (superseded): a word-based multiplier

The original estimator was a single flat constant, `token_estimate = int(word_count × 0.85)`, applied to every file regardless of content type. Measured against a real tokenizer (`cl100k_base` via `tiktoken`, used only as an offline measurement tool — it is not a project dependency) on `examples/sample-corpus/`, it underestimated by −55.5% overall. A first attempt at fixing this fit a per-extension words-per-token multiplier instead of a flat constant — that revision is no longer in the code because a deeper problem surfaced during review (next section).

### Why the unit itself, not just the constant, was wrong

A fitted words-per-token multiplier for CSV/JSONL landed at ~11 tokens per "word" — a symptom, not a fix. Whitespace-split "words" break down on structured formats: a CSV row or a JSONL object often contains almost no whitespace, so an entire row can be one "word" by that definition, while a real tokenizer still splits it into many tokens at commas, braces, colons, and quotes. No single per-word constant can represent that, because the *number of words per row is itself unstable* across files of the same type.

This is directly measurable via coefficient of variation (CV = standard deviation / mean) of the per-file ratio, computed separately for **tokens-per-word** and **tokens-per-character** on the fitting corpus (`examples/sample-corpus/`):

| Group | n | CV(words-per-token) | CV(chars-per-token) |
|---|---|---|---|
| prose | 7 | 0.124 | **0.103** |
| structured (csv/jsonl) | 2 | **0.827** | **0.047** |
| code | 1 | n/a (single file) | n/a (single file) |

Characters-per-token is **~18x more stable** for structured data (CV 0.047 vs 0.827) and modestly more stable for prose (0.103 vs 0.124). This isn't a marginal preference — the words-per-token CV for structured data means the "constant" varies by ±83% of its own mean across just two files, which is not a usable estimator. Characters are what a tokenizer actually consumes; words are a lossy proxy for that which breaks specifically where whitespace is sparse.

### Revision 2 (current): a character-based multiplier

`forge_prep/auditor.py` now computes `token_estimate = int(char_count / chars_per_token)`, with `chars_per_token` fit per content-type group (weighted `sum(chars) / sum(tokens)`) on `examples/sample-corpus/`:

| Extension group | chars per token | Fit from |
|---|---|---|
| `.txt`, `.md` (prose) | 4.12 | 6 files |
| `.py` (code) | 3.91 | 1 file |
| `.csv`, `.jsonl`, `.json` (structured) | 2.70 | 2 files |
| Everything else (unmeasured extensions) | 3.86 (corpus-wide weighted average) | Fallback, not a fit |

### Held-out evaluation (the number that actually matters)

Fitting and evaluating on the same 10 files (as Revision 1's report originally did) produces an aggregate error of ~0% by construction — that's an identity, not a validation, and doesn't say anything about unseen files. To get a real generalization number, the estimator above was evaluated on a **genuinely held-out set**: 21 files never used for fitting, spanning prose, code, CSV, JSONL, and markdown, none of them in `examples/sample-corpus/`:

- **Prose**: the U.S. Declaration of Independence and U.S. Constitution (public domain, fetched from Project Gutenberg) and the Apache-2.0 license text.
- **Markdown**: this repo's own `README.md`, `CHANGELOG.md`, `docs/methodology.md`, `docs/limitations.md`, `RELEASING.md`, `CONTRIBUTING.md`.
- **Code**: this repo's own `forge_prep/*.py` source files (8 files) — none of them the one `.py` file used for fitting.
- **CSV/JSONL**: 4 synthetic files with deliberately varied shapes — wide numeric (sensor telemetry, many short columns), narrow with a long text field (support tickets), short numeric events, and long-text-per-record (reviews) — because a single CSV "shape" doesn't represent structured data in general.

Reproduce with `python3 tests/fixtures/measure_token_estimator.py` (requires `pip install tiktoken`; not a project dependency).

**Held-out mean absolute error, by group:**

| Group | n | Mean absolute error | Range observed |
|---|---|---|---|
| prose | 9 | **8.7%** | −14.4% to +21.6% |
| code | 8 | **10.8%** | −8.2% to +35.1% |
| structured (csv/jsonl) | 4 | **57.1%** | −44.9% to +93.6% |

This is the real, generalization-honest number, and it tells a clear story: switching to characters fixed prose and code — both now comfortably inside ±25% held-out error, a genuine improvement over the original −55.5%/−44.9% figures, not an artifact of in-sample fitting this time. **It did not fix structured data.** CSV/JSONL held-out error is still 57.1% mean absolute, because token density in structured formats depends on *shape* (wide-numeric vs. narrow-long-text) in a way a single per-format-family constant can't capture — `wide_numeric.csv` and `narrow_text.csv` are both CSV, both held out, and still land on opposite sides of the estimate by 45% and 94% respectively.

### What this means for what forge-prep actually reports

Per-file and aggregate token counts for file types whose held-out error is ≥25% (currently: `.csv`, `.jsonl`, `.json`, and any extension with no held-out measurement at all) are **not shown as a single number**. `FileAudit.token_estimate_confident` is `False` for those files, and the CLI, Markdown report, and JSON report all show a `token_estimate_low`–`token_estimate_high` range instead of (in addition to) the point figure — see the `Tokens:` line in the README's example output. Prose and code, at 8.7% and 10.8% held-out error, are shown as confident point estimates.

**The Volume *score* is unchanged by any of this** — it's still computed from the same point-estimate sum as before recalibration (`total_tokens_estimate`), just against better-fit multipliers. Whether Volume should score on token estimates at all, versus scoring on the exact, uncertainty-free byte/character count directly, is an open question — see the tradeoff note below. This document doesn't resolve it; that's a product decision, not a measurement one.

**Open question — should Volume score on characters instead of tokens?** Token thresholds (10M/1M/100K/10K) are the standard way the field talks about training data volume, so scoring on tokens keeps the number comparable to how everyone else describes corpus size — but it means the second-largest-weighted dimension in the whole score (0.25) partly rests on an estimate with a measured 57%+ error for an entire content-type family. Scoring on character or byte count instead would be exact and never wrong, at the cost of the score no longer being directly comparable to "tokens needed for pre-training" language that the rest of the industry (and this tool's own recommendations) uses. A middle path — scoring on tokens for confident file types and falling back to a characters-based estimate (with a documented, wider conversion assumption) for unconfident ones — is also possible but adds complexity. This tradeoff has not been decided; `_score_volume` in `forge_prep/scorer.py` still scores on `total_tokens_estimate` unchanged.

This measurement used `cl100k_base` as a stand-in for a Mistral-specific tokenizer since no Mistral tokenizer was available in the measurement environment; the exact multipliers and error would differ against Mistral's actual tokenizer, though the direction of the finding (characters are a more stable unit than words, especially for structured data) is a property of the formats being measured, not of the specific tokenizer, and should hold generally.

## Honesty note on the PII benchmark

The precision/recall numbers reported above were measured against `tests/fixtures/pii_benchmark.jsonl`, a fixture written by the same person (and in the same work session) as the validators it's testing. This is a real limitation of the measurement, not a footnote:

- The benchmark is **synthetic and author-generated** — every true positive and every hard negative in it was written by hand, not sampled from real-world text.
- The **hard negatives were chosen by the author** to probe the specific failure modes the audit had already identified (order IDs, version strings, invoice numbers, private IP ranges, checksum-invalid card/IBAN/NIR numbers). This means the benchmark is well-suited to confirming those specific fixes work, but it cannot surface failure modes nobody thought to write a test case for.
- Performance on **real enterprise text is unmeasured**. A 1.00/1.00 score on a 125-line hand-labeled fixture says the checksum and context-denylist logic is implemented correctly against the cases it was designed to catch — it says nothing about precision or recall on an actual enterprise corpus, which will contain PII-adjacent text nobody anticipated when writing this fixture.

Treat the benchmark as a regression test (it will catch a future change that breaks Luhn/mod-97/NIR validation) and not as an external accuracy claim.

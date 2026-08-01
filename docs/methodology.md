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
| Deduplication | 0.15 | Fraction of files that are exact-content duplicates (SHA-256) *or* estimated near-duplicates (MinHash/LSH) | Author judgement on ratio thresholds; near-dup detection is a standard, published algorithm (Broder, 1997) — see "Near-duplicate detection" below |
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

## Near-duplicate detection

Exact-hash dedup (SHA-256) only catches byte-identical files. Real corpora are full of documents that differ by a header, footer, date stamp, or boilerplate disclaimer — exact hashing misses all of them, which made the Deduplication dimension systematically over-optimistic on exactly the kind of data this tool exists to assess. On the next.js benchmark corpus (see below), exact-hash dedup alone found 3,170 duplicates (16.4%); near-duplicate detection found another 602 files (3.1%) that exact hashing missed entirely — files whose content overlaps almost completely but isn't byte-identical, mostly repeated test-fixture boilerplate (e.g. the same `next.config.js` reused with minor edits across dozens of test directories).

### Algorithm

Implemented in [`forge_prep/near_dup.py`](../forge_prep/near_dup.py), pure stdlib (no numpy/scipy/datasketch — consistent with the zero-runtime-dependency guarantee):

1. **Shingling**: each document is split into word 5-grams after whitespace normalization and case folding (`shingle_text`).
2. **MinHash**: each shingle is hashed once, then run through 128 independent fast multiplicative hash functions (seeded deterministically, so signatures are reproducible run to run), keeping the minimum output per function. Two documents' Jaccard similarity is estimated by the fraction of the 128 signature positions where they agree.
3. **LSH banding**: the 128-value signature is split into 16 bands of 8 rows each. Two documents are only ever compared if they share an identical band in *at least one* of the 16 bands — this is what keeps the algorithm sub-quadratic (never O(n²) pairwise comparison across the whole corpus).
4. **Confirmation + clustering**: LSH-candidate pairs are confirmed against the actual estimated Jaccard (not just the one matching band) before being merged, via union-find, into connected-component clusters. Only clusters with 2+ members are reported, each with a representative (its first member in sorted path order — same convention as exact-hash dedup's "first-seen is canonical").

`--near-dup-threshold` (default **0.85** estimated Jaccard) controls sensitivity; `--no-near-dup` disables detection entirely (skips signature computation, not just clustering, so it's also the fast path). A file already counted as an exact duplicate is never also counted as a near-duplicate — the two counts don't overlap — and both feed into the Deduplication score as a combined ratio.

### LSH has false negatives by design — this is expected, not a bug

Banding only generates a candidate pair if the two documents share an identical 8-value band by chance. For genuinely similar documents this happens with high probability (that's the point of the band/row parameters), but it means a small fraction of true near-duplicates near the threshold boundary can be missed. This is the standard, well-understood LSH speed/recall tradeoff — the alternative is full pairwise comparison, which does not scale. `--near-dup-threshold` does not override this: lowering it only affects whether an already-found *candidate* pair is confirmed, not whether LSH finds it as a candidate in the first place (verified in `tests/test_near_dup.py`, `test_unrelated_documents_never_become_lsh_candidates`).

### Performance: bounding cost on large files, and what that costs in recall

The signature-computation step is O(shingles × 128) per file. A handful of large, heavily-shingled files (compiled bundles, generated manifests) dominated runtime disproportionately in initial testing — one 2.5MB, 62,855-shingle file in the next.js corpus took 1.36s on its own. `MAX_SHINGLES` bounds this: documents with more shingles than the cap are reduced via a deterministic "bottom-k" sketch (`forge_prep/near_dup.py`, `_bounded_shingle_hashes`: `heapq.nsmallest(max_shingles, hashes)` — selection is by hash *value*, confirmed not by document position, so two documents sharing only a middle section still match correctly) before MinHash runs. A second, smaller factor: fast multiplicative hashing (`((a·h) ^ b) & 0xFFFFFFFFFFFFFFFF`, Knuth-style) instead of the classical `(a·x + b) mod prime` universal-hash construction — a ~1.6x speedup on its own, with slightly higher signature variance as the tradeoff, not incorrectness.

**The first shipped value (150) was tuned for runtime alone, without measuring the accuracy cost** — every fixture in `tests/test_near_dup.py` is small enough that the cap never engages, so nothing in the test suite could have caught this. That gap was found and closed before release, not after — see `tests/fixtures/measure_shingle_cap.py`, which builds 35 (original, variant) pairs from 7 real books (Project Gutenberg, public domain, boilerplate stripped, 429KB–1.26MB each), each with a known modification: 5%/10%/20%/30% of paragraphs replaced with unrelated content, plus a header/footer-only edit. True Jaccard is computed directly on the full, uncapped shingle sets (independent ground truth, not derived from this tool's own output) — 14 of the 35 pairs are true near-duplicates at the default 0.85 threshold, 21 are not.

| cap | precision | recall | next.js runtime | vs. baseline (19.3s) |
|---|---|---|---|---|
| 150 | 0.929 | 0.929 | 35.9s | 1.86x |
| 250 | 1.000 | 0.857 | 40.4s | 2.09x |
| 500 | 1.000 | 0.929 | 43.8s | 2.27x |
| **2000 (current default)** | **0.933** | **1.000** | **53.3s** | **2.76x** |
| uncapped | 0.875 | 1.000 | 86.7s | 4.49x |

**Read this table with its sample size in mind.** With only 14 positive pairs, a single classification flip moves recall by ~7 percentage points — the non-monotonic dip at 250 and the precision drop at 2000/uncapped are consistent with ordinary MinHash sampling noise at 128 permutations (the standard error of the similarity estimate near a 0.85 true-Jaccard boundary is itself on the order of ±0.03–0.06), not a claim that "more shingles causes worse precision." Two things this table does support with confidence: **150 measurably misses real near-duplicates** (missed 1 of 14 constructed pairs, and 12 of 614 real near-duplicate files found on the next.js corpus), and **2000 achieves perfect recall on this test** at a runtime that's still fast in absolute terms for a once-per-corpus-update operation (53s, not 53 minutes).

**Default raised from 150 to 2000** on this basis — trading the original, self-imposed 2x runtime target (arbitrary, per the person who set it) for recall, since a corpus audit runs once per corpus update, not in a hot loop, and a missed real duplicate is worse than an extra 34 seconds. `--shingle-cap N` (0 = uncapped) exposes this as a user-tunable tradeoff instead of a buried constant — lower it for faster audits of huge corpora where some recall loss on large files is acceptable, raise it or set it to 0 for maximum recall when runtime doesn't matter.

Reproduce: `python3 tests/fixtures/measure_shingle_cap.py /path/to/7+ real books over 200KB each` for the recall/precision table; `--shingle-cap`/`--no-near-dup` against a fresh `vercel/next.js` clone for runtime. Exact numbers will vary by machine and by which books/corpus you use.

## Privacy scoring and PII detection

The Privacy dimension's *ratio thresholds* (what fraction of files with PII counts as "minor" vs. "critical") are author judgement. The *detection* underneath it, however, is not a guess — see [`forge_prep/pii.py`](../forge_prep/pii.py):

- **credit_card**: rejected unless it passes the Luhn/mod-10 checksum, is not a published test PAN (see below), is not a low-entropy/placeholder digit sequence, is a complete token (not a substring of a longer hex/GUID value), and — by default, disable with `--strict-pii` — is not sitting inside dense/vendored/minified content.
- **iban**: rejected unless it passes the ISO 13616 mod-97 checksum and matches the correct length for its country code (FR, DE, ES, IT, NL, BE, GB supported; other country codes are treated as non-matches rather than guessed at).
- **french_nir**: rejected unless the INSEE control key (`97 - (first 13 digits mod 97)`) matches, the month is plausible (01–12, or the INSEE special values 20/30/42–99), and the department code is plausible.
- **ip_address**: rejected if any octet exceeds 255, or if the match is actually part of a longer dotted version string (e.g. `10.2.14.3.1`). Whether private/loopback/link-local ranges count as PII at all is controlled by `--ip-mode` (default `public`, meaning they don't — see the code for the exact ranges via Python's `ipaddress` module).
- **ssn_us**: rejected if the digits are a low-entropy/placeholder sequence (see below), or if immediately preceded by a word from a denylist (`invoice`, `sku`, `order`, `ref`, `version`, `build`, `batch`, `id`) that indicates the number is actually an identifier, not personal data.
- **phone_intl**: rejected if immediately preceded by a denylist word, same list as above.
- **email**: matched by pattern only — this is deliberately not further validated, since the email pattern itself is already a strong, low-false-positive signal for this purpose.

### credit_card: fixed after failing on real data (0.1.1)

Luhn alone is weak — it rejects only ~90% of random digit strings, so across megabytes of minified JS and numeric constants, false positives are inevitable. Running the 0.1.0 wheel against a full clone of `vercel/next.js` (a real, large, representative open-source codebase — prose, code, config, and vendored/compiled bundles) surfaced 7 credit_card false positives, all in files still present in the repository as of this writing:

| Matched | What it really is | File |
|---|---|---|
| `4242424242424242` | Stripe's published test card | `examples/with-stripe-typescript/README.md` |
| `4000002760003184` | Stripe's published 3D-Secure test card | `examples/with-stripe-typescript/README.md` |
| `4503599627370496` | 2^52, a JS numeric constant | `packages/next/src/compiled/crypto-browserify/index.js` |
| `9007199254740992` | 2^53 (`Number.MAX_SAFE_INTEGER`+1) | `packages/next/src/compiled/crypto-browserify/index.js` |
| `0000000000000000` | a run of zeros in a padding array | (vendored/compiled content) |
| `00000000-0000-0000-…` | a fragment of a null GUID | (TypeScript model file) |

Four independent fixes address this, each targeting a different failure mode:

1. **Known test PAN denylist** (`KNOWN_TEST_CARD_NUMBERS` in `forge_prep/pii.py`) — published test card numbers from Stripe, Visa, Mastercard, Amex, Discover, PayPal sandbox, and Adyen documentation, reproduced constantly across real codebases. These are deliberately Luhn-valid (so they behave like real cards in a test environment), which is exactly why Luhn alone can't reject them.
2. **Low-entropy rejection** (`is_low_entropy_digits`) — fewer than 4 distinct digit values (covers runs of zeros and single-repeated-digit padding) or a strictly ascending/descending run with digit wraparound (catches sequences like `0123456789012345`). Zeros sum to zero under Luhn, so this class needed a separate check.
3. **Complete-token requirement** (`_breaks_complete_token`) — rejects a match immediately touching a hex letter (it's a substring of a longer hex value, not a standalone number) or touching a dash where the surrounding text is UUID/GUID-shaped. This is what catches the null-GUID case: the regex was matching *inside* a longer structured token rather than requiring the whole token to be a card number.
4. **Machine-content suppression** (default on, disable with `--strict-pii`) — a match is not reported if the ~200 characters around it have a whitespace ratio below 5% (minified JS, base64, source maps) or the file path contains `dist/`, `compiled/`, `vendor/`, `node_modules/`, `.min.js`, or `.map`.

**Re-verified against the same real corpus after the fix:** all 4 checks were run against the same next.js clone (19,306 files, 90.2 MB scanned). **`credit_card` hits: 0** — including with `--strict-pii` (machine-content suppression disabled), meaning checks 1–3 alone are sufficient for every false positive in this corpus; machine-content suppression is defense-in-depth for corpora this one didn't happen to exercise, not the layer actually doing the work here. Both of the two flagged files (`examples/with-stripe-typescript/README.md` and `packages/next/src/compiled/crypto-browserify/index.js`) now report `pii_detected: []`.

### Should low-entropy / complete-token rules apply to iban and french_nir too?

Considered and **not applied** — reported here per the request to show the reasoning rather than apply it blindly. `iban` (mod-97 checksum) and `french_nir` (INSEE control key) both already have a checksum with a false-accept rate around 1/97 (~1%) for a random digit string, roughly an order of magnitude stronger than Luhn's ~1/10. A low-entropy or sequential digit run essentially never satisfies either checksum by chance, so an additional entropy filter would reject a near-zero number of matches — not worth the added complexity or the (small but nonzero) risk of rejecting a real IBAN/NIR whose account or serial digits happen to look patterned. `ssn_us` has no checksum at all (SSNs aren't checksum-validated), so it was the one case where applying low-entropy rejection had real value — and real cost: it initially rejected a hand-picked benchmark "true positive" (`234-56-7890`) that turned out to be an accidental ascending run, which is exactly the kind of edge case this reasoning predicts. That benchmark entry was fixed rather than the detector loosened, because a perfectly sequential 9-digit run is astronomically unlikely to be a genuinely-assigned SSN and overwhelmingly likely to be a placeholder.

### Measured precision/recall

`tests/test_pii_precision.py` runs the detector against a 154-line hand-labeled benchmark (`tests/fixtures/pii_benchmark.jsonl` — 63 true positives spread across all 7 types, 91 hard negatives). The negative set now includes all 6 next.js false positives from the table above, plus 22 additional hard negatives drawn from realistic minified JS, source maps, UUIDs, git SHAs, and numeric constants, on top of the original order IDs, invoice numbers, version strings, and private/malformed IP addresses. As of this writing, measured performance is:

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

**The gap between this number and reality, stated plainly:** `credit_card` was *already* reported as 1.00/1.00 precision/recall against the synthetic benchmark before 0.1.1 — and it still produced 7 confirmed false positives on a real, unremarkable open-source codebase. A perfect synthetic-benchmark score is evidence the implemented logic behaves as designed against the cases the benchmark author thought to write, and nothing more. It is not evidence of real-world precision, and the next.js run is the reason this project now measures against a real corpus (see "Real-corpus benchmark" below) rather than treating the synthetic number as sufficient on its own. To reproduce or regenerate the benchmark, see `tests/fixtures/build_pii_benchmark.py`.

### Real-corpus benchmark (next.js)

In addition to the synthetic benchmark above, every release is checked against a full clone of `vercel/next.js` — a large (19,306 scanned files, 90.2 MB), real, non-adversarial corpus that nobody constructed to test this tool. Current numbers (measured for the 0.1.1 release):

| Metric | Value |
|---|---|
| Files scanned | 19,306 (30,931 present; 11,625 skipped for unsupported extension) |
| Runtime | ~21s |
| Forge Readiness Score | 59.0/100 (D) |
| Exact-duplicate rate | 16.4% (3,170 files) |
| PII detected | email: 220, ip_address: 11, **credit_card: 0** |

See `CHANGELOG.md` for these numbers on each release — a regression in any of them blocks the release.

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

# Limitations

Read this before relying on forge-prep as a privacy or compliance control. It is a **screening aid**, not a certification, and it does not substitute for review by your Data Protection Officer or legal counsel before you upload, share, or train on a corpus.

## What the PII detector does not catch

forge-prep's PII detector (`forge_prep/pii.py`) matches seven specific, structurally regular patterns: email addresses, international phone numbers, IP addresses, credit card numbers, US SSNs, IBANs (7 supported countries), and French NIRs. It validates each candidate match with a checksum or context check to reduce false positives (see [`docs/methodology.md`](methodology.md)), but the underlying approach is still pattern matching over structured formats — not general-purpose PII recognition. It does **not** detect:

- **Person names** appearing in prose (no named-entity recognition of any kind)
- **Postal addresses**
- **Dates of birth** written in prose (e.g. "born on the 3rd of March, 1985") — only the structured French NIR format encodes a birth date, and only when it appears in that exact numeric format
- **Employee IDs, customer IDs, or other organization-specific identifiers** that don't happen to look like one of the seven supported patterns
- **Health information** of any kind (diagnoses, medications, treatment notes)
- **Free-text identifiers** — anything that isn't already a structured, pattern-matchable format
- **Any PII inside scanned images, photos, or other binary/non-text formats** — forge-prep only reads the file formats listed in `SUPPORTED_EXTENSIONS` (`forge_prep/auditor.py`) as text; it does not do OCR, image analysis, or binary parsing
- **PII types outside these seven categories entirely** — e.g. passport numbers, driver's license numbers, tax IDs other than French NIR, national ID numbers for countries other than the ones listed, IBANs for countries outside the 7 supported

If your corpus is likely to contain any of the above, forge-prep's Privacy score and PII counts will understate your actual exposure — a clean scan is not evidence of a clean corpus.

## Known false positive classes

These are patterns that *used* to produce false positives, confirmed by running against a real, non-adversarial corpus (a full clone of `vercel/next.js`) — not hypothetical edge cases. As of 0.1.1 they're all suppressed by default; understanding what's being filtered out matters if you're deciding whether to trust a "no PII found" result.

- **Published test card numbers** (Stripe, Visa, Mastercard, Amex, Discover, PayPal, Adyen documentation) — e.g. `4242424242424242`. These appear constantly in READMEs, integration tests, and example `.env` files, and are deliberately Luhn-valid so they behave like real cards in a sandbox. If your corpus contains a *real* card number that happens to collide with a published test number (astronomically unlikely, since real PANs are issuer-assigned), it will not be flagged.
- **Low-entropy digit runs** — fewer than 4 distinct digits (padding, zero-runs) or a strictly ascending/descending sequence (`0123456789012345`). This also applies to `ssn_us`. A genuinely-assigned SSN or PAN that happens to be perfectly sequential (vanishingly unlikely) would not be flagged.
- **Numbers embedded in a longer hex/UUID/GUID value** — a 16-digit run inside a 32-character hex string or inside a UUID (e.g. the digits inside `00000000-0000-0000-0000-000000000000`) is not treated as a standalone card number.
- **Dense, whitespace-free content** (minified JS, base64 blobs, source maps) and files under `dist/`, `compiled/`, `vendor/`, `node_modules/`, `*.min.js`, or `*.map` — credit-card matches from these are suppressed by default. Use `--strict-pii` to disable this and see everything, including matches you'll then need to triage yourself. On the next.js benchmark corpus, disabling this suppression did not change the credit-card count (still zero) — the denylist, entropy, and token-boundary checks did the actual work there, but a different corpus could plausibly hit this suppression layer where the others don't.

None of these suppression rules are applied to `iban` or `french_nir` — their checksums (mod-97, INSEE control key) already have a false-accept rate around 1% for random digit strings, an order of magnitude stronger than Luhn, so the same classes of false positive are far less likely to occur there in the first place. See [`docs/methodology.md`](methodology.md) for the full reasoning and the real-corpus numbers.

## Near-duplicate detection limitations

The precision figure reported in `docs/methodology.md` for near-duplicate detection (0.933–1.000 depending on shingle cap) was measured against negative pairs drawn from **different books** — i.e. unrelated documents with essentially no real content overlap. That's an easy negative case. **Hard negatives were not tested**: different chapters of the same work, different documents generated from a shared template, or different records that share a lot of common boilerplate but are legitimately distinct. Those are exactly the cases where MinHash/LSH is most likely to produce a false positive in practice, and real-world precision on a corpus containing them may be meaningfully lower than the reported figure. Treat the measured precision as an upper bound from an easy test set, not a guarantee.

Near-duplicate detection also has a practical corpus-size ceiling that hasn't been tested past. It holds a 128-integer MinHash signature per file in memory for the whole run, on top of the existing per-file audit records, and the entire audit — including near-dup — is single-threaded. The only corpus this has been measured against is a full clone of `vercel/next.js`: 19,308 files, 90MB, 53s. Corpora an order of magnitude larger (hundreds of thousands of files, gigabytes of text) are untested and may exhaust memory or take proportionally longer — there's no data either way. Streaming file reads and parallelism are planned (see the roadmap) but not implemented; until they are, treat this as validated at "one large real-world repository" scale, not "enterprise-wide corpus" scale.

## Redaction is pseudonymisation, not anonymisation

`forge-prep clean` replaces detected PII with typed placeholders (`[EMAIL_REDACTED]`, `[SSN_REDACTED]`, etc.). This is **pseudonymisation**, not anonymisation, under the GDPR's definitions (Art. 4(5); Recital 26):

- The replacement is reversible in principle if the original file is retained elsewhere or if the surrounding context still uniquely identifies a person even with the direct identifier removed (e.g. "the CFO of [company], reachable at [EMAIL_REDACTED]" may still identify a specific individual from context).
- Cleaned output is very likely to still be **personal data** under GDPR and similar regimes, and processing it (including training a model on it) still requires a lawful basis — redaction by this tool does not remove that requirement.
- No guarantee is made that *all* PII has been removed — see the detection gaps above. A file with zero PII flags after cleaning may still contain PII that this tool doesn't look for.

## This is a screening aid, not a compliance sign-off

Use forge-prep's audit and clean commands to triage a corpus quickly and get a rough sense of scale before deeper review — not as the final word on whether a corpus is safe to use. For anything with real compliance stakes (training data with customer information, regulated data, cross-border transfers), get sign-off from your DPO or legal counsel using their own review process, not this tool's report alone.

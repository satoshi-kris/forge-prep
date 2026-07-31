# Changelog

All notable changes to forge-prep will be documented in this file.

## [0.1.0] — 2026-04-02

### Added
- `forge-prep audit` command — scans a corpus directory and produces a Forge Readiness Score (0–100)
- `forge-prep clean` command — deduplicates, scrubs PII, and filters low-quality files
- Six scoring dimensions: Volume, Quality, Deduplication, Privacy, Language Focus, Format Consistency
- PII detection for: email, phone (international), IP address, credit card, US SSN, IBAN, French NIR
- Language detection heuristics for English, French, German, Spanish
- Markdown and JSON report generation
- Interactive React dashboard for visual exploration
- 38 unit tests with full pipeline coverage
- Zero external dependencies for core functionality
- Sample enterprise corpus for demo and testing

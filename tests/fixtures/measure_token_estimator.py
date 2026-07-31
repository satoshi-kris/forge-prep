"""
One-off measurement script — not part of the test suite, not run in CI.

Fits per-extension-group token multipliers on examples/sample-corpus/, then
evaluates them on a genuinely held-out set (tests/fixtures/token_holdout/ plus
a fixed list of this repo's own files never used for fitting) to report
generalization error, not the in-sample identity you get from fitting and
evaluating on the same files.

Requires `tiktoken` (pip install tiktoken) — not a project dependency, used
only for this offline measurement. Run manually with:
    python3 tests/fixtures/measure_token_estimator.py
"""

import statistics as st
from collections import defaultdict
from pathlib import Path

import tiktoken

REPO_ROOT = Path(__file__).parent.parent.parent
ENC = tiktoken.get_encoding("cl100k_base")

GROUPS = {
    ".txt": "prose", ".md": "prose",
    ".py": "code",
    ".csv": "structured", ".jsonl": "structured", ".json": "structured",
}

# --- Fitting corpus: examples/sample-corpus/ (unchanged from prior measurements) ---
FIT_DIR = REPO_ROOT / "examples" / "sample-corpus"

# --- Held-out corpus: NOT in examples/sample-corpus/, spans prose/code/csv/jsonl/markdown ---
HOLDOUT_DIR = REPO_ROOT / "tests" / "fixtures" / "token_holdout"
HOLDOUT_FILES = [
    # prose (fetched public-domain text + the Apache LICENSE boilerplate)
    HOLDOUT_DIR / "declaration_of_independence.txt",
    HOLDOUT_DIR / "us_constitution.txt",
    HOLDOUT_DIR / "apache_license.txt",
    # markdown (this repo's own docs, none of which were in the fitting corpus)
    REPO_ROOT / "README.md",
    REPO_ROOT / "CHANGELOG.md",
    REPO_ROOT / "docs" / "methodology.md",
    REPO_ROOT / "docs" / "limitations.md",
    REPO_ROOT / "RELEASING.md",
    REPO_ROOT / "CONTRIBUTING.md",
    # code (this repo's own package source, distinct from the one code file used for fitting)
    REPO_ROOT / "forge_prep" / "__init__.py",
    REPO_ROOT / "forge_prep" / "_version.py",
    REPO_ROOT / "forge_prep" / "auditor.py",
    REPO_ROOT / "forge_prep" / "cleaner.py",
    REPO_ROOT / "forge_prep" / "cli.py",
    REPO_ROOT / "forge_prep" / "pii.py",
    REPO_ROOT / "forge_prep" / "report.py",
    REPO_ROOT / "forge_prep" / "scorer.py",
    # csv/jsonl (synthetic, realistic-shaped: wide vs narrow, numeric vs long-text)
    HOLDOUT_DIR / "wide_numeric.csv",
    HOLDOUT_DIR / "narrow_text.csv",
    HOLDOUT_DIR / "events_numeric.jsonl",
    HOLDOUT_DIR / "reviews_text.jsonl",
]


def measure(files):
    """Return {ext: [(path, words, chars, real_tokens), ...]}."""
    by_ext = defaultdict(list)
    for fpath in files:
        text = fpath.read_text(encoding="utf-8", errors="replace")
        words = text.split()
        real = len(ENC.encode(text))
        by_ext[fpath.suffix.lower()].append((str(fpath.relative_to(REPO_ROOT)), len(words), len(text), real))
    return by_ext


def cv(vals):
    if len(vals) < 2:
        return None
    m = st.mean(vals)
    return (st.pstdev(vals) / m) if m else 0.0


def fit_multipliers(fit_by_ext):
    """Weighted (sum/sum) chars-per-token and words-per-token, grouped by GROUPS."""
    group_chars = defaultdict(int)
    group_tokens = defaultdict(int)
    group_words = defaultdict(int)
    total_chars = total_tokens = 0
    for ext, rows in fit_by_ext.items():
        group = GROUPS.get(ext)
        if group is None:
            continue
        for _, words, chars, real in rows:
            group_chars[group] += chars
            group_tokens[group] += real
            group_words[group] += words
            total_chars += chars
            total_tokens += real

    chars_per_token = {g: group_chars[g] / group_tokens[g] for g in group_chars}
    words_per_token = {g: group_words[g] / group_tokens[g] for g in group_words}
    default_cpt = total_chars / total_tokens
    default_wpt = sum(group_words.values()) / total_tokens
    return chars_per_token, words_per_token, default_cpt, default_wpt


def main():
    fit_by_ext = measure(sorted(p for p in FIT_DIR.rglob("*") if p.is_file() and p.suffix.lower() in GROUPS))
    holdout_by_ext = measure(HOLDOUT_FILES)

    n_holdout = sum(len(rows) for rows in holdout_by_ext.values())
    print(f"Held-out set: {n_holdout} files across {len(holdout_by_ext)} extensions "
          f"({', '.join(sorted(holdout_by_ext))})\n")
    assert n_holdout >= 20, f"held-out set has only {n_holdout} files, need >= 20"

    print("=" * 100)
    print("STEP 1 — stability comparison: words-per-token vs chars-per-token, measured on the FIT corpus")
    print("=" * 100)
    fit_group_rows = defaultdict(list)
    for ext, rows in fit_by_ext.items():
        group = GROUPS.get(ext)
        if group:
            fit_group_rows[group].extend(rows)

    for group, rows in sorted(fit_group_rows.items()):
        wpt = [w / r for _, w, c, r in rows if r]
        cpt = [c / r for _, w, c, r in rows if r]
        cv_wpt, cv_cpt = cv(wpt), cv(cpt)
        cv_wpt_s = f"{cv_wpt:.3f}" if cv_wpt is not None else "n/a (n=1)"
        cv_cpt_s = f"{cv_cpt:.3f}" if cv_cpt is not None else "n/a (n=1)"
        print(f"  {group:11s} n={len(rows):2d}  CV(words/token)={cv_wpt_s:>10s}   CV(chars/token)={cv_cpt_s:>10s}")

    chars_per_token, words_per_token, default_cpt, default_wpt = fit_multipliers(fit_by_ext)
    print()
    print("Fitted chars_per_token (weighted, sum(chars)/sum(tokens)) on the FIT corpus:")
    for g, v in sorted(chars_per_token.items()):
        print(f"  {g:11s} {v:.3f}")
    print(f"  {'default':11s} {default_cpt:.3f}")

    print()
    print("=" * 100)
    print("STEP 2 — held-out evaluation: char-based estimator applied to files NEVER used for fitting")
    print("=" * 100)
    print(f"{'file':55s} {'ext':8s} {'group':11s} {'chars':>7s} {'real':>6s} {'est(char)':>10s} {'err':>8s}")

    group_errors = defaultdict(list)
    ext_errors = defaultdict(list)
    for ext, rows in sorted(holdout_by_ext.items()):
        group = GROUPS.get(ext, "unmapped")
        mult = chars_per_token.get(group, default_cpt)
        for path, words, chars, real in rows:
            est = int(chars / mult)
            err = (est - real) / real * 100 if real else 0
            group_errors[group].append(err)
            ext_errors[ext].append(err)
            print(f"{path:55s} {ext:8s} {group:11s} {chars:7d} {real:6d} {est:10d} {err:+7.1f}%")

    print()
    print("Held-out mean absolute error, by group (this is the number that goes in the docs):")
    for g, errs in sorted(group_errors.items()):
        mae = sum(abs(e) for e in errs) / len(errs)
        print(f"  {g:11s} n={len(errs):2d}  mean_abs_error={mae:5.1f}%   min={min(errs):+.1f}%  max={max(errs):+.1f}%")

    print()
    print("Held-out mean absolute error, by extension:")
    for ext, errs in sorted(ext_errors.items()):
        mae = sum(abs(e) for e in errs) / len(errs)
        print(f"  {ext:8s} n={len(errs):2d}  mean_abs_error={mae:5.1f}%   min={min(errs):+.1f}%  max={max(errs):+.1f}%")


if __name__ == "__main__":
    main()

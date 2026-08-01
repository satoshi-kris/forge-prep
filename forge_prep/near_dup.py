"""
Near-duplicate detection — MinHash + LSH over word 5-gram shingles, pure
stdlib (no numpy/scipy/datasketch). Exact-hash dedup (forge_prep/auditor.py)
only catches byte-identical files; real enterprise corpora are full of
documents that differ by a header, footer, date stamp, or boilerplate
disclaimer, which exact hashing misses entirely.

Algorithm, matching the standard MinHash/LSH formulation:
  1. Shingle each document into word 5-grams after whitespace
     normalization and case folding.
  2. Hash each shingle once (blake2b, truncated to 64 bits) and run it
     through NUM_PERM independent universal hash functions, keeping the
     minimum per function — the MinHash signature. Two documents' Jaccard
     similarity is estimated by the fraction of signature positions where
     they agree.
  3. Band the signature into NUM_BANDS bands of ROWS_PER_BAND rows each.
     Documents sharing an identical band in *any* band are LSH candidates.
     This is what makes near-dup detection sub-quadratic — we never
     compute pairwise similarity for the full O(n^2) file pairs, only for
     candidates that already share a band.
  4. Candidates are confirmed with the actual estimated Jaccard from their
     full signatures (not just the one matching band) before being
     unioned into a cluster, via union-find — so bands generate candidates
     but don't decide cluster membership themselves.
"""

import heapq
import random
from collections import defaultdict
from hashlib import blake2b

NUM_PERM = 128
NUM_BANDS = 16
ROWS_PER_BAND = 8  # NUM_BANDS * ROWS_PER_BAND == NUM_PERM
assert NUM_BANDS * ROWS_PER_BAND == NUM_PERM

_MASK64 = (1 << 64) - 1
DEFAULT_THRESHOLD = 0.85
_PERMUTATION_SEED = 1337  # fixed so signatures are reproducible across runs

# The expensive step is O(len(shingles) * NUM_PERM). A handful of large,
# highly-shingled files (compiled bundles, generated manifests) can
# dominate runtime disproportionately — measured 1.36s for a single
# 62,855-shingle 2.5MB file. Bounding the shingle count with a
# deterministic "bottom-k" sketch (keep the K shingles with the smallest
# base hash) caps worst-case per-file cost regardless of document size,
# without depending on shingle iteration order.
#
# This value was tuned twice. The first pass (150) chased a 2x-runtime
# target and was shipped without measuring the accuracy cost, because the
# test suite's fixtures are all too small to ever engage the cap. Measured
# against 35 (original, variant) pairs built from real >200KB documents
# with known Jaccard (tests/fixtures/measure_shingle_cap.py), 150 measured
# 0.929 recall / 0.929 precision and missed 12 of 614 real near-duplicate
# files on a full vercel/next.js clone. 2000 measured 1.000 recall / 0.933
# precision on the same test, at 2.76x baseline runtime (53s vs. 19s on
# that corpus) — worse than the original 2x target, but a corpus audit
# runs once per corpus update, not in a hot loop, and missing real
# duplicates is worse than an extra 34 seconds. See docs/methodology.md
# for the full cap/recall/precision/runtime table and the honest caveats
# (small sample, MinHash sampling noise near the threshold). Override with
# --shingle-cap (0 = uncapped) if your corpus needs different tuning.
MAX_SHINGLES = 2000


def _make_permutations(num_perm: int, seed: int) -> list:
    # (a odd, b) pairs for a fast multiplicative hash: ((a*h) ^ b) & MASK64.
    # This is not a strict 2-universal hash family the way (a*h+b) mod
    # prime is, but it's a standard, fast multiplicative-hashing technique
    # (Knuth) and is more than sufficient for MinHash's purpose — the
    # observable effect is on estimate_jaccard's variance, not correctness,
    # and it's what makes 128 permutations affordable in pure Python. See
    # docs/methodology.md for the measured performance impact.
    rng = random.Random(seed)
    return [(rng.randrange(1, _MASK64) | 1, rng.randrange(0, _MASK64)) for _ in range(num_perm)]


_PERMUTATIONS = _make_permutations(NUM_PERM, _PERMUTATION_SEED)


def shingle_text(text: str, k: int = 5) -> set:
    """Word k-gram shingles after whitespace normalization and case folding."""
    words = text.lower().split()
    if len(words) < k:
        return {" ".join(words)} if words else set()
    return {" ".join(words[i:i + k]) for i in range(len(words) - k + 1)}


def _shingle_hash(shingle: str) -> int:
    return int.from_bytes(blake2b(shingle.encode("utf-8"), digest_size=8).digest(), "big")


def _bounded_shingle_hashes(shingles: set, max_shingles: int = MAX_SHINGLES) -> list:
    """Base hash of every shingle, capped to max_shingles via a deterministic
    bottom-k sketch (smallest hash values) when there are more than that.
    max_shingles <= 0 means uncapped."""
    hashes = [_shingle_hash(s) for s in shingles]
    if max_shingles <= 0 or len(hashes) <= max_shingles:
        return hashes
    return heapq.nsmallest(max_shingles, hashes)


def minhash_signature(shingles: set, num_perm: int = NUM_PERM, max_shingles: int = MAX_SHINGLES) -> list:
    """MinHash signature: num_perm ints, one minimum per fast multiplicative hash."""
    hashes = _bounded_shingle_hashes(shingles, max_shingles)
    if not hashes:
        return [_MASK64] * num_perm
    permutations = _PERMUTATIONS if num_perm == NUM_PERM else _make_permutations(num_perm, _PERMUTATION_SEED)
    mask = _MASK64
    # min() over a generator, once per permutation, is measurably faster
    # than a manual per-shingle inner loop with an explicit comparison —
    # both are O(len(hashes) * num_perm), but this leans on min()'s C
    # implementation instead of Python-level branching per element.
    return [min(((a * h) ^ b) & mask for h in hashes) for a, b in permutations]


def estimate_jaccard(sig_a: list, sig_b: list) -> float:
    """Estimated Jaccard similarity: fraction of MinHash signature positions that agree."""
    if not sig_a or not sig_b or len(sig_a) != len(sig_b):
        return 0.0
    matches = sum(1 for x, y in zip(sig_a, sig_b) if x == y)
    return matches / len(sig_a)


class _UnionFind:
    def __init__(self, keys):
        self.parent = {k: k for k in keys}

    def find(self, k):
        while self.parent[k] != k:
            self.parent[k] = self.parent[self.parent[k]]
            k = self.parent[k]
        return k

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


def find_clusters(
    signatures: dict,
    threshold: float = DEFAULT_THRESHOLD,
    num_bands: int = NUM_BANDS,
    rows_per_band: int = ROWS_PER_BAND,
) -> list:
    """
    Given {key: minhash_signature}, return clusters of keys estimated to be
    near-duplicates of each other (Jaccard >= threshold), as a list of
    lists. Only clusters with 2+ members are returned — singletons are
    dropped. Each cluster is sorted for determinism; clusters themselves
    are sorted by their first (smallest) member.
    """
    buckets = defaultdict(list)
    for key, sig in signatures.items():
        for band_idx in range(num_bands):
            start = band_idx * rows_per_band
            band = tuple(sig[start:start + rows_per_band])
            buckets[(band_idx, band)].append(key)

    uf = _UnionFind(signatures.keys())
    checked_pairs = set()
    for bucket_keys in buckets.values():
        if len(bucket_keys) < 2:
            continue
        for i in range(len(bucket_keys)):
            for j in range(i + 1, len(bucket_keys)):
                a, b = bucket_keys[i], bucket_keys[j]
                pair = (a, b) if a < b else (b, a)
                if pair in checked_pairs:
                    continue
                checked_pairs.add(pair)
                if estimate_jaccard(signatures[a], signatures[b]) >= threshold:
                    uf.union(a, b)

    groups = defaultdict(list)
    for key in signatures:
        groups[uf.find(key)].append(key)

    clusters = [sorted(members) for members in groups.values() if len(members) > 1]
    clusters.sort(key=lambda c: c[0])
    return clusters

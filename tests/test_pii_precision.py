"""
Measures precision/recall of forge_prep.pii.scan_text against a hand-labeled
benchmark (tests/fixtures/pii_benchmark.jsonl) and fails the build if either
drops below the thresholds promised in docs/methodology.md.

Each row is scored independently: for a positive row, a hit is the expected
type appearing anywhere in the full scan of the text (not just the row's
own "near-miss" negatives) so a false positive triggered by unrelated text
elsewhere in the benchmark is still counted against precision.
"""

import json
import unittest
from collections import defaultdict
from pathlib import Path

from forge_prep import pii

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "pii_benchmark.jsonl"
MIN_PRECISION = 0.90
MIN_RECALL = 0.85


def load_benchmark():
    rows = []
    with open(FIXTURE_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


class TestPIIPrecisionRecall(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = load_benchmark()
        cls.stats = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0})

        for row in cls.rows:
            detected = pii.scan_text(row["text"])
            expected_type = row["expected_type"]
            hit = expected_type in detected

            if row["label"]:
                if expected_type == "none":
                    continue  # not a real category
                if hit:
                    cls.stats[expected_type]["tp"] += 1
                else:
                    cls.stats[expected_type]["fn"] += 1
            else:
                if expected_type != "none" and hit:
                    cls.stats[expected_type]["fp"] += 1
                # any detection at all on a negative row is a false positive,
                # even for a type this row wasn't specifically designed to probe
                for detected_type in detected:
                    if detected_type != expected_type:
                        cls.stats[detected_type]["fp"] += 1

    def test_benchmark_has_minimum_size(self):
        self.assertGreaterEqual(len(self.rows), 120)
        positives = sum(1 for r in self.rows if r["label"])
        negatives = len(self.rows) - positives
        self.assertGreaterEqual(positives, 50)
        self.assertGreaterEqual(negatives, 50)

    def test_all_seven_types_represented(self):
        types = {r["expected_type"] for r in self.rows if r["label"]}
        self.assertEqual(
            types,
            {"email", "phone_intl", "ip_address", "credit_card", "ssn_us", "iban", "french_nir"},
        )

    def test_overall_precision_and_recall_meet_thresholds(self):
        total_tp = sum(s["tp"] for s in self.stats.values())
        total_fp = sum(s["fp"] for s in self.stats.values())
        total_fn = sum(s["fn"] for s in self.stats.values())

        precision = total_tp / (total_tp + total_fp) if (total_tp + total_fp) else 1.0
        recall = total_tp / (total_tp + total_fn) if (total_tp + total_fn) else 1.0

        self.assertGreaterEqual(
            precision, MIN_PRECISION,
            f"Overall precision {precision:.3f} is below the {MIN_PRECISION} threshold",
        )
        self.assertGreaterEqual(
            recall, MIN_RECALL,
            f"Overall recall {recall:.3f} is below the {MIN_RECALL} threshold",
        )

    def test_per_type_precision_and_recall_meet_thresholds(self):
        failures = []
        for pii_type, s in sorted(self.stats.items()):
            tp, fp, fn = s["tp"], s["fp"], s["fn"]
            precision = tp / (tp + fp) if (tp + fp) else 1.0
            recall = tp / (tp + fn) if (tp + fn) else 1.0
            if precision < MIN_PRECISION or recall < MIN_RECALL:
                failures.append(f"{pii_type}: precision={precision:.2f} recall={recall:.2f} (tp={tp} fp={fp} fn={fn})")
        self.assertEqual(failures, [], "\n".join(failures))


if __name__ == "__main__":
    unittest.main()

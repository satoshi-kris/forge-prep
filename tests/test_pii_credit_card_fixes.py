"""
Unit tests for the 0.1.1 credit_card false-positive fixes, verified against
a real clone of vercel/next.js: known test PANs, low-entropy digit runs,
incomplete tokens (matches inside a longer hex/GUID string), and
machine-content suppression with the --strict-pii opt-out.
"""

import tempfile
import unittest
from pathlib import Path

from forge_prep import pii


class TestKnownTestCardDenylist(unittest.TestCase):
    def test_stripe_test_card_is_rejected(self):
        text = "Use test card 4242 4242 4242 4242 to simulate a successful charge."
        self.assertNotIn("credit_card", pii.scan_text(text))

    def test_stripe_3ds_test_card_is_rejected(self):
        text = "For 3D Secure testing, use 4000 0027 6000 3184 which requires authentication."
        self.assertNotIn("credit_card", pii.scan_text(text))

    def test_known_test_card_rejected_even_without_spaces(self):
        self.assertFalse(pii.validate_credit_card("4242424242424242", "4242424242424242", 0, 16))

    def test_real_looking_card_not_in_denylist_still_passes(self):
        # Luhn-valid, not a published test PAN, not low-entropy.
        self.assertTrue(pii.validate_credit_card("6011998877665546", "x 6011998877665546 x", 2, 18))


class TestLowEntropyDigits(unittest.TestCase):
    def test_run_of_zeros_is_low_entropy(self):
        self.assertTrue(pii.is_low_entropy_digits("0000000000000000"))

    def test_single_repeated_digit_is_low_entropy(self):
        self.assertTrue(pii.is_low_entropy_digits("1111111111111111"))

    def test_ascending_run_is_low_entropy(self):
        self.assertTrue(pii.is_low_entropy_digits("0123456789012345"))

    def test_descending_run_is_low_entropy(self):
        self.assertTrue(pii.is_low_entropy_digits("9876543210987654"))

    def test_fewer_than_four_distinct_values_is_low_entropy(self):
        self.assertTrue(pii.is_low_entropy_digits("1212121212121212"))  # only {1, 2}

    def test_realistic_card_number_is_not_low_entropy(self):
        self.assertFalse(pii.is_low_entropy_digits("6011998877665546"))

    def test_zeros_run_rejected_end_to_end(self):
        text = "const PADDING = [0000000000000000, 0000000000000000];"
        self.assertNotIn("credit_card", pii.scan_text(text))

    def test_js_numeric_constant_2_53_rejected(self):
        text = "const MAX_SAFE_INT_PLUS_ONE = 9007199254740992; // Number.MAX_SAFE_INTEGER + 1"
        self.assertNotIn("credit_card", pii.scan_text(text))

    def test_js_numeric_constant_2_52_rejected(self):
        text = "var LARGEST_SAFE_DOUBLE = 4503599627370496; // 2^52"
        self.assertNotIn("credit_card", pii.scan_text(text))


class TestCompleteTokenBoundary(unittest.TestCase):
    def test_null_guid_does_not_flag_credit_card(self):
        text = "const NULL_ID = '00000000-0000-0000-0000-000000000000';"
        self.assertNotIn("credit_card", pii.scan_text(text))

    def test_real_uuid_does_not_flag_credit_card(self):
        text = "session guid 6fa459ea-ee8a-3ca4-894e-db77e160355e was logged"
        self.assertNotIn("credit_card", pii.scan_text(text))

    def test_digits_adjacent_to_hex_letter_rejected(self):
        # A 16-digit run immediately touching a hex letter is part of a
        # longer hex token, not a standalone card number.
        self.assertTrue(pii._breaks_complete_token("xa1234567890123456bx", 2, 18))

    def test_standalone_card_number_is_a_complete_token(self):
        self.assertFalse(pii._breaks_complete_token(" 1234567890123456 ", 1, 17))


class TestMachineContentSuppression(unittest.TestCase):
    def test_dense_minified_context_suppresses_credit_card(self):
        # A Luhn-valid, non-denylisted, non-low-entropy 16-digit run
        # embedded in dense, whitespace-free content.
        card = "6011998877665546"
        minified = "a=1;b=2;c=3;" * 10 + card + ";d=4;e=5;f=6;" * 10
        found = pii.scan_text(minified)
        self.assertNotIn("credit_card", found)

    def test_strict_pii_disables_machine_content_suppression(self):
        card = "6011998877665546"
        minified = "a=1;b=2;c=3;" * 10 + card + ";d=4;e=5;f=6;" * 10
        found = pii.scan_text(minified, strict_pii=True)
        self.assertIn("credit_card", found)

    def test_vendored_path_suppresses_credit_card(self):
        card = "6011998877665546"
        text = f"This document mentions the number {card} in normal prose with plenty of whitespace."
        found = pii.scan_text(text, file_path="project/dist/bundle.min.js")
        self.assertNotIn("credit_card", found)

    def test_strict_pii_disables_vendored_path_suppression(self):
        card = "6011998877665546"
        text = f"This document mentions the number {card} in normal prose with plenty of whitespace."
        found = pii.scan_text(text, file_path="project/dist/bundle.min.js", strict_pii=True)
        self.assertIn("credit_card", found)

    def test_normal_prose_card_is_still_detected_by_default(self):
        card = "6011998877665546"
        text = f"The card on file is {card}, charged for the annual renewal of the enterprise plan."
        found = pii.scan_text(text)
        self.assertIn("credit_card", found)

    def test_scan_file_chunked_applies_path_suppression(self):
        tmpdir = tempfile.mkdtemp()
        try:
            vendored = Path(tmpdir) / "vendor"
            vendored.mkdir()
            fpath = vendored / "bundle.js"
            card = "6011998877665546"
            fpath.write_text(
                f"This document mentions the number {card} in normal prose with plenty of whitespace.",
                encoding="utf-8",
            )
            found_types, _ = pii.scan_file_chunked(fpath)
            self.assertNotIn("credit_card", found_types)

            found_types_strict, _ = pii.scan_file_chunked(fpath, strict_pii=True)
            self.assertIn("credit_card", found_types_strict)
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()

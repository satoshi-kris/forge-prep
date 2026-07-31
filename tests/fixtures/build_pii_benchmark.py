"""
One-off generator for pii_benchmark.jsonl — not part of the test suite.

Regenerates the hand-labeled PII benchmark used by tests/test_pii_precision.py.
Credit card numbers, IBANs, and French NIRs are generated with correct
checksums so their "true positive" label is independently verifiable
against the published checksum algorithms (Luhn / ISO 13616 mod-97 /
INSEE control key) rather than against this codebase's behavior.

Run manually with: python3 tests/fixtures/build_pii_benchmark.py
"""

import json
import random
from pathlib import Path


def luhn_valid_number(prefix: str) -> str:
    for check in range(10):
        candidate = prefix + str(check)
        total = 0
        parity = len(candidate) % 2
        for i, ch in enumerate(candidate):
            d = int(ch)
            if i % 2 == parity:
                d *= 2
                if d > 9:
                    d -= 9
            total += d
        if total % 10 == 0:
            return candidate
    raise AssertionError("no valid check digit found")


def make_nir(sex, yy, mm, dept, commune, order):
    first13 = f"{sex}{yy:02d}{mm:02d}{dept:02d}{commune:03d}{order:03d}"
    key = 97 - (int(first13) % 97)
    return first13 + f"{key:02d}"


def iban_check_digits(country, bban):
    rearranged = bban + country + "00"
    numeric = "".join(str(int(ch, 36)) for ch in rearranged)
    remainder = int(numeric) % 97
    return f"{98 - remainder:02d}"


def make_iban(country, total_len, seed):
    rng = random.Random(seed)
    bban = "".join(rng.choice("0123456789") for _ in range(total_len - 4))
    check = iban_check_digits(country, bban)
    return f"{country}{check}{bban}"


def spaced_nir(n):
    return f"{n[0]} {n[1:3]} {n[3:5]} {n[5:7]} {n[7:10]} {n[10:13]} {n[13:15]}"


def main():
    rows = []

    def pos(text, t):
        rows.append({"text": text, "expected_type": t, "label": True})

    def neg(text, t):
        rows.append({"text": text, "expected_type": t, "label": False})

    # --- email: true positives ---
    for text in [
        "Please contact sarah.jones@northwind-consulting.com for a quote.",
        "Reach the helpdesk at it.support@globex-corp.io any time.",
        "New hire onboarding: send documents to hr.intake@meridian-labs.co.uk",
        "For billing questions, write finance.ap@acme-industries.com.",
        "The vendor's contact is m.tanaka@fujimoto-trading.jp.",
        "Escalate outages to oncall+sev1@cloudforge.dev immediately.",
        "Marketing list signups go through newsletter@brightpath.org.",
        "Legal correspondence: counsel@sterling-partners.law",
        "Send the signed NDA to procurement@delta-manufacturing.com",
    ]:
        pos(text, "email")

    # --- email: hard negatives (no @+domain.tld shape at all) ---
    for text in [
        "@channel please review the PR before end of day",
        "cc finance-team, no direct contact needed for this one",
        "the report references section 4.2.1, not an external address",
        "ticket number 4521 needs review by the on-call engineer",
        "the config key is service.retry@attempt in the yaml file",
    ]:
        neg(text, "email")

    # --- phone_intl: true positives ---
    for text in [
        "Call our Paris office at +33 1 42 68 53 00 for support.",
        "Reach Berlin reception on +49 30 2574 1180 weekdays.",
        "US sales line: +1 415 555 0148, available 9-5 PT.",
        "London desk: +44 20 7946 0958, ask for Priya.",
        "Madrid support: +34 91 123 45 67 during business hours.",
        "Emergency contact for the Milan site: +39 02 8734 5521.",
        "Amsterdam ops: +31 20 555 0199, escalate P1 only.",
        "Zurich compliance: +41 44 123 88 99.",
        "Toronto helpdesk: +1 416 555 0199 for Canadian clients.",
    ]:
        pos(text, "phone_intl")

    # --- phone_intl: hard negatives (denylisted context) ---
    for text in [
        "order +1 415 555 0148 confirmed for pickup",
        "version +33 1 42 68 53 00 deprecated in favor of v2",
        "build +49 30 2574 1180 tagged for staging",
        "batch +44 20 7946 0958 reprocessed overnight",
        "ref +34 91 123 45 67 closed without resolution",
        "sku +39 02 8734 5521 discontinued this quarter",
    ]:
        neg(text, "phone_intl")

    # --- ip_address: true positives (genuinely public addresses) ---
    for ip in [
        "8.8.8.8", "1.1.1.1", "93.184.216.34", "172.217.14.206",
        "104.16.132.229", "13.107.42.14", "140.82.112.3", "34.102.136.180", "151.101.1.140",
    ]:
        pos(f"connection observed from {ip} in the access log", "ip_address")

    # --- ip_address: hard negatives ---
    for text in [
        "internal server at 192.168.1.100 handles traffic",
        "the batch job runs on 10.0.0.5 every night",
        "bound to loopback address 127.0.0.1 for local testing",
        "the router advertises link-local address 169.254.0.5",
        "internal subnet uses 172.31.255.1 for the gateway",
        "malformed entry 999.1.2.3 was dropped by the parser",
        "malformed entry 300.168.1.1 was dropped by the parser",
        "running build 8.1.2.3.4 tonight",
        "upgrade to version 10.2.14.3.1 recommended",
    ]:
        neg(text, "ip_address")

    # --- credit_card: true positives (Luhn-valid, NOT a published test PAN,
    # NOT low-entropy — a real cardholder PAN looks like these, not like
    # 4111111111111111, which is Stripe/Visa's own documented test card and
    # is correctly rejected by the KNOWN_TEST_CARD_NUMBERS denylist) ---
    for prefix in [
        "601100000000000", "453211223344556", "453298761234567", "601160116011601",
        "441199223344556", "453277881122334", "601199887766554", "453266778899001", "400511223344556",
    ]:
        card = luhn_valid_number(prefix)
        formatted = " ".join(card[i:i + 4] for i in range(0, 16, 4))
        pos(f"The card on file is {formatted}, charged for the annual renewal.", "credit_card")

    # --- credit_card: hard negatives (Luhn-invalid order/invoice/tracking numbers) ---
    for text in [
        "order id 4532 1122 3344 5566",
        "invoice number 1234 5678 9012 3456",
        "tracking code 9999 8888 7777 6665",
        "reference 1111 2222 3333 4441",
        "batch 4000 1234 5678 9010",
        "ticket 5000 6000 7000 8000",
        "confirmation 2020 2021 2022 2023",
        "po number 3141 5926 5358 9793",
        "sku group 7000 1000 2000 3000",
    ]:
        neg(text, "credit_card")

    # --- credit_card: hard negatives verified against a real corpus
    # (a full clone of vercel/next.js) — every one of these is a false
    # positive the pre-1.3 detector actually produced on real-world code. ---
    for text in [
        # published Stripe test cards, as they actually appear in READMEs/docs
        "Use test card 4242 4242 4242 4242 with any future expiry date to simulate a successful charge.",
        "For 3D Secure testing, use card number 4000 0027 6000 3184 which requires authentication.",
        # JS numeric constants (2^52, 2^53 / Number.MAX_SAFE_INTEGER + 1)
        "const MAX_SAFE_INT_PLUS_ONE = 9007199254740992; // Number.MAX_SAFE_INTEGER + 1",
        "var LARGEST_SAFE_DOUBLE = 4503599627370496; // 2^52, used by the bignum shim",
        # a run of zeros in a padding array
        "const PADDING = [0000000000000000, 0000000000000000, 0000000000000000];",
        # a null GUID (the 16-digit run inside it is not a PAN)
        "const NULL_ID = '00000000-0000-0000-0000-000000000000';",
        "default_uuid: 00000000-0000-0000-0000-000000000000,",
    ]:
        neg(text, "credit_card")

    # --- credit_card: hard negatives from real minified JS, source maps,
    # UUIDs, git SHAs, and numeric constants (drawn from patterns observed
    # in a real next.js clone during the 0.1.1 remediation) ---
    for text in [
        "!function(e){var t=1732584193,n=4023233417,r=2562383102,i=271733878;e.exports=function(a){return t^n^r^i}}();",
        "sourceMappingURL=data:application/json;base64,eyJ2ZXJzaW9uIjozLCJzb3VyY2VzIjpbIndlYnBhY2s6Ly8vc3JjL2luZGV4LnRzIl19",
        "//# sourceMappingURL=chunk.8f3a9c21b7e4d6f0.js.map",
        "commit a1b2c3d4e5f60718293a4b5c6d7e8f9012345678 fixes the regression from last week's release.",
        "git rev-parse HEAD returned 9f8e7d6c5b4a39281706f5e4d3c2b1a0f9e8d7c6 for the failing build.",
        "resource id: 550e8400-e29b-41d4-a716-446655440000-v2-cache-key",
        "hash: 3f2504e0-4f89-11d3-9a0c-0305e82c3301 (namespace UUID, RFC 4122)",
        "const FNV_OFFSET_BASIS = 14695981039346656037n; // FNV-1a 64-bit constant",
        "0x1234567890ABCDEF1234567890ABCDEF is the raw memory address logged by the profiler.",
        "var CRC_TABLE_ENTRY_42 = 0xEDB88320CCCCCCCC; // precomputed CRC32 polynomial table entry",
        "webpackChunkName: 1234567890123456_vendors-node_modules_react-dom_client_js",
        "!function(t){function e(r){if(n[r])return n[r].exports;var i=n[r]={i:r,l:!1,exports:{}};return t[r].call(i.exports,i,i.exports,e),i.l=!0,i.exports}var n={};",
        "base64: iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=",
        "instance_id=8894477610358384728&session=1234567890123456&build=production",
        "phone hash lookup returned bucket 1234567890123456 during the sharding test.",
        "the object's internal id is 0x0000000000000000 before initialization completes.",
        "timestamp_ns: 1706745600000000000 recorded at the start of the trace span.",
        "test fixture literal: 1000000000000000 (10^15, used to test bignum overflow handling).",
        "memory offset 0xDEADBEEFCAFEBABE marks the start of the guard page.",
        "checksum 0000111122223333 did not match the expected value in the corrupted archive test.",
        "release tag build_number: 2024010100000001 was superseded by the next nightly.",
        "the array [1111111111111111, 2222222222222222] holds two sentinel values for the parser.",
    ]:
        neg(text, "credit_card")

    # --- ssn_us: true positives ---
    for text in [
        "Please update her file — SSN 246-53-8790 needs verification.",
        "The applicant provided 555-01-2345 as their social security number.",
        "Employee record shows 402-88-1234 on the tax form.",
        "His social security number, 118-45-6789, was on the document.",
        "The background check listed 233-90-1122 for the candidate.",
        "HR needs the number 076-33-9911 confirmed before payroll runs.",
        "Patient identifier 512-77-0022 was found in the old export.",
        "The lease application included 345-66-8890 as the guarantor SSN.",
        "Court filing referenced 690-12-4433 for the defendant.",
    ]:
        pos(text, "ssn_us")

    # --- ssn_us: hard negatives (denylisted context) ---
    for text in [
        "order id 234-56-7890 shipped today",
        "invoice ref 123-45-6789 attached",
        "sku 045-12-3456 discontinued",
        "batch 555-11-2222 processed",
        "build 321-54-9876 tagged for release",
        "version 100-20-3000 released today",
        "the case id 402-88-1234 was closed",
        "po ref 200-33-4455 approved",
    ]:
        neg(text, "ssn_us")

    # --- iban: true positives (mod-97 valid) ---
    iban_specs = [
        ("FR", 27, 1), ("DE", 22, 2), ("ES", 24, 3), ("IT", 27, 4),
        ("NL", 18, 5), ("BE", 16, 6), ("GB", 22, 7), ("FR", 27, 8), ("GB", 22, 9),
    ]
    for country, length, seed in iban_specs:
        iban = make_iban(country, length, seed)
        pos(f"Please wire the balance to account {iban} before month end.", "iban")

    # --- iban: hard negatives (bad checksum / unsupported country / malformed) ---
    for text in [
        "reference number FR7630006000011234567890188 was rejected",
        "account code DE81854123293427424522 failed validation",
        "the string PT50000201231234567890154 is not a supported country",
        "internal code XX1234567890123456789 looked IBAN-shaped but was not",
        "test value NL2119040377936867 failed the bank check",
        "BE35778897772135 does not pass verification",
        "GB42655536303423465353 was flagged as malformed",
    ]:
        neg(text, "iban")

    # --- french_nir: true positives (valid control key) ---
    nir_specs = [
        (1, 85, 6, 75, 115, 328), (2, 92, 11, 33, 5, 12), (1, 70, 1, 13, 55, 200),
        (2, 99, 20, 69, 234, 9), (1, 88, 9, 44, 12, 88), (2, 60, 3, 59, 350, 77),
        (1, 95, 12, 92, 4, 150), (2, 77, 42, 13, 88, 201), (1, 63, 7, 97, 1, 45),
    ]
    for spec in nir_specs:
        nir = make_nir(*spec)
        pos(f"Numero de securite sociale : {spaced_nir(nir)} figure sur le document.", "french_nir")

    # --- french_nir: hard negatives (bad control key / invalid month or dept) ---
    for text in [
        "invoice 1980315123456 78",
        "tracking number 2991399123456 12",
        "batch code 1850675115328 70",
        "order ref 1701195512020 99",
        "confirmation 2 96 13 96 234 009 55",
        "shipment 1 05 00 12 345 678 90",
        "case file 1 85 06 99 115 328 69",
        "ledger entry 2 92 42 33 005 012 44",
    ]:
        neg(text, "french_nir")

    # --- generic hard negatives (ISBN, GUID, timestamp, coordinates, part numbers) ---
    for text in [
        "the book ISBN is 978-3-16-148410-0 per the catalog entry",
        "ISBN-10 0-306-40615-2 was used for the legacy edition",
        "request id 550e8400-e29b-41d4-a716-446655440000 traced the failure",
        "session guid 6fa459ea-ee8a-3ca4-894e-db77e160355e was logged",
        "event logged at 2026-07-31T14:23:00Z per the audit trail",
        "deployment finished at 2026-01-05 09:12:44 UTC",
        "the venue is located at 48.8566, 2.3522 near the river",
        "coordinates 37.7749,-122.4194 mark the warehouse entrance",
        "part number PN-4589-2201-XL was out of stock",
        "component code A1-7734-B was replaced under warranty",
    ]:
        neg(text, "none")

    out_path = Path(__file__).parent / "pii_benchmark.jsonl"
    with open(out_path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    n_pos = sum(1 for r in rows if r["label"])
    n_neg = sum(1 for r in rows if not r["label"])
    print(f"Wrote {len(rows)} rows ({n_pos} positive, {n_neg} negative) to {out_path}")


if __name__ == "__main__":
    main()

"""
PII detection — the single code path used by both the auditor and the
cleaner so they can never disagree about what counts as PII.

Detection is a two-stage process: a regex finds *candidate* matches, then a
type-specific validator either accepts or rejects each candidate. This
keeps the regexes broad (so nothing is missed) while pushing precision
into checksum/format validation (so ordinary business text like order IDs,
version strings, and invoice numbers isn't flagged).
"""

import ipaddress
import re
from collections import Counter

# --- Candidate-match patterns (deliberately permissive; validators do the filtering) ---
PII_PATTERNS = {
    "email": re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+"),
    "phone_intl": re.compile(r"\+\d{1,3}[\s.-]\d[\s.\d-]{6,15}\d"),
    "ip_address": re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"),
    "credit_card": re.compile(r"\b(?:\d{4}[-\s]?){3}\d{4}\b"),
    "ssn_us": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "iban": re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{4}\d{7}(?:[A-Z0-9]){0,16}\b"),
    "french_nir": re.compile(r"\b[12]\s?\d{2}\s?\d{2}\s?\d{2}\s?\d{3}\s?\d{3}\s?\d{2}\b"),
}

REPLACEMENT_MAP = {
    "email": "[EMAIL_REDACTED]",
    "phone_intl": "[PHONE_REDACTED]",
    "ip_address": "[IP_REDACTED]",
    "credit_card": "[CC_REDACTED]",
    "ssn_us": "[SSN_REDACTED]",
    "iban": "[IBAN_REDACTED]",
    "french_nir": "[NIR_REDACTED]",
}

IP_MODES = {"all", "public", "off"}

# Words that, when they appear immediately before a candidate ssn_us/phone_intl
# match, indicate it's actually an order/invoice/version/build identifier.
DEFAULT_CONTEXT_DENYLIST = frozenset(
    {"invoice", "sku", "order", "ref", "version", "build", "batch", "id"}
)

IBAN_LENGTHS = {
    "FR": 27, "DE": 22, "ES": 24, "IT": 27, "NL": 18, "BE": 16, "GB": 22,
}

# Published test/demo card numbers from payment processor documentation
# (Stripe's testing docs, Visa/Mastercard/Amex/Discover demo numbers
# reproduced across processor developer docs, PayPal sandbox card numbers,
# Adyen test card numbers). These show up constantly in real codebases —
# READMEs, integration tests, example .env files — and are never
# cardholder data. Luhn alone does not reject them, because they're
# deliberately constructed to *pass* Luhn so they behave like real cards
# in a test environment.
KNOWN_TEST_CARD_NUMBERS = frozenset({
    # Stripe
    "4242424242424242", "4000056655665556", "4000002760003184",
    "4000002500003155", "4000000000003220", "4000000000003063",
    "4000000000009995", "4000000000009987", "4000000000009979",
    "4000000000000002", "4000000000000069", "4000000000000127",
    "4000000000000119", "4000000000003184", "2223003122003222",
    "5200828282828210", "3530111333300000", "3566002020360505",
    "6200000000000005",
    # Visa
    "4111111111111111", "4012888888881881", "4222222222222",
    "4012000033330026", "4012000077777777",
    # Mastercard
    "5555555555554444", "5105105105105100", "5424000000000015",
    # American Express
    "378282246310005", "371449635398431", "378734493671000",
    # Discover
    "6011111111111117", "6011000990139424",
    # PayPal sandbox — https://developer.paypal.com/tools/sandbox/card-testing/
    "4032039403200393", "5425233430109903", "373641846941295",
    # Adyen test cards
    "5555444433331111", "370000000000002", "36006666333344",
})

# Paths that indicate machine-generated or third-party content (minified
# JS, source maps, vendored/compiled output) where dense numeric constants
# are expected and not worth flagging as PII by default.
MACHINE_PATH_MARKERS = ("dist/", "compiled/", "vendor/", "node_modules/", ".min.js", ".map")

_UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


# --- Validators ---

def is_low_entropy_digits(digits: str) -> bool:
    """
    True for digit sequences that are never real card/SSN numbers: fewer
    than 4 distinct digits (this alone also covers a single repeated
    digit, e.g. "0000000000000000"), or a strictly ascending/descending
    run such as "0123456789012345" or "9876543210987654" (digits wrap
    mod 10, since padding/placeholder sequences commonly do).
    """
    if len(set(digits)) < 4:
        return True
    diffs = [(int(digits[i + 1]) - int(digits[i])) % 10 for i in range(len(digits) - 1)]
    if all(d == 1 for d in diffs) or all(d == 9 for d in diffs):
        return True
    return False


def _looks_like_machine_path(file_path) -> bool:
    if file_path is None:
        return False
    normalized = str(file_path).replace("\\", "/").lower()
    return any(marker in normalized for marker in MACHINE_PATH_MARKERS)


def _low_whitespace_context(text: str, start: int, end: int, window: int = 100) -> bool:
    """True if the ~200 chars around a match look like minified code, a
    source map, or a base64 blob rather than prose or normal source."""
    ctx = text[max(0, start - window):end + window]
    if not ctx:
        return False
    return (sum(1 for ch in ctx if ch.isspace()) / len(ctx)) < 0.05


def _breaks_complete_token(text: str, start: int, end: int) -> bool:
    """
    True if the match is not a standalone token: it's immediately touching
    a hex letter (meaning it's actually a substring of a longer hex/UUID
    value), or it's touching a dash and the surrounding text is
    UUID/GUID-shaped (catches PANs "matched" inside a null GUID like
    00000000-0000-0000-0000-000000000000).
    """
    before = text[start - 1:start]
    after = text[end:end + 1]
    if before.lower() in "abcdef" or after.lower() in "abcdef":
        return True
    if before == "-" or after == "-":
        window = text[max(0, start - 40):end + 40]
        if _UUID_RE.search(window):
            return True
    return False


def luhn_check(digits: str) -> bool:
    """Standard Luhn/mod-10 checksum used by payment card numbers."""
    if not digits.isdigit():
        return False
    total = 0
    parity = len(digits) % 2
    for i, ch in enumerate(digits):
        d = int(ch)
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def validate_iban(candidate: str) -> bool:
    """Mod-97 checksum plus a per-country length check (ISO 13616)."""
    s = candidate.replace(" ", "").upper()
    if len(s) < 4:
        return False
    country = s[:2]
    expected_len = IBAN_LENGTHS.get(country)
    if expected_len is None or len(s) != expected_len:
        return False
    rearranged = s[4:] + s[:4]
    try:
        numeric = "".join(str(int(ch, 36)) for ch in rearranged)
    except ValueError:
        return False
    return int(numeric) % 97 == 1


_NIR_VALID_MONTHS = set(range(1, 13)) | {20, 30} | set(range(42, 100))


def validate_french_nir(digits: str) -> bool:
    """
    Validate a 15-digit French NIR (13 identifier digits + 2-digit control key).
    Checks the control key, a plausible birth month (including INSEE special
    values for unknown/administrative months), and a plausible department code.
    """
    if len(digits) != 15 or not digits.isdigit():
        return False
    if digits[0] not in ("1", "2"):
        return False
    month = int(digits[3:5])
    if month not in _NIR_VALID_MONTHS:
        return False
    dept = int(digits[5:7])
    if not (1 <= dept <= 95 or dept in (97, 98, 99)):
        return False
    first13 = digits[:13]
    key = int(digits[13:15])
    expected_key = 97 - (int(first13) % 97)
    return key == expected_key


def validate_ip(candidate: str, trailing_context: str, ip_mode: str) -> bool:
    """
    Reject malformed octets and version-string false positives
    (e.g. "10.2.14.3.1" — a 5-part version number, not an IP).
    Filters RFC1918/loopback/link-local ranges when ip_mode == "public".
    """
    if ip_mode == "off":
        return False
    parts = candidate.split(".")
    if len(parts) != 4 or any(int(p) > 255 for p in parts):
        return False
    if trailing_context[:1] == "." and trailing_context[1:2].isdigit():
        return False
    if ip_mode == "public":
        try:
            addr = ipaddress.ip_address(candidate)
        except ValueError:
            return False
        if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved or addr.is_multicast:
            return False
    return True


def _has_denylisted_context(text: str, start: int, denylist: frozenset) -> bool:
    prefix = text[max(0, start - 25):start].lower()
    words = re.findall(r"[a-z]+", prefix)
    return any(w in denylist for w in words[-3:])


def validate_credit_card(
    matched: str,
    text: str,
    start: int,
    end: int,
    file_path=None,
    strict_pii: bool = False,
) -> bool:
    """
    A candidate 16-digit run is only reported as a card number if it
    passes *all* of: not a published test PAN, Luhn-valid, not a
    low-entropy/placeholder sequence, a complete token (not a substring of
    a longer hex/GUID value), and — unless --strict-pii — not sitting
    inside minified/vendored/compiled content where dense numeric
    constants are the norm rather than the exception.
    """
    digits = re.sub(r"\D", "", matched)
    if digits in KNOWN_TEST_CARD_NUMBERS:
        return False
    if not luhn_check(digits):
        return False
    if is_low_entropy_digits(digits):
        return False
    if _breaks_complete_token(text, start, end):
        return False
    if not strict_pii:
        if _looks_like_machine_path(file_path) or _low_whitespace_context(text, start, end):
            return False
    return True


def _validate_match(
    pii_type: str,
    match: re.Match,
    text: str,
    ip_mode: str,
    denylist: frozenset,
    file_path=None,
    strict_pii: bool = False,
) -> bool:
    matched = match.group(0)
    if pii_type == "credit_card":
        return validate_credit_card(matched, text, match.start(), match.end(), file_path, strict_pii)
    if pii_type == "iban":
        return validate_iban(matched)
    if pii_type == "french_nir":
        return validate_french_nir(re.sub(r"\D", "", matched))
    if pii_type == "ip_address":
        return validate_ip(matched, text[match.end():match.end() + 2], ip_mode)
    if pii_type == "ssn_us":
        digits = re.sub(r"\D", "", matched)
        if is_low_entropy_digits(digits):
            return False
        return not _has_denylisted_context(text, match.start(), denylist)
    if pii_type == "phone_intl":
        return not _has_denylisted_context(text, match.start(), denylist)
    return True  # email needs no extra validation


def scan_text(
    text: str,
    ip_mode: str = "public",
    context_denylist: frozenset | None = None,
    file_path=None,
    strict_pii: bool = False,
) -> dict:
    """Scan a string and return {pii_type: [(start, end), ...]} for validated matches."""
    if ip_mode not in IP_MODES:
        raise ValueError(f"ip_mode must be one of {sorted(IP_MODES)}, got {ip_mode!r}")
    denylist = DEFAULT_CONTEXT_DENYLIST if context_denylist is None else context_denylist
    found = {}
    for pii_type, pattern in PII_PATTERNS.items():
        if pii_type == "ip_address" and ip_mode == "off":
            continue
        matches = [
            (m.start(), m.end())
            for m in pattern.finditer(text)
            if _validate_match(pii_type, m, text, ip_mode, denylist, file_path, strict_pii)
        ]
        if matches:
            found[pii_type] = matches
    return found


def redact(
    text: str,
    ip_mode: str = "public",
    context_denylist: frozenset | None = None,
    file_path=None,
    strict_pii: bool = False,
) -> tuple:
    """Return (redacted_text, {pii_type: replacement_count})."""
    found = scan_text(text, ip_mode=ip_mode, context_denylist=context_denylist, file_path=file_path, strict_pii=strict_pii)
    spans = sorted(
        ((start, end, pii_type) for pii_type, matches in found.items() for start, end in matches),
        key=lambda x: x[0],
    )
    pieces = []
    counts: Counter = Counter()
    last_end = 0
    for start, end, pii_type in spans:
        if start < last_end:
            continue  # overlapping match from another type — keep the first
        pieces.append(text[last_end:start])
        pieces.append(REPLACEMENT_MAP[pii_type])
        counts[pii_type] += 1
        last_end = end
    pieces.append(text[last_end:])
    return "".join(pieces), dict(counts)


def scan_file_chunked(
    fpath,
    chunk_size: int = 1_000_000,
    overlap: int = 512,
    scan_limit: int = 0,
    ip_mode: str = "public",
    context_denylist: frozenset | None = None,
    strict_pii: bool = False,
) -> tuple:
    """
    Scan a file for PII in overlapping chunks without loading the whole
    file into memory. Returns (sorted list of pii types found, truncated: bool).

    scan_limit (bytes/chars, approximate): 0 means unlimited. When set and
    the file is larger than the limit, truncated is True.
    """
    found_types: set = set()
    truncated = False
    consumed = 0
    carry = ""

    with open(fpath, encoding="utf-8", errors="replace") as f:
        while True:
            if scan_limit and consumed >= scan_limit:
                truncated = bool(f.read(1))
                break

            read_size = chunk_size
            if scan_limit:
                read_size = min(chunk_size, scan_limit - consumed)

            chunk = f.read(read_size)
            if not chunk:
                break
            consumed += len(chunk)

            search_text = carry + chunk
            matches = scan_text(
                search_text, ip_mode=ip_mode, context_denylist=context_denylist,
                file_path=fpath, strict_pii=strict_pii,
            )
            found_types.update(matches.keys())

            carry = search_text[-overlap:] if overlap else ""

    return sorted(found_types), truncated

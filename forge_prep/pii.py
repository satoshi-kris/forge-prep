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


# --- Validators ---

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


def _validate_match(pii_type: str, match: re.Match, text: str, ip_mode: str, denylist: frozenset) -> bool:
    matched = match.group(0)
    if pii_type == "credit_card":
        return luhn_check(re.sub(r"\D", "", matched))
    if pii_type == "iban":
        return validate_iban(matched)
    if pii_type == "french_nir":
        return validate_french_nir(re.sub(r"\D", "", matched))
    if pii_type == "ip_address":
        return validate_ip(matched, text[match.end():match.end() + 2], ip_mode)
    if pii_type in ("ssn_us", "phone_intl"):
        return not _has_denylisted_context(text, match.start(), denylist)
    return True  # email needs no extra validation


def scan_text(
    text: str,
    ip_mode: str = "public",
    context_denylist: frozenset | None = None,
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
            if _validate_match(pii_type, m, text, ip_mode, denylist)
        ]
        if matches:
            found[pii_type] = matches
    return found


def redact(
    text: str,
    ip_mode: str = "public",
    context_denylist: frozenset | None = None,
) -> tuple:
    """Return (redacted_text, {pii_type: replacement_count})."""
    found = scan_text(text, ip_mode=ip_mode, context_denylist=context_denylist)
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
            matches = scan_text(search_text, ip_mode=ip_mode, context_denylist=context_denylist)
            found_types.update(matches.keys())

            carry = search_text[-overlap:] if overlap else ""

    return sorted(found_types), truncated

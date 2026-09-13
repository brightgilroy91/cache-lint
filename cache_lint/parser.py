"""Pure functions for parsing and checking HTTP caching headers.

Nothing in this module touches a file, a socket, or the clock. The CLI
layer is responsible for reading input and for supplying "now" when a
freshness calculation needs it. That split is what makes the rules here
testable with plain dicts and strings.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime


@dataclass(frozen=True)
class Finding:
    severity: str  # "error", "warning", or "info"
    message: str


def _split_directives(value: str) -> list[str]:
    """Split a Cache-Control value on commas, ignoring commas inside quotes."""
    parts = []
    current: list[str] = []
    in_quotes = False
    for ch in value:
        if ch == '"':
            in_quotes = not in_quotes
            current.append(ch)
        elif ch == "," and not in_quotes:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    parts.append("".join(current))
    return parts


def parse_cache_control(value: str) -> dict[str, str | None]:
    """Parse a Cache-Control header value into {directive: value_or_none}.

    Directive names are lowercased. Quoted values have their quotes
    stripped. Directives with no "=" (e.g. "no-store") map to None.
    """
    directives: dict[str, str | None] = {}
    for part in _split_directives(value):
        part = part.strip()
        if not part:
            continue
        if "=" in part:
            name, _, raw_value = part.partition("=")
            name = name.strip().lower()
            raw_value = raw_value.strip()
            if len(raw_value) >= 2 and raw_value.startswith('"') and raw_value.endswith('"'):
                raw_value = raw_value[1:-1]
            directives[name] = raw_value
        else:
            directives[part.lower()] = None
    return directives


def parse_headers_text(text: str) -> dict[str, str]:
    """Parse raw header text (as pasted from devtools or `curl -I`) into a dict.

    Lines without a colon (status lines, request lines, blank lines) are
    skipped. Header names are lowercased. Repeated headers are joined
    with ", " per RFC 9110 semantics.
    """
    headers: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        name, _, value = line.partition(":")
        name = name.strip().lower()
        value = value.strip()
        if name in headers:
            headers[name] = f"{headers[name]}, {value}"
        else:
            headers[name] = value
    return headers


def _parse_http_date(value: str) -> datetime | None:
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def freshness_lifetime_seconds(
    cache_control: dict[str, str | None],
    headers: dict[str, str],
    reference_time: datetime,
) -> int | None:
    """Work out how many seconds a response is fresh for.

    Follows the RFC 9111 precedence: s-maxage, then max-age, then Expires
    (relative to the Date header if present, otherwise reference_time).
    Returns None if nothing usable is present.
    """
    if "s-maxage" in cache_control:
        try:
            return int(cache_control["s-maxage"])
        except (TypeError, ValueError):
            pass

    if "max-age" in cache_control:
        try:
            return int(cache_control["max-age"])
        except (TypeError, ValueError):
            pass

    expires_raw = headers.get("expires")
    if expires_raw:
        expires_dt = _parse_http_date(expires_raw)
        if expires_dt is None:
            return None
        base_dt = reference_time
        date_raw = headers.get("date")
        if date_raw:
            parsed_date = _parse_http_date(date_raw)
            if parsed_date is not None:
                base_dt = parsed_date
        return int((expires_dt - base_dt).total_seconds())

    return None


def lint_headers(headers: dict[str, str]) -> list[Finding]:
    """Check a parsed header dict for common caching mistakes."""
    findings: list[Finding] = []
    raw_cc = headers.get("cache-control")
    cache_control = parse_cache_control(raw_cc) if raw_cc else {}
    has_expires = "expires" in headers

    if raw_cc is None and not has_expires:
        findings.append(
            Finding("warning", "no Cache-Control or Expires header; caches will fall back to heuristic freshness")
        )

    if "no-store" in cache_control and ("max-age" in cache_control or "s-maxage" in cache_control):
        findings.append(
            Finding("error", "no-store makes max-age/s-maxage pointless; the response will never be stored")
        )

    if "public" in cache_control and "private" in cache_control:
        findings.append(Finding("error", "public and private are mutually exclusive"))

    if "no-cache" in cache_control and "immutable" in cache_control:
        findings.append(
            Finding("warning", "no-cache and immutable conflict: immutable skips revalidation, no-cache forces it")
        )

    if "immutable" in cache_control and "max-age" not in cache_control and "s-maxage" not in cache_control:
        findings.append(Finding("warning", "immutable without max-age has no effect in most implementations"))

    if "private" in cache_control and "s-maxage" in cache_control:
        findings.append(
            Finding("info", "s-maxage is ignored on a private response; only shared caches honor it")
        )

    if "max-age" in cache_control:
        raw_value = cache_control["max-age"]
        try:
            if int(raw_value) < 0:
                findings.append(Finding("error", "max-age is negative"))
        except (TypeError, ValueError):
            findings.append(Finding("error", f"max-age value {raw_value!r} is not a valid integer"))

    vary = headers.get("vary")
    if vary and "*" in [v.strip() for v in vary.split(",")]:
        findings.append(Finding("warning", "Vary: * prevents shared caches from ever reusing a stored response"))

    age_header = headers.get("age")
    if age_header and "max-age" in cache_control:
        try:
            age_value = int(age_header)
            max_age_value = int(cache_control["max-age"])
            if age_value >= max_age_value:
                findings.append(
                    Finding("info", f"Age ({age_value}s) has already reached max-age ({max_age_value}s); response is stale")
                )
        except (TypeError, ValueError):
            pass

    return findings

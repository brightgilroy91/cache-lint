from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

from .parser import (
    freshness_lifetime_seconds,
    lint_headers,
    parse_cache_control,
    parse_headers_text,
)


def _read_input(path: str | None) -> str:
    if path is None or path == "-":
        return sys.stdin.read()
    with open(path, encoding="utf-8") as f:
        return f.read()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="cache-lint",
        description="Check HTTP response headers for caching problems and report freshness lifetime.",
    )
    parser.add_argument(
        "file",
        nargs="?",
        help="file with response headers, one 'Name: value' per line (default: stdin)",
    )
    args = parser.parse_args(argv)

    text = _read_input(args.file)
    headers = parse_headers_text(text)
    findings = lint_headers(headers)

    raw_cc = headers.get("cache-control")
    cache_control = parse_cache_control(raw_cc) if raw_cc else {}
    lifetime = freshness_lifetime_seconds(cache_control, headers, datetime.now(timezone.utc))

    if lifetime is not None:
        print(f"freshness lifetime: {lifetime}s")
    else:
        print("freshness lifetime: unknown (no max-age, s-maxage, or usable Expires)")

    if not findings:
        print("no issues found")
    else:
        for finding in findings:
            print(f"[{finding.severity}] {finding.message}")

    return 1 if any(f.severity == "error" for f in findings) else 0


if __name__ == "__main__":
    raise SystemExit(main())

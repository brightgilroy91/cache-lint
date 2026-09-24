from __future__ import annotations

import unittest
from datetime import datetime, timezone

from cache_lint.parser import (
    freshness_lifetime_seconds,
    lint_headers,
    parse_cache_control,
    parse_headers_text,
)


class ParseCacheControlTests(unittest.TestCase):
    def test_simple_directives(self):
        result = parse_cache_control("public, max-age=3600")
        self.assertEqual(result, {"public": None, "max-age": "3600"})

    def test_directive_names_are_lowercased(self):
        result = parse_cache_control("MAX-AGE=100, NO-STORE")
        self.assertEqual(result, {"max-age": "100", "no-store": None})

    def test_quoted_value_with_embedded_comma_is_not_split(self):
        result = parse_cache_control('private="X-Custom, Y-Custom", max-age=60')
        self.assertEqual(result["private"], "X-Custom, Y-Custom")
        self.assertEqual(result["max-age"], "60")

    def test_quotes_are_stripped_from_value(self):
        result = parse_cache_control('no-cache="Set-Cookie"')
        self.assertEqual(result["no-cache"], "Set-Cookie")

    def test_whitespace_around_directives_and_values_is_trimmed(self):
        result = parse_cache_control("  public ,  max-age = 300 ")
        self.assertEqual(result, {"public": None, "max-age": "300"})

    def test_empty_segments_are_skipped(self):
        result = parse_cache_control("public,, max-age=60,")
        self.assertEqual(result, {"public": None, "max-age": "60"})


class MalformedMaxAgeTests(unittest.TestCase):
    def test_lint_flags_non_integer_max_age(self):
        headers = {"cache-control": "max-age=abc"}
        findings = lint_headers(headers)
        messages = [f.message for f in findings]
        self.assertIn("max-age value 'abc' is not a valid integer", messages)

    def test_lint_flags_negative_max_age(self):
        headers = {"cache-control": "max-age=-5"}
        findings = lint_headers(headers)
        messages = [f.message for f in findings]
        self.assertIn("max-age is negative", messages)

    def test_lint_accepts_quoted_integer_max_age(self):
        headers = {"cache-control": 'max-age="3600"'}
        findings = lint_headers(headers)
        self.assertEqual(findings, [])

    def test_freshness_falls_back_to_expires_when_max_age_is_malformed(self):
        cache_control = parse_cache_control("max-age=notanumber")
        headers = {"expires": "Wed, 21 Oct 2015 07:28:00 GMT"}
        reference_time = datetime(2015, 10, 21, 7, 0, 0, tzinfo=timezone.utc)
        lifetime = freshness_lifetime_seconds(cache_control, headers, reference_time)
        self.assertEqual(lifetime, 1680)

    def test_freshness_returns_none_when_max_age_malformed_and_no_expires(self):
        cache_control = parse_cache_control("max-age=notanumber")
        lifetime = freshness_lifetime_seconds(cache_control, {}, datetime.now(timezone.utc))
        self.assertIsNone(lifetime)

    def test_freshness_falls_back_to_max_age_when_s_maxage_is_malformed(self):
        cache_control = parse_cache_control("s-maxage=nope, max-age=120")
        lifetime = freshness_lifetime_seconds(cache_control, {}, datetime.now(timezone.utc))
        self.assertEqual(lifetime, 120)


class ParseHeadersTextTests(unittest.TestCase):
    def test_skips_lines_without_a_colon(self):
        text = "HTTP/1.1 200 OK\nCache-Control: max-age=60\n\n"
        headers = parse_headers_text(text)
        self.assertEqual(headers, {"cache-control": "max-age=60"})

    def test_header_names_are_lowercased_and_values_stripped(self):
        text = "Content-Type:   text/html; charset=utf-8  \n"
        headers = parse_headers_text(text)
        self.assertEqual(headers, {"content-type": "text/html; charset=utf-8"})

    def test_repeated_headers_are_joined_with_comma_space(self):
        text = "Set-Cookie: a=1\nSet-Cookie: b=2\n"
        headers = parse_headers_text(text)
        self.assertEqual(headers["set-cookie"], "a=1, b=2")

    def test_curl_verbose_output_keeps_only_response_headers(self):
        text = (
            "*   Trying 93.184.216.34:443...\n"
            "* Connected to example.com (93.184.216.34) port 443\n"
            "> GET / HTTP/1.1\n"
            "> Host: example.com\n"
            "> User-Agent: curl/8.4.0\n"
            "> Accept: */*\n"
            ">\n"
            "< HTTP/1.1 200 OK\n"
            "< Cache-Control: public, max-age=3600\n"
            "< Vary: Accept-Encoding\n"
            "<\n"
            "* Connection #0 to host example.com left intact\n"
            "<html><body>hello</body></html>\n"
        )
        headers = parse_headers_text(text)
        self.assertEqual(
            headers,
            {"cache-control": "public, max-age=3600", "vary": "Accept-Encoding"},
        )

    def test_curl_verbose_output_with_redirect_keeps_final_response_only(self):
        text = (
            "> GET / HTTP/1.1\n"
            "> Host: example.com\n"
            ">\n"
            "< HTTP/1.1 301 Moved Permanently\n"
            "< Location: https://example.com/new\n"
            "< Cache-Control: max-age=60\n"
            "<\n"
            "> GET /new HTTP/1.1\n"
            "> Host: example.com\n"
            ">\n"
            "< HTTP/1.1 200 OK\n"
            "< Cache-Control: no-store\n"
            "<\n"
        )
        headers = parse_headers_text(text)
        self.assertEqual(headers, {"cache-control": "no-store"})


class FreshnessLifetimePrecedenceTests(unittest.TestCase):
    def test_s_maxage_wins_over_max_age_and_expires(self):
        cache_control = parse_cache_control("s-maxage=10, max-age=20")
        headers = {"expires": "Wed, 21 Oct 2015 07:28:00 GMT"}
        lifetime = freshness_lifetime_seconds(cache_control, headers, datetime.now(timezone.utc))
        self.assertEqual(lifetime, 10)

    def test_max_age_wins_over_expires(self):
        cache_control = parse_cache_control("max-age=20")
        headers = {"expires": "Wed, 21 Oct 2015 07:28:00 GMT"}
        lifetime = freshness_lifetime_seconds(cache_control, headers, datetime.now(timezone.utc))
        self.assertEqual(lifetime, 20)

    def test_expires_is_relative_to_date_header_when_present(self):
        headers = {
            "date": "Wed, 21 Oct 2015 07:00:00 GMT",
            "expires": "Wed, 21 Oct 2015 07:28:00 GMT",
        }
        lifetime = freshness_lifetime_seconds({}, headers, datetime.now(timezone.utc))
        self.assertEqual(lifetime, 1680)

    def test_unparseable_expires_returns_none(self):
        headers = {"expires": "not a date"}
        lifetime = freshness_lifetime_seconds({}, headers, datetime.now(timezone.utc))
        self.assertIsNone(lifetime)

    def test_nothing_present_returns_none(self):
        self.assertIsNone(freshness_lifetime_seconds({}, {}, datetime.now(timezone.utc)))


if __name__ == "__main__":
    unittest.main()

# cache-lint

A command line tool that reads HTTP response headers and tells you what a
cache will actually do with them: how long the response stays fresh, and
which directives contradict each other.

I keep running into responses where someone set `Cache-Control: no-store,
max-age=3600` or `private` together with `s-maxage`, and the intent is
unclear even to the person who wrote it. `cache-lint` doesn't fetch
anything itself — you give it headers you already have (from devtools,
`curl -I`, a log line, whatever) and it tells you the freshness lifetime
and flags anything contradictory.

## Usage

Save headers to a file, one `Name: value` per line:

```
Cache-Control: public, max-age=3600, s-maxage=86400
Vary: Accept-Encoding
ETag: "33a64df551"
```

```
$ cache-lint headers.txt
freshness lifetime: 3600s
no issues found
```

Or pipe them in directly from curl:

```
$ curl -sI https://example.com | cache-lint
```

`curl -v` output works too - request lines, `*` info lines, and the
response body are all ignored, and if the transcript includes a redirect
only the final response's headers are used:

```
$ curl -v https://example.com 2>&1 | cache-lint
```

A header set with a real conflict:

```
$ printf 'Cache-Control: no-store, max-age=3600\n' | cache-lint
freshness lifetime: 3600s
[error] no-store makes max-age/s-maxage pointless; the response will never be stored
```

The exit code is `1` if any finding has severity `error`, `0` otherwise,
so it can be used as a check in a build or deploy script.

## Why headers-as-text instead of a URL

Taking a URL would mean making network requests, which pulls in retry
and TLS edge cases that have nothing to do with cache header logic. Piping
in headers you already captured keeps the tool doing one thing, and keeps
the parsing and linting functions pure — they take a dict or a string in
and return a value out, nothing else. That's also what makes them easy to
unit test: no server to mock, no clock to fake except where the freshness
calculation explicitly takes a reference time as an argument.

## Install

No dependencies beyond the Python standard library.

```
pip install -e .
```

or just run it in place:

```
python -m cache_lint headers.txt
```

## Status

Early. Currently understands `Cache-Control`, `Expires`, `Date`, `Age`,
and `Vary`, from plain header dumps or `curl -v` transcripts.

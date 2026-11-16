"""Web tools: web_fetch (SSRF-guarded) and web_search (DuckDuckGo HTML).

Both use httpx with strict timeouts, cap output, and return error strings
instead of raising. HTML is stripped to text with stdlib html.parser.
"""
from __future__ import annotations

import ipaddress
import socket
from html.parser import HTMLParser
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import httpx

FETCH_TIMEOUT = 20.0
MAX_REDIRECTS = 5
UA = "Mozilla/5.0 (compatible; dsc-agent/0.1; +https://github.com/dsc)"
_BLOCKED_HOSTS = {"localhost", "0.0.0.0", "metadata.google.internal", "169.254.169.254"}


class _TextExtractor(HTMLParser):
    """Extract title and visible text from HTML."""

    _SKIP = {"script", "style", "noscript", "head", "meta", "link"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.chunks: list[str] = []
        self._skip_depth = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip_depth += 1
        if tag == "title":
            self._in_title = True
        if tag in ("br", "p", "div", "li", "tr", "h1", "h2", "h3"):
            self.chunks.append("\n")

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip_depth:
            self.chunks.append(data)


def html_to_text(html: str) -> tuple[str, str]:
    """Return (title, text)."""
    p = _TextExtractor()
    try:
        p.feed(html)
        p.close()
    except Exception:
        pass
    text = "".join(p.chunks)
    # collapse whitespace
    lines = [ln.strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    return p.title.strip(), "\n".join(lines)


def _assert_safe_url(url: str) -> None:
    """Refuse non-http(s) and SSRF targets. Raises ValueError."""
    u = urlparse(url)
    if u.scheme not in ("http", "https"):
        raise ValueError(f"only http(s) URLs allowed, got scheme {u.scheme!r}")
    host = u.hostname or ""
    if not host:
        raise ValueError("URL has no hostname")
    if host.lower() in _BLOCKED_HOSTS or host.lower().endswith(".local"):
        raise ValueError(f"blocked host: {host}")
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError as e:
        raise ValueError(f"cannot resolve {host}: {e}")
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
        ):
            raise ValueError(f"blocked private/reserved address: {ip} for {host}")


def web_fetch(**args) -> str:
    """web_fetch(url, max_chars?) — GET, strip HTML, cap chars."""
    url = str(args.get("url", "")).strip()
    if not url:
        return "error: web_fetch requires a url"
    try:
        max_chars = min(int(args.get("max_chars") or 20_000), 100_000)
    except (TypeError, ValueError):
        return "error: max_chars must be an integer"

    try:
        _assert_safe_url(url)
    except ValueError as e:
        return f"error: {e}"

    try:
        with httpx.Client(
            follow_redirects=True,
            timeout=FETCH_TIMEOUT,
            headers={"User-Agent": UA},
        ) as client:
            # manual redirect count for a precise <=5 cap
            resp = client.build_request("GET", url)
            r = client.send(resp, stream=False)
            if r.history and len(r.history) > MAX_REDIRECTS:
                return f"error: too many redirects (> {MAX_REDIRECTS})"
            body = r.text
            status = r.status_code
            final_url = str(r.url)
    except httpx.HTTPError as e:
        return f"error: fetch failed: {type(e).__name__}: {e}"

    if status >= 400:
        return f"error: HTTP {status} for {url}"
    title, text = html_to_text(body)
    out = f"title: {title}\nurl: {final_url}\n\n{text}"
    if len(out) > max_chars:
        out = out[:max_chars] + f"\n...[truncated {len(out) - max_chars} chars]"
    return out


# ------------------------------------------------------------- web_search
class _DDGParser(HTMLParser):
    """Parse DuckDuckGo HTML results: a.result__a for links."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[dict] = []
        self._cur: dict | None = None
        self._in_link = False
        self._in_snippet = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = a.get("class", "")
        if tag == "a" and "result__a" in cls:
            href = a.get("href", "")
            # DDG wraps links: //duckduckgo.com/l/?uddg=<encoded>&rut=...
            if href.startswith("//duckduckgo.com/l/") or "/l/?" in href:
                qs = parse_qs(urlparse(href).query)
                real = qs.get("uddg", [None])[0]
                if real:
                    href = unquote(real)
            self._cur = {"url": href, "title": "", "snippet": ""}
            self._in_link = True
        elif tag == "a" and "result__snippet" in cls:
            self._in_snippet = True

    def handle_endtag(self, tag):
        if tag == "a":
            if self._in_link and self._cur:
                if self._cur["url"] and self._cur["title"]:
                    self.results.append(self._cur)
                self._cur = None
            self._in_link = False
            self._in_snippet = False

    def handle_data(self, data):
        if self._in_link and self._cur is not None:
            self._cur["title"] += data
        elif self._in_snippet and self.results:
            self.results[-1]["snippet"] += data


def web_search(**args) -> str:
    """web_search(query, max_results?) — DuckDuckGo HTML endpoint, no key."""
    query = str(args.get("query", "")).strip()
    if not query:
        return "error: web_search requires a query"
    try:
        max_results = min(int(args.get("max_results") or 5), 20)
    except (TypeError, ValueError):
        return "error: max_results must be an integer"

    try:
        resp = httpx.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query},
            headers={"User-Agent": UA},
            timeout=20.0,
            follow_redirects=True,
        )
    except httpx.HTTPError as e:
        return f"error: search failed: {type(e).__name__}: {e}"

    if resp.status_code >= 400:
        return f"error: search backend returned HTTP {resp.status_code}"

    p = _DDGParser()
    try:
        p.feed(resp.text)
        p.close()
    except Exception as e:
        return f"error: could not parse search results: {e}"

    results = p.results[:max_results]
    if not results:
        return (
            "error: no results parsed from the search backend "
            "(it may have changed markup or is rate-limiting; try again later)"
        )
    lines = []
    for i, r in enumerate(results, 1):
        line = f"{i}. {r['title']}\n   {r['url']}"
        if r.get("snippet"):
            line += f"\n   {r['snippet'][:200]}"
        lines.append(line)
    return "\n".join(lines)

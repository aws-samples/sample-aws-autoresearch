#!/usr/bin/env python3
"""
search.py -- web search + page reading for Claude Code sessions with no built-in
WebSearch tool (e.g. Claude via Amazon Bedrock). Stdlib only (pdftotext optional).

  search.py web    "<query>" [-n 10]          DuckDuckGo HTML results: title, url, snippet
  search.py arxiv  "<query>" [-n 10]          arXiv API: id, title, date, authors, abstract
  search.py github "<query>" [-n 10] [--code] GitHub repo search (or code search w/ GH token)
  search.py fetch  <url> [--max-chars 20000] [--start 0]
                                              page/PDF -> plain text (arXiv abs -> PDF)
"""
import argparse
import html
import json
import os
import ipaddress
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from html.parser import HTMLParser

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"


def check_url(url):
    """Only plain http(s) to public hosts: refuse file:// and other schemes, and refuse
    loopback / link-local / private addresses (e.g. the EC2 metadata service)."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        sys.exit("Refusing URL (only http/https allowed): %s" % url)
    try:
        addrs = {ai[4][0] for ai in socket.getaddrinfo(parsed.hostname, None)}
    except socket.gaierror as e:
        sys.exit("Cannot resolve %s: %s" % (parsed.hostname, e))
    for a in addrs:
        ip = ipaddress.ip_address(a.split("%")[0])
        if ip.is_loopback or ip.is_link_local or ip.is_private or ip.is_reserved:
            sys.exit("Refusing URL that resolves to a non-public address: %s" % url)


def get(url, timeout=30, headers=None):
    """GET -> (body bytes, content-type). Uses curl when present (retries, robust on
    large PDFs where urllib can hit IncompleteRead), else urllib."""
    check_url(url)
    h = {"User-Agent": UA}
    h.update(headers or {})
    if shutil.which("curl"):
        cmd = ["curl", "-sSL", "--proto", "=http,https", "--proto-redir", "=http,https",
               "--retry", "3", "--compressed", "-m", str(timeout),
               "-w", "\n%{content_type}\n%{http_code}"]
        for k, v in h.items():
            cmd += ["-H", "%s: %s" % (k, v)]
        r = subprocess.run(cmd + [url], capture_output=True)
        if r.returncode != 0:
            raise urllib.error.URLError(r.stderr.decode("utf-8", "replace").strip())
        body, ctype, code = r.stdout.rsplit(b"\n", 2)
        if int(code or 0) >= 400:
            raise urllib.error.HTTPError(url, int(code), "", None, None)
        return body, ctype.decode()
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:  # nosec B310 - scheme checked in check_url
        return r.read(), r.headers.get("Content-Type", "")


def clean(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", s or ""))).strip()


def web(query, n):
    body, _ = get("https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query))
    page = body.decode("utf-8", "replace")
    blocks = re.findall(r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>(.*?)(?=<a[^>]*class="result__a"|$)',
                        page, re.S)
    out = 0
    for href, title, rest in blocks:
        m = re.search(r"uddg=([^&]+)", href)
        url = urllib.parse.unquote(m.group(1)) if m else html.unescape(href)
        if "duckduckgo.com/y.js" in url:          # ads
            continue
        sn = re.search(r'class="result__snippet"[^>]*>(.*?)</a>', rest, re.S)
        print("%d. %s\n   %s\n   %s\n" % (out + 1, clean(title), url, clean(sn.group(1)) if sn else ""))
        out += 1
        if out >= n:
            break
    if not out:
        print("(no results -- DuckDuckGo may be rate-limiting; retry in a minute or rephrase)")


def arxiv(query, n):
    # AND every term (quoted phrases stay together); a bare multi-word query is OR-ish.
    terms = re.findall(r'"[^"]+"|\S+', query)
    q = urllib.parse.quote(" AND ".join("all:" + t for t in terms), safe=":")
    url = ("https://export.arxiv.org/api/query?search_query=%s&max_results=%d"
           "&sortBy=relevance" % (q, n))
    body, _ = get(url, timeout=60)
    ns = {"a": "http://www.w3.org/2005/Atom"}
    root = ET.fromstring(body)
    entries = root.findall("a:entry", ns)
    if not entries:
        print("(no results)")
    for i, e in enumerate(entries, 1):
        aid = e.findtext("a:id", "", ns).rsplit("/abs/", 1)[-1]
        title = clean(e.findtext("a:title", "", ns))
        date = e.findtext("a:published", "", ns)[:10]
        authors = ", ".join(a.findtext("a:name", "", ns) for a in e.findall("a:author", ns)[:6])
        abstract = clean(e.findtext("a:summary", "", ns))
        print("%d. [%s] %s (%s)\n   %s\n   https://arxiv.org/abs/%s\n   %s\n"
              % (i, aid, title, date, authors, aid, abstract[:900]))


def github(query, n, code):
    kind = "code" if code else "repositories"
    url = "https://api.github.com/search/%s?q=%s&per_page=%d" % (kind, urllib.parse.quote(query), n)
    headers = {"Accept": "application/vnd.github+json"}
    tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not tok and shutil.which("gh"):
        try:
            tok = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=10).stdout.strip()
        except Exception:
            tok = None
    if tok:
        headers["Authorization"] = "Bearer " + tok
    elif code:
        sys.exit("GitHub code search needs a token (GITHUB_TOKEN or `gh auth login`).")
    body, _ = get(url, headers=headers)
    items = json.loads(body).get("items", [])
    if not items:
        print("(no results)")
    for i, it in enumerate(items, 1):
        if code:
            print("%d. %s : %s\n   %s\n" % (i, it["repository"]["full_name"], it["path"], it["html_url"]))
        else:
            print("%d. %s  (stars %s, updated %s)\n   %s\n   %s\n" % (
                i, it["full_name"], it.get("stargazers_count"), (it.get("pushed_at") or "")[:10],
                it.get("html_url"), it.get("description") or ""))


class _Text(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "head", "nav", "footer"}
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "section", "article", "table"}

    def __init__(self):
        super().__init__()
        self.out, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag in self.BLOCK:
            self.out.append("\n")
        if tag in ("h1", "h2", "h3", "h4"):
            self.out.append("#" * int(tag[1]) + " ")
        if tag == "li":
            self.out.append("- ")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


def fetch(url, max_chars, start):
    m = re.match(r"https?://arxiv\.org/abs/([^?#]+)", url)
    if m:
        url = "https://arxiv.org/pdf/" + m.group(1)
    # GitHub blob -> raw file
    m = re.match(r"https://github\.com/([^/]+/[^/]+)/blob/(.+)", url)
    if m:
        url = "https://raw.githubusercontent.com/%s/%s" % (m.group(1), m.group(2))
    body, ctype = get(url, timeout=90)
    if "pdf" in ctype or body[:5] == b"%PDF-":
        if not shutil.which("pdftotext"):
            sys.exit("PDF fetched but pdftotext is not installed (brew install poppler).")
        with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
            f.write(body)
            f.flush()
            text = subprocess.run(["pdftotext", "-layout", f.name, "-"], capture_output=True,
                                  text=True).stdout
    elif "html" in ctype or body.lstrip()[:1] == b"<":
        p = _Text()
        p.feed(body.decode("utf-8", "replace"))
        text = "".join(p.out)
    else:
        text = body.decode("utf-8", "replace")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    total = len(text)
    chunk = text[start:start + max_chars]
    print("[source: %s | %d chars total | showing %d..%d]\n" % (url, total, start, start + len(chunk)))
    print(chunk)
    if start + max_chars < total:
        print("\n[... truncated; continue with --start %d]" % (start + max_chars))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("web", "arxiv", "github"):
        s = sub.add_parser(name)
        s.add_argument("query")
        s.add_argument("-n", type=int, default=10)
        if name == "github":
            s.add_argument("--code", action="store_true")
    f = sub.add_parser("fetch")
    f.add_argument("url")
    f.add_argument("--max-chars", type=int, default=20000)
    f.add_argument("--start", type=int, default=0)
    a = ap.parse_args()
    try:
        if a.cmd == "web":
            web(a.query, a.n)
        elif a.cmd == "arxiv":
            arxiv(a.query, a.n)
        elif a.cmd == "github":
            github(a.query, a.n, a.code)
        else:
            fetch(a.url, a.max_chars, a.start)
    except urllib.error.HTTPError as e:
        sys.exit("HTTP %s for %s" % (e.code, e.url))
    except urllib.error.URLError as e:
        sys.exit("Network error: %s" % e.reason)


if __name__ == "__main__":
    main()

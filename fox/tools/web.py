"""Open websites, search the web and read pages."""

from __future__ import annotations

import ipaddress
import re
import socket
import webbrowser
from urllib.parse import urljoin, urlparse

import httpx

from fox.core.tooling import ToolError, fox_tool

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Fox/0.1"


def normalize_url(url: str) -> str:
    url = url.strip()
    # "localhost:3000" has no scheme, but "javascript:alert(1)" does.
    has_scheme = re.match(r"[a-zA-Z][a-zA-Z0-9+.-]*:(?!\d)", url)
    if not has_scheme:
        url = f"https://{url}"
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ToolError(f"Only http and https web addresses are allowed, not '{url}'.")
    return url


def host_of(url: str) -> str:
    return (urlparse(normalize_url(url)).hostname or "").removeprefix("www.")


def _require_public(url: str) -> None:
    """Pages can't use Fox to reach the local network, including Fox's own API."""
    host = urlparse(url).hostname or ""
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(host, None)}
    except socket.gaierror as exc:
        raise ToolError(f"Could not find {host}: {exc}") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address.split("%")[0])
        if not ip.is_global:
            raise ToolError(f"{host} is a private or local address, which fetch_webpage doesn't read.")


@fox_tool(scope=lambda a: host_of(a["url"]), describe=lambda a: f"Open website: {normalize_url(a['url'])}")
def open_website(url: str) -> str:
    """Open a website in the user's default browser."""
    target = normalize_url(url)
    if not webbrowser.open(target):
        raise ToolError("Windows could not open the browser.")
    return f"Opened {target}."


@fox_tool(describe=lambda a: f"Search the web for: {a['query']}", untrusted=True, timeout=30)
def web_search(query: str, max_results: int = 5) -> str:
    """Search the web. Returns titles, links and snippets."""
    from ddgs import DDGS

    results = DDGS().text(query, max_results=min(max_results, 15))
    if not results:
        return "No results."
    return "\n\n".join(f"{r.get('title')}\n{r.get('href')}\n{r.get('body')}" for r in results)


@fox_tool(
    scope=lambda a: host_of(a["url"]),
    describe=lambda a: f"Read web page: {normalize_url(a['url'])}",
    untrusted=True,
    timeout=45,
)
def fetch_webpage(url: str) -> str:
    """Download a public web page and return its readable text."""
    from bs4 import BeautifulSoup

    target = normalize_url(url)
    with httpx.Client(timeout=20, headers={"User-Agent": USER_AGENT}, follow_redirects=False) as client:
        for _ in range(5):
            _require_public(target)
            response = client.get(target)
            if not response.is_redirect:
                break
            target = normalize_url(urljoin(target, response.headers.get("location", "")))
        else:
            raise ToolError("Too many redirects.")
    response.raise_for_status()
    if "html" not in response.headers.get("content-type", "html"):
        return response.text
    soup = BeautifulSoup(response.text, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "nav", "footer"]):
        tag.decompose()
    lines = (line.strip() for line in soup.get_text("\n").splitlines())
    return f"URL: {target}\n" + "\n".join(line for line in lines if line)

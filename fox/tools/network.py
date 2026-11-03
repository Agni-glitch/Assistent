"""Analyse web traffic: requests a browser extension sent to Fox, or a HAR file exported from DevTools.

Captured traffic is full of secrets, so they are removed before anything is stored: auth and cookie
headers, tokens in addresses, and passwords in request bodies. What remains still comes from web
pages, so every tool here marks its output as untrusted.
"""

from __future__ import annotations

import base64
import json
import re
from collections import Counter, defaultdict
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from fox.config import get_settings
from fox.core.permissions import Risk
from fox.core.storage import get_store, now
from fox.core.tooling import ToolError, fox_tool
from fox.tools.files import check_readable, resolve

SENSITIVE_HEADERS = {
    "authorization", "proxy-authorization", "cookie", "set-cookie", "x-api-key", "api-key", "x-auth-token",
    "x-csrf-token", "x-xsrf-token", "x-amz-security-token", "x-goog-api-key",
}  # fmt: skip
SENSITIVE_PARAMS = {
    "access_token", "id_token", "refresh_token", "token", "api_key", "apikey", "key", "password", "passwd",
    "secret", "client_secret", "sig", "signature", "auth", "code", "session", "sessionid",
}  # fmt: skip
_JSON_SECRET = re.compile(
    r'("(?:password|passwd|pwd|secret|client_secret|access_token|refresh_token|id_token|api_key|apikey|token)"\s*:\s*)"(?:[^"\\]|\\.)*"',
    re.IGNORECASE,
)
_FORM = re.compile(r"[^=&\s]+=[^&\s]*(?:&[^=&\s]+=[^&\s]*)*")  # a submitted form: a=1&b=2
MAX_BODY = 100_000  # characters kept per body
MAX_BATCH = 1000  # entries per request to /api/captures
MAX_HAR_BYTES = 300 * 1024 * 1024
SHOWN_BODY = 8000  # characters of a body shown to the model at once


def _removed(value: str) -> str:
    return f"[removed by Fox, {len(value)} characters]"


def clean_url(url: str) -> str:
    """Drop tokens and passwords from the query string, keeping the parameter names."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return url[:2000]
    if not parts.query:
        return url
    query = [(k, "[removed]" if k.lower() in SENSITIVE_PARAMS else v) for k, v in parse_qsl(parts.query, keep_blank_values=True)]
    return urlunsplit(parts._replace(query=urlencode(query, safe="[]")))


def clean_headers(raw: Any) -> dict[str, str]:
    """Accepts {name: value} or HAR's [{name, value}]. Secret values are replaced by their length."""
    pairs = raw.items() if isinstance(raw, dict) else ((h.get("name", ""), h.get("value", "")) for h in raw or [] if isinstance(h, dict))
    headers: dict[str, str] = {}
    for name, value in pairs:
        name, value = str(name)[:200], str(value)[:4000]
        headers[name] = _removed(value) if name.lower() in SENSITIVE_HEADERS else value
    return headers


def clean_body(body: str) -> str:
    body = _JSON_SECRET.sub(r'\1"[removed by Fox]"', body)
    if len(body) < MAX_BODY and _FORM.fullmatch(body):
        pairs = parse_qsl(body, keep_blank_values=True)
        body = urlencode([(k, "[removed]" if k.lower() in SENSITIVE_PARAMS else v) for k, v in pairs])
    if len(body) > MAX_BODY:
        body = body[:MAX_BODY] + f"\n[cut by Fox: {len(body)} characters in total]"
    return body


class CaptureEntry(BaseModel):
    """One request as a browser extension sends it. See docs/browser-capture.md for the format."""

    model_config = ConfigDict(extra="ignore")

    url: str = Field(min_length=1, max_length=20000)
    method: str = Field(default="GET", max_length=20)
    status: int = 0
    mime: str = Field(default="", max_length=200)
    page: str = Field(default="", max_length=20000)
    duration_ms: float | None = None
    size: int | None = None
    error: str = Field(default="", max_length=2000)
    request_headers: dict[str, Any] | list[dict[str, Any]] = {}
    response_headers: dict[str, Any] | list[dict[str, Any]] = {}
    request_body: str = ""
    response_body: str = ""
    at: str = ""

    @field_validator("request_body", "response_body", mode="before")
    @classmethod
    def _text(cls, value: Any) -> str:
        return value if isinstance(value, str) else "" if value is None else json.dumps(value)


def clean_entry(entry: CaptureEntry, source: str) -> dict[str, Any]:
    url = clean_url(entry.url)
    return {
        "at": entry.at[:40] or now(),
        "source": source[:60],
        "page": clean_url(entry.page),
        "method": entry.method.upper(),
        "url": url,
        "host": urlsplit(url).hostname or "",
        "status": entry.status,
        "mime": entry.mime.split(";")[0].strip().lower(),
        "duration_ms": entry.duration_ms,
        "size": entry.size,
        "error": entry.error,
        "request_headers": json.dumps(clean_headers(entry.request_headers)),
        "response_headers": json.dumps(clean_headers(entry.response_headers)),
        "request_body": clean_body(entry.request_body),
        "response_body": clean_body(entry.response_body),
    }


def ingest(entries: list[Any], source: str) -> tuple[int, int]:
    """Clean and store entries. Returns (stored, skipped)."""
    rows, skipped = [], 0
    for raw in entries[:MAX_BATCH]:
        try:
            rows.append(clean_entry(CaptureEntry.model_validate(raw), source))
        except ValidationError:
            skipped += 1
    get_store().add_captures(rows, get_settings().fox_max_captures)
    return len(rows), skipped + max(0, len(entries) - MAX_BATCH)


def har_entries(har: Any) -> list[dict[str, Any]]:
    """Turn a HAR file (DevTools, Network, Export HAR) into capture entries."""
    log = har.get("log") if isinstance(har, dict) else None
    if not isinstance(log, dict) or not isinstance(log.get("entries"), list):
        raise ToolError("This isn't a HAR file: it has no log.entries.")
    pages = {p.get("id"): p.get("title", "") for p in log.get("pages") or [] if isinstance(p, dict)}
    out = []
    for item in log["entries"]:
        request, response = item.get("request") or {}, item.get("response") or {}
        content = response.get("content") or {}
        body = content.get("text") or ""
        if content.get("encoding") == "base64":
            try:
                body = base64.b64decode(body).decode("utf-8")
            except ValueError:
                body = f"[binary, {content.get('size', 0)} bytes]"
        size = content.get("size")
        out.append({
            "url": request.get("url", ""),
            "method": request.get("method", "GET"),
            "status": response.get("status") or 0,
            "mime": content.get("mimeType", ""),
            "page": pages.get(item.get("pageref"), ""),
            "duration_ms": item.get("time"),
            "size": size if isinstance(size, int) and size >= 0 else None,
            "error": response.get("_error") or "",
            "request_headers": request.get("headers") or [],
            "response_headers": response.get("headers") or [],
            "request_body": (request.get("postData") or {}).get("text", ""),
            "response_body": body,
            "at": item.get("startedDateTime", ""),
        })  # fmt: skip
    return out


def import_har(data: bytes, source: str) -> int:
    try:
        har = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ToolError(f"This HAR file isn't valid JSON: {exc}") from exc
    stored, _ = ingest(har_entries(har), source)
    return stored


# --- Formatting ---


def _size(n: int | None) -> str:
    if n is None:
        return "?"
    return f"{n} B" if n < 1024 else f"{n / 1024:.1f} KB" if n < 1024 * 1024 else f"{n / 1024 / 1024:.1f} MB"


def _ms(value: float | None) -> str:
    return "?" if value is None else f"{value:.0f} ms"


def _status(row: dict) -> str:
    return f"failed ({row['error']})" if row.get("error") else str(row.get("status") or "?")


def _line(row: dict) -> str:
    url = row["url"] if len(row["url"]) <= 160 else row["url"][:157] + "..."
    clock = (row.get("at") or "")[11:19]
    return f"[{row['id']}] {clock} {row['method']} {_status(row)} {_ms(row['duration_ms'])} {_size(row['size'])} {url}"


def _failed(row: dict) -> bool:
    return bool(row.get("error")) or (row.get("status") or 0) >= 400


# --- Tools ---


@fox_tool(
    describe=lambda a: "Read captured network requests" + (f" matching '{a['url_contains']}'" if a["url_contains"] else ""),
    untrusted=True,
)
def list_network_requests(url_contains: str = "", method: str = "", only_failures: bool = False, limit: int = 50) -> str:
    """List requests captured from the browser (by a browser extension or an imported HAR file), oldest first.

    Filter by part of the address, HTTP method, or failures only (status 400+ or a network error).
    Each line starts with an id for get_network_request.
    """
    rows = get_store().list_captures(url_contains, method, 400 if only_failures else 0, max(1, min(limit, 300)))
    if not rows:
        total = get_store().count_captures()
        return "No captured requests match." if total else "Nothing has been captured yet. Send requests from the browser extension or import a HAR file."
    return "\n".join(_line(row) for row in rows)


@fox_tool(describe=lambda a: f"Read captured network request {a['request_id']}, with its headers and bodies", untrusted=True)
def get_network_request(request_id: int, body_start: int = 0) -> str:
    """Show one captured request in full: headers, request body and response body.

    Bodies longer than 8000 characters are shown in parts; pass body_start to read further.
    """
    row = get_store().get_capture(request_id)
    if row is None:
        raise ToolError(f"No captured request has id {request_id}.")
    lines = [
        f"{row['method']} {row['url']}",
        f"Status: {_status(row)}   Time: {_ms(row['duration_ms'])}   Size: {_size(row['size'])}   Type: {row['mime'] or '?'}",
        f"Captured: {row['at']}  from: {row['source']}" + (f"   Page: {row['page']}" if row["page"] else ""),
    ]
    for title, key in (("Request headers", "request_headers"), ("Response headers", "response_headers")):
        headers = json.loads(row[key] or "{}")
        lines += ["", f"{title}:", *(f"  {k}: {v}" for k, v in headers.items())] if headers else ["", f"{title}: none recorded"]
    for title, key in (("Request body", "request_body"), ("Response body", "response_body")):
        body = row[key] or ""
        if not body:
            continue
        part = body[body_start : body_start + SHOWN_BODY]
        lines += ["", f"{title} ({len(body)} characters):", part]
        if body_start + SHOWN_BODY < len(body):
            lines.append(f"[more: call again with body_start={body_start + SHOWN_BODY}]")
    return "\n".join(lines)


@fox_tool(
    describe=lambda a: "Summarise captured network traffic" + (f" matching '{a['url_contains']}'" if a["url_contains"] else ""),
    untrusted=True,
)
def summarize_network_traffic(url_contains: str = "") -> str:
    """Summarise captured traffic: hosts, status codes, failures, slowest and largest requests, and likely problems.

    Start here when asked to analyse requests and responses, then use get_network_request for details.
    """
    rows = get_store().list_captures(url_contains, limit=100_000)
    if not rows:
        return "Nothing captured" + (f" matching '{url_contains}'." if url_contains else " yet.")

    out = [f"{len(rows)} requests from {rows[0]['at']} to {rows[-1]['at']}."]
    classes = Counter("failed" if r["error"] else f"{(r['status'] or 0) // 100}xx" for r in rows)
    out.append("Status: " + ", ".join(f"{k}: {v}" for k, v in sorted(classes.items())))

    hosts: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        hosts[row["host"]].append(row)
    out.append("\nBusiest hosts:")
    for host, items in sorted(hosts.items(), key=lambda kv: -len(kv[1]))[:12]:
        times = [r["duration_ms"] for r in items if r["duration_ms"] is not None]
        average = f", average {sum(times) / len(times):.0f} ms" if times else ""
        out.append(f"- {host or '?'}: {len(items)} requests, {sum(map(_failed, items))} failed{average}")

    failures = [r for r in rows if _failed(r)]
    if failures:
        out.append(f"\nFailures ({len(failures)}):")
        out += [f"- {_line(r)}" for r in failures[:20]]
    timed = sorted((r for r in rows if r["duration_ms"] is not None), key=lambda r: -r["duration_ms"])
    if timed:
        out.append("\nSlowest:")
        out += [f"- {_line(r)}" for r in timed[:8]]
    sized = sorted((r for r in rows if r["size"]), key=lambda r: -r["size"])
    if sized:
        out.append("\nLargest:")
        out += [f"- {_line(r)}" for r in sized[:8]]
    types = Counter(r["mime"] or "unknown" for r in rows)
    out.append("\nContent types: " + ", ".join(f"{k} ({v})" for k, v in types.most_common(8)))

    notes = []
    insecure = [r for r in rows if r["url"].startswith("http://") and r["page"].startswith("https://")]
    if insecure:
        notes.append(f"{len(insecure)} plain http request(s) from an https page (mixed content), such as [{insecure[0]['id']}].")
    repeats = Counter((r["method"], r["url"]) for r in rows)
    for (method, url), count in repeats.most_common(3):
        if count >= 3:
            notes.append(f"{method} {url[:120]} was requested {count} times.")
    cors = [r for r in rows if "cors" in (r["error"] or "").lower() or "blocked" in (r["error"] or "").lower()]
    if cors:
        notes.append(f"{len(cors)} request(s) blocked by the browser (CORS or a blocker), such as [{cors[0]['id']}].")
    huge = [r for r in rows if (r["size"] or 0) > 2 * 1024 * 1024]
    if huge:
        notes.append(f"{len(huge)} response(s) over 2 MB.")
    if notes:
        out.append("\nWorth a look:")
        out += [f"- {n}" for n in notes]
    return "\n".join(out)


def _har_path(a: dict):
    target = check_readable(resolve(a["path"]))
    if not target.is_file():
        raise ToolError(f"File not found: {target}")
    if target.stat().st_size > MAX_HAR_BYTES:
        raise ToolError(f"{target.name} is larger than {MAX_HAR_BYTES // 1024 // 1024} MB.")
    return target


@fox_tool(
    scope=lambda a: str(resolve(a["path"]).parent),
    describe=lambda a: f"Import a HAR file into captured traffic: {_har_path(a)}",
    untrusted=True,
    timeout=120,
)
def import_har_file(path: str) -> str:
    """Import a HAR file (from Chrome DevTools: Network, Export HAR) so the network tools can analyse it."""
    target = _har_path({"path": path})
    stored = import_har(target.read_bytes(), f"har:{target.name}")
    return f"Imported {stored} requests from {target.name}.\n\n" + summarize_network_traffic()


@fox_tool(risk=Risk.NORMAL, describe=lambda a: f"Delete all {get_store().count_captures()} captured network requests")
def clear_network_requests() -> str:
    """Delete every captured network request."""
    get_store().clear_captures()
    return "Cleared the captured traffic."


def capture_stats() -> dict[str, Any]:
    store = get_store()
    latest = store.list_captures(limit=1)
    return {"count": store.count_captures(), "latest": latest[0]["at"] if latest else None}

# Browser capture: analyse requests and responses

Fox can analyse the network traffic of a web page: which requests failed and why, what an API returned, what is slow or large, and what looks wrong. The traffic gets to Fox in one of two ways:

- **A browser extension** sends each request and response to Fox while you browse. A ready-to-use Chrome extension is in [`extensions/chrome-capture`](../extensions/chrome-capture/). You can also build your own and post to the same API.
- **A HAR file** exported from Chrome DevTools. Attach it in chat, or ask Fox to import it from a path.

Then ask, for example:

- *"Summarise the captured traffic. What failed?"*
- *"Why does the POST to /api/orders return 422? Show me the request body."*
- *"Which requests to api.example.com took longer than a second?"*
- *"Compare the responses of the two calls to /graphql."*

- [What Fox stores, and what it removes](#what-fox-stores-and-what-it-removes)
- [Using the Chrome extension](#using-the-chrome-extension)
- [Using a HAR file](#using-a-har-file)
- [The network tools](#the-network-tools)
- [Building your own extension](#building-your-own-extension)
- [Writing your own analysis](#writing-your-own-analysis)
- [Limits](#limits)

## What Fox stores, and what it removes

Captured traffic is full of secrets, so Fox cleans every request **before** it's stored:

| Removed | How |
|---|---|
| Auth and cookie headers | `Authorization`, `Cookie`, `Set-Cookie`, `X-Api-Key`, CSRF tokens and similar. The header stays, with its value replaced by its length. |
| Tokens in addresses | Query parameters such as `access_token`, `token`, `key`, `code`, `password` and `signature`. |
| Passwords in request bodies | JSON fields such as `"password"` and `"client_secret"`, and the same names in submitted forms. |

This is a best effort, not a guarantee: a secret under an unusual name gets through. Captures are kept in Fox's local database (`data/fox.db`), which tools can't read directly. The newest `FOX_MAX_CAPTURES` (5000) are kept. Delete them any time in **Settings → Browser capture**, under **Control → Reset**, or by asking Fox.

Whatever the model reads goes to your AI provider, like any other tool result. Everything the network tools return is marked as untrusted, so instructions hidden in a response body aren't followed.

## Using the Chrome extension

1. In Chrome, open `chrome://extensions`, turn on **Developer mode**, click **Load unpacked** and choose the `extensions/chrome-capture` folder.
2. In Fox, open **Settings → Browser capture**. Copy the **Fox address** and the **Access token**.
3. Click the extension's icon, paste both, and click **Test connection**. It should say *Connected to Fox*.
4. Open the page you want to analyse and click **Capture this tab**. Chrome shows a bar saying the extension is debugging the tab. That's expected: the debugger is the only way an extension can read response bodies.
5. Use the page, then click **Stop capturing**. Requests are sent to Fox every two seconds, and the count in Fox's Settings goes up.
6. Ask Fox about them in chat.

**Include response bodies** sends text bodies (JSON, HTML, JavaScript, XML and so on) up to 100,000 characters each. Turn it off to send headers and timings only.

The desktop app listens on `FOX_SERVER_PORT` (`8765`) when that port is free, so the address stays the same between runs. If another program holds the port, Fox picks a free one, and Settings shows the address to use.

The token gives full control of Fox, so keep it private. The extension stores it in Chrome's local extension storage.

## Using a HAR file

In Chrome DevTools, open **Network**, reproduce the problem, then click **Export HAR** (the download arrow). Then either:

- attach the `.har` file in chat with **+**, or by dragging it into the window. Fox imports it and tells the model how many requests arrived; or
- ask *"Import C:\Users\me\Downloads\site.har and tell me what failed"*. The `import_har_file` tool asks permission to read the file.

HAR files go through the same cleaning as extension captures. Recent versions of Chrome also leave cookies and auth headers out of the export unless you choose to include sensitive data.

## The network tools

| Tool | Risk | What it does |
|---|---|---|
| `summarize_network_traffic` | normal | Hosts, status codes, failures, the slowest and largest requests, and likely problems such as mixed content, blocked requests and repeated calls. |
| `list_network_requests` | normal | One line per request, filtered by part of the address, method, or failures only. Each line has an id. |
| `get_network_request` | normal | One request in full: headers, request body and response body, shown in parts if long. |
| `import_har_file` | normal | Imports a HAR file from disk. |
| `clear_network_requests` | normal | Deletes every capture. |

They ask permission like other normal tools; choose **Always allow** if you don't want to be asked again.

## Building your own extension

Any extension, script or tool can send requests to Fox. Post JSON to `/api/captures` with the token:

```http
POST http://127.0.0.1:8765/api/captures
Authorization: Bearer <token from Settings, Browser capture>
Content-Type: application/json

{
  "source": "my-extension",
  "entries": [
    {
      "url": "https://api.example.com/orders?page=2",
      "method": "POST",
      "status": 422,
      "mime": "application/json",
      "page": "https://shop.example.com/checkout",
      "at": "2026-11-03T10:15:02.120Z",
      "duration_ms": 184.5,
      "size": 512,
      "error": "",
      "request_headers": {"Content-Type": "application/json"},
      "response_headers": [{"name": "Content-Type", "value": "application/json"}],
      "request_body": "{\"item\": 7}",
      "response_body": "{\"error\": \"quantity is required\"}"
    }
  ]
}
```

Only `url` is required. Headers can be an object or HAR-style `[{name, value}]` pairs. `error` is for requests that never got a response, such as `net::ERR_BLOCKED_BY_CLIENT`. Up to 1000 entries per request; entries that don't fit the format are skipped and counted. The answer is:

```json
{"stored": 1, "skipped": 0, "count": 241, "latest": "2026-11-03T10:15:02.120Z"}
```

Notes for extension authors:

- **CORS.** A Manifest V3 extension with `"host_permissions": ["http://127.0.0.1/*"]` can call Fox from its service worker or popup without CORS. A normal web page can't, and shouldn't be able to.
- **Bodies.** `chrome.webRequest` can't read response bodies in Manifest V3. Use `chrome.debugger` with the `Network` domain as the included extension does ([`background.js`](../extensions/chrome-capture/background.js)), or have DevTools export a HAR.
- **Batching.** Send every second or two rather than one request per call.
- **Secrets.** Fox cleans what it receives, but it's better not to send what you don't need.

`GET /api/captures?url_contains=&limit=100` lists what's stored (without bodies), and `DELETE /api/captures` clears it. See [Local API](api.md#network-captures).

## Writing your own analysis

The built-in tools cover general questions. For checks specific to your app, such as validating every response against a schema or finding calls without a trace header, write a [plugin](plugins.md) that reads the same store:

```python
import json

from fox.core.storage import get_store
from fox.core.tooling import fox_tool


@fox_tool(describe=lambda a: "Find captured API calls without an X-Request-Id header", untrusted=True)
def requests_missing_trace_id(url_contains: str = "/api/") -> str:
    """List captured API calls whose request had no X-Request-Id header."""
    missing = []
    for row in get_store().list_captures(url_contains, limit=5000, with_bodies=True):
        headers = {k.lower() for k in json.loads(row["request_headers"] or "{}")}
        if "x-request-id" not in headers:
            missing.append(f"[{row['id']}] {row['method']} {row['url']}")
    return "\n".join(missing) or "Every captured API call had an X-Request-Id."
```

## Limits

- Only text bodies are kept, up to 100,000 characters each. Images, fonts and other binary responses keep their headers, size and timing.
- WebSocket frames and server-sent events aren't captured.
- Secret removal works by name, so it can miss secrets with unusual names. Don't share a database or HAR file without checking it.
- The included extension captures one tab at a time per click, and stops when the tab closes or you close the debugging bar.

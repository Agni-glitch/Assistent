// Records a tab's network traffic with the Chrome DevTools protocol and sends it to Fox in batches.
//
// chrome.debugger is the only extension API that can read response bodies. While it's attached,
// Chrome shows a bar saying the tab is being debugged, so capturing is never invisible.
// Fox removes cookies, auth headers and passwords on arrival; this file sends what Chrome reports.

const PROTOCOL = "1.3";
const FLUSH_MS = 2000;
const MAX_QUEUE = 2000;
const MAX_BODY = 100000;
const TEXT_TYPES = /^(text\/|application\/(json|javascript|xml|x-www-form-urlencoded|graphql|ld\+json|problem\+json)|image\/svg)/;

const capturing = new Map(); // tabId -> { page, requests: Map(requestId -> entry) }
let queue = [];
let sent = 0;
let lastError = "";

const settings = async () => ({
  address: "http://127.0.0.1:8765",
  token: "",
  bodies: true,
  ...(await chrome.storage.local.get(["address", "token", "bodies"])),
});

function finish(tab, requestId) {
  const entry = tab.requests.get(requestId);
  if (!entry) return;
  tab.requests.delete(requestId);
  delete entry.start;
  queue.push(entry);
  if (queue.length > MAX_QUEUE) queue = queue.slice(-MAX_QUEUE);
}

async function onEvent(source, method, params) {
  const tab = capturing.get(source.tabId);
  if (!tab) return;
  const requestId = params.requestId;

  if (method === "Network.requestWillBeSent") {
    if (params.redirectResponse) {
      // The same id continues after a redirect: close the hop that just ended.
      const hop = tab.requests.get(requestId);
      if (hop) {
        hop.status = params.redirectResponse.status;
        hop.response_headers = params.redirectResponse.headers;
        hop.duration_ms = (params.timestamp - hop.start) * 1000;
        finish(tab, requestId);
      }
    }
    if (params.documentURL && params.type === "Document") tab.page = params.documentURL;
    tab.requests.set(requestId, {
      url: params.request.url,
      method: params.request.method,
      request_headers: params.request.headers,
      request_body: params.request.postData ?? "",
      page: tab.page,
      at: new Date(params.wallTime * 1000).toISOString(),
      start: params.timestamp,
    });
  } else if (method === "Network.responseReceived") {
    const entry = tab.requests.get(requestId);
    if (!entry) return;
    entry.status = params.response.status;
    entry.mime = params.response.mimeType;
    entry.response_headers = params.response.headers;
  } else if (method === "Network.loadingFinished") {
    const entry = tab.requests.get(requestId);
    if (!entry) return;
    entry.duration_ms = (params.timestamp - entry.start) * 1000;
    entry.size = params.encodedDataLength;
    const { bodies } = await settings();
    if (bodies && TEXT_TYPES.test(entry.mime ?? "")) {
      try {
        const result = await chrome.debugger.sendCommand({ tabId: source.tabId }, "Network.getResponseBody", { requestId });
        const body = result.base64Encoded ? atob(result.body) : result.body;
        entry.response_body = body.slice(0, MAX_BODY);
      } catch {
        // Some bodies are gone by the time we ask, such as those of a page that navigated away.
      }
    }
    finish(tab, requestId);
  } else if (method === "Network.loadingFailed") {
    const entry = tab.requests.get(requestId);
    if (!entry) return;
    entry.duration_ms = (params.timestamp - entry.start) * 1000;
    const reason = params.blockedReason || params.corsErrorStatus?.corsError;
    entry.error = [params.errorText, reason && `blocked: ${reason}`].filter(Boolean).join(", ");
    finish(tab, requestId);
  }
}

async function flush() {
  if (!queue.length) return;
  const { address, token } = await settings();
  if (!token) {
    lastError = "Add Fox's token in the popup.";
    return;
  }
  const batch = queue.slice(0, 500);
  try {
    const response = await fetch(`${address.replace(/\/$/, "")}/api/captures`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
      body: JSON.stringify({ entries: batch, source: "chrome-extension" }),
    });
    if (!response.ok) throw new Error(response.status === 401 ? "Fox refused the token." : `Fox answered ${response.status}.`);
    queue = queue.slice(batch.length);
    sent += batch.length;
    lastError = "";
  } catch (error) {
    lastError = error.message.includes("fetch") ? "Can't reach Fox. Is it open, and is the address right?" : error.message;
  }
}

setInterval(flush, FLUSH_MS);

async function start(tabId) {
  if (capturing.has(tabId)) return;
  const tab = await chrome.tabs.get(tabId);
  await chrome.debugger.attach({ tabId }, PROTOCOL);
  capturing.set(tabId, { page: tab.url ?? "", requests: new Map() });
  await chrome.debugger.sendCommand({ tabId }, "Network.enable", { maxPostDataSize: 65536 });
}

async function stop(tabId) {
  capturing.delete(tabId);
  try {
    await chrome.debugger.detach({ tabId });
  } catch {
    // Already detached, for example because the tab closed.
  }
  await flush();
}

chrome.debugger.onEvent.addListener((source, method, params) => {
  onEvent(source, method, params);
});
chrome.debugger.onDetach.addListener((source) => capturing.delete(source.tabId));
chrome.tabs.onRemoved.addListener((tabId) => capturing.delete(tabId));

chrome.runtime.onMessage.addListener((message, _sender, reply) => {
  (async () => {
    if (message.cmd === "start") await start(message.tabId);
    if (message.cmd === "stop") await stop(message.tabId);
    if (message.cmd === "flush") await flush();
    return { capturing: [...capturing.keys()], sent, queued: queue.length, lastError };
  })().then(reply, (error) => reply({ error: error.message, capturing: [...capturing.keys()], sent, queued: queue.length, lastError }));
  return true; // the reply comes later
});

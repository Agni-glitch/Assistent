const $ = (id) => document.getElementById(id);
let tabId = null;

async function load() {
  const saved = await chrome.storage.local.get(["address", "token", "bodies"]);
  $("address").value = saved.address ?? "http://127.0.0.1:8765";
  $("token").value = saved.token ?? "";
  $("bodies").checked = saved.bodies ?? true;
  [tabId] = (await chrome.tabs.query({ active: true, currentWindow: true })).map((tab) => tab.id);
  show(await chrome.runtime.sendMessage({ cmd: "status" }));
}

const save = () =>
  chrome.storage.local.set({ address: $("address").value.trim(), token: $("token").value.trim(), bodies: $("bodies").checked });

function show(status, note = "") {
  const on = status.capturing.includes(tabId);
  $("toggle").textContent = on ? "Stop capturing" : "Capture this tab";
  $("toggle").classList.toggle("on", on);
  const parts = [on ? "Capturing this tab." : "Not capturing this tab.", `${status.sent} sent to Fox, ${status.queued} waiting.`];
  const problem = status.error || status.lastError;
  $("status").textContent = [note, ...parts, problem].filter(Boolean).join(" ");
  $("status").classList.toggle("bad", Boolean(problem));
}

$("address").onchange = save;
$("token").onchange = save;
$("bodies").onchange = save;

$("toggle").onclick = async () => {
  await save();
  const status = await chrome.runtime.sendMessage({ cmd: "status" });
  const cmd = status.capturing.includes(tabId) ? "stop" : "start";
  show(await chrome.runtime.sendMessage({ cmd, tabId }));
};

$("test").onclick = async () => {
  await save();
  try {
    const response = await fetch(`${$("address").value.trim().replace(/\/$/, "")}/api/status`, {
      headers: { Authorization: `Bearer ${$("token").value.trim()}` },
    });
    const note = response.ok ? `Connected to Fox ${(await response.json()).version}.` : response.status === 401 ? "Fox refused the token." : `Fox answered ${response.status}.`;
    show(await chrome.runtime.sendMessage({ cmd: "flush" }), note);
  } catch {
    show(await chrome.runtime.sendMessage({ cmd: "status" }), "Can't reach Fox. Is it open, and is the address right?");
  }
};

load();

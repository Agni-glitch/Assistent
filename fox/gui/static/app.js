// Fox's window. Talks to the local API over REST and a WebSocket. See README: "Local API".
import { renderMarkdown } from "./markdown.js";

const $ = (id) => document.getElementById(id);
const el = (tag, props = {}, ...children) => {
  const node = Object.assign(document.createElement(tag), props);
  node.append(...children);
  return node;
};

// --- Token and API ---

function readToken() {
  const match = location.hash.match(/token=([^&]+)/);
  if (match) {
    sessionStorage.setItem("fox-token", match[1]);
    history.replaceState(null, "", location.pathname); // keep it out of the visible address
    return match[1];
  }
  return sessionStorage.getItem("fox-token");
}

const token = readToken();

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { Authorization: `Bearer ${token}`, ...(options.body ? { "Content-Type": "application/json" } : {}) },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(typeof body.detail === "string" ? body.detail : `Request failed (${response.status})`);
  }
  return response.json();
}
const send = (path, method, body) => api(path, { method, body: body === undefined ? undefined : JSON.stringify(body) });

function toast(message, bad = false) {
  const node = el("div", { className: `toast${bad ? " bad" : ""}`, textContent: message });
  $("toasts").append(node);
  setTimeout(() => node.remove(), bad ? 8000 : 4000);
}
const guarded = (work) => async (...args) => {
  try {
    return await work(...args);
  } catch (error) {
    toast(error.message, true);
  }
};

function confirmDelete(text) {
  return new Promise((resolve) => {
    const dialog = $("confirm-dialog");
    $("confirm-text").textContent = text;
    const finish = (answer) => {
      dialog.close();
      resolve(answer);
    };
    $("confirm-yes").onclick = () => finish(true);
    $("confirm-no").onclick = () => finish(false);
    dialog.oncancel = () => resolve(false);
    dialog.showModal();
    $("confirm-no").focus();
  });
}

// --- State ---

const state = {
  sessionId: null,
  busy: false,
  voice: "idle",
  wake: false,
  status: null,
  control: { kill_switch: false, disabled: [] },
  prefs: { theme: "system", speak: false, model: "" },
  socket: null,
  reply: null, // the reply being streamed: { node, text }
  activity: new Map(), // tool name -> the row waiting for its result
  prompt: null,
  retry: 500,
  attachments: [], // files attached to the message being written: { id, name, kind, size, pending, preview }
  models: null,
};

// --- Navigation ---

const loaders = {
  chat: () => loadSessions(),
  agents: () => loadAgents(),
  models: () => loadModels(),
  permissions: () => loadPermissions(),
  control: () => loadControl(),
  memory: () => loadMemory(),
  settings: () => loadSettings(),
};

function showPage(name) {
  document.querySelectorAll(".page").forEach((page) => page.classList.toggle("active", page.id === `page-${name}`));
  document.querySelectorAll(".rail-item").forEach((item) => {
    const active = item.dataset.page === name;
    item.classList.toggle("active", active);
    if (active) item.setAttribute("aria-current", "page");
    else item.removeAttribute("aria-current");
  });
  guarded(loaders[name])();
}
document.querySelectorAll(".rail-item").forEach((item) => (item.onclick = () => showPage(item.dataset.page)));

// --- Chat view ---

const messages = $("messages");
const nearBottom = () => messages.scrollHeight - messages.scrollTop - messages.clientHeight < 120;
const scrollDown = (force = false) => {
  if (force || nearBottom()) messages.scrollTop = messages.scrollHeight;
};

function showEmptyState() {
  const suggestions = ["What's using the most memory right now?", "Remind me to stretch in 30 minutes", "Open Notepad"];
  const row = el("div", { className: "suggestions" });
  for (const text of suggestions) {
    row.append(el("button", { className: "chip", textContent: text, onclick: () => submit(text) }));
  }
  messages.replaceChildren(
    el("div", { className: "empty" },
      el("img", { src: "/static/fox.svg", alt: "", width: 64, height: 64 }),
      el("h2", { textContent: "What should Fox do?" }),
      el("p", { className: "muted", textContent: "Fox asks before it acts, and you decide each time." }),
      row),
  );
}

function clearEmptyState() {
  messages.querySelector(".empty")?.remove();
}

const formatSize = (bytes) =>
  bytes < 1024 ? `${bytes} B` : bytes < 1048576 ? `${(bytes / 1024).toFixed(0)} KB` : `${(bytes / 1048576).toFixed(1)} MB`;

// Images need the token, so they're fetched and shown from a local blob rather than by address.
const imageUrls = new Map();
async function loadImage(id) {
  if (!imageUrls.has(id)) {
    const response = await fetch(`/api/uploads/${id}/image`, { headers: { Authorization: `Bearer ${token}` } });
    if (!response.ok) throw new Error("This image was deleted.");
    imageUrls.set(id, URL.createObjectURL(await response.blob()));
  }
  return imageUrls.get(id);
}

function attachmentChip(file, onRemove) {
  const chip = el("div", {
    className: `attachment ${file.kind}${file.pending ? " pending" : ""}`,
    title: `${file.name} (${formatSize(file.size)})${file.pending ? ", uploading" : ""}`,
  });
  if (file.kind === "image") {
    const image = el("img", { alt: "" });
    if (file.preview) image.src = file.preview;
    else if (file.id) loadImage(file.id).then((url) => (image.src = url), () => image.remove());
    chip.append(image);
  } else {
    const ext = file.kind === "har" ? "HAR" : (file.name.split(".").pop() || "file").slice(0, 4).toUpperCase();
    chip.append(el("span", { className: "file-icon", textContent: ext }));
  }
  chip.append(el("span", { className: "attachment-name", textContent: file.name }));
  if (onRemove) {
    chip.append(el("button", { className: "attachment-remove", type: "button", textContent: "\u00d7", ariaLabel: `Remove ${file.name}`, onclick: onRemove }));
  }
  return chip;
}

function userMessage(text, attachments = []) {
  const stack = el("div", { className: "user-stack" });
  if (attachments.length) stack.append(el("div", { className: "attachments" }, ...attachments.map((file) => attachmentChip(file))));
  if (text) stack.append(el("div", { className: "bubble", textContent: text }));
  return el("div", { className: "message user" }, stack);
}

function addUserMessage(text, attachments = []) {
  clearEmptyState();
  messages.append(userMessage(text, attachments));
  scrollDown(true);
}

// Tool calls that follow each other share one block, headed by how many are done.
function stepsGroup() {
  const last = messages.lastElementChild;
  if (last?.classList.contains("steps")) return last;
  const group = el("div", { className: "steps" }, el("div", { className: "steps-head" }));
  messages.append(group);
  return group;
}

function updateSteps(group) {
  const rows = [...group.querySelectorAll(":scope > .activity")];
  const count = (kind) => rows.filter((row) => row.classList.contains(kind)).length;
  const done = count("ok");
  const failed = count("failed");
  const running = rows.length - done - failed;
  const parts = [`${rows.length} steps`];
  if (done) parts.push(`${done} done`);
  if (failed) parts.push(`${failed} stopped`);
  if (running) parts.push(`${running} running`);
  const head = group.firstElementChild;
  head.textContent = parts.join(" \u00b7 ");
  head.hidden = rows.length < 2;
  group.classList.toggle("has-failed", failed > 0);
}

function addAssistantMessage(text) {
  const node = el("div", { className: "message assistant markdown" });
  // Everything in `text` is escaped by renderMarkdown before it gets here.
  node.innerHTML = renderMarkdown(text);
  messages.append(node);
  return node;
}

function addNote(text) {
  messages.append(el("div", { className: "message note", textContent: text }));
  scrollDown();
}

function addError(text) {
  clearEmptyState();
  messages.append(el("div", { className: "message error" }, el("div", { className: "bubble", textContent: text })));
  scrollDown(true);
}

function startReply() {
  if (!state.reply) {
    clearEmptyState();
    state.reply = { node: addAssistantMessage(""), text: "" };
    state.reply.node.classList.add("typing");
  }
  return state.reply;
}

function renderReply() {
  const reply = state.reply;
  if (reply) {
    reply.node.innerHTML = renderMarkdown(reply.text);
    scrollDown();
  }
}

// A reply ends where a tool starts, so tool rows and text appear in the order they happened.
function closeReply() {
  if (!state.reply) return;
  state.reply.node.classList.remove("typing");
  if (!state.reply.text.trim()) state.reply.node.remove();
  state.reply = null;
}

function addActivity(tool, summary) {
  closeReply();
  clearEmptyState();
  const label = el("span", { className: "state", textContent: "Running" });
  const row = el("details", { className: "activity" }, el("summary", {}, label, el("span", { textContent: summary })));
  const group = stepsGroup();
  group.append(row);
  updateSteps(group);
  state.activity.set(tool, { row, label, group });
  scrollDown();
  return state.activity.get(tool);
}

function finishActivity({ tool, ok, output }) {
  // A refused action never started, so it has no row yet.
  const entry = state.activity.get(tool) ?? addActivity(tool, tool);
  state.activity.delete(tool);
  entry.row.classList.add(ok ? "ok" : "failed");
  entry.label.textContent = ok ? "Done" : "Stopped";
  if (output) entry.row.append(el("pre", { textContent: output }));
  if (!ok) entry.row.open = true;
  updateSteps(entry.group);
  scrollDown();
}

function setBusy(busy) {
  state.busy = busy;
  updateControls();
}

const VOICE_TEXT = {
  listening: () => (state.wake ? `Listening for "${state.status?.wake_word ?? "fox"}"` : "Listening. Speak now."),
  transcribing: () => "Working out what you said",
  thinking: () => "Thinking",
  speaking: () => "Speaking",
};

function updateControls() {
  const voiceOn = state.voice !== "idle";
  const noVoice = Boolean(state.status?.voice_problem);
  $("send").hidden = state.busy;
  $("stop").hidden = !state.busy;
  $("input").disabled = state.wake;
  $("input").placeholder = state.wake ? "Turn off Hands-free to type" : "Ask Fox to do something";
  $("talk").disabled = noVoice || (state.busy && !voiceOn) || state.wake;
  $("talk").classList.toggle("on", voiceOn && !state.wake);
  $("wake").disabled = noVoice || (state.busy && !state.wake);
  $("wake").setAttribute("aria-pressed", String(state.wake));
  $("new-chat").disabled = state.busy;
  $("attach").disabled = state.wake;
  document.querySelectorAll(".session-open, .session-delete").forEach((button) => (button.disabled = state.busy));
  const text = VOICE_TEXT[state.voice]?.();
  $("voice-status").hidden = !text;
  $("voice-status").textContent = text ?? "";
}

function submit(text) {
  const message = text.trim();
  const files = state.attachments.filter((file) => file.id);
  if ((!message && !files.length) || state.busy) return;
  if (state.attachments.some((file) => file.pending)) {
    toast("Wait a moment: a file is still uploading.");
    return;
  }
  if (state.socket?.readyState !== WebSocket.OPEN) {
    toast("Not connected to Fox yet. Try again in a moment.", true);
    return;
  }
  addUserMessage(message, files);
  setBusy(true);
  state.socket.send(JSON.stringify({
    type: "message",
    text: message,
    session_id: state.sessionId,
    speak: state.prefs.speak,
    attachments: files.map((file) => file.id),
    model: state.prefs.model || null,
  }));
  $("input").value = "";
  state.attachments = [];
  renderTray();
  resizeInput();
  startReply();
}

// --- Attachments ---

const MAX_ATTACHMENTS = 10;

function readAsBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",")[1] ?? "");
    reader.onerror = () => reject(new Error("Could not read the file."));
    reader.readAsDataURL(file);
  });
}

function renderTray() {
  const tray = $("attachment-tray");
  tray.hidden = !state.attachments.length;
  tray.replaceChildren(...state.attachments.map((file) => attachmentChip(file, file.pending ? null : () => removeAttachment(file))));
}

async function removeAttachment(file) {
  state.attachments = state.attachments.filter((other) => other !== file);
  renderTray();
  if (file.id) await send(`/api/uploads/${file.id}`, "DELETE").catch(() => {});
}

async function attachOne(file) {
  const name = file.name || `pasted-${Date.now()}.png`;
  const kind = file.type.startsWith("image/") ? "image" : name.toLowerCase().endsWith(".har") ? "har" : "document";
  const entry = { name, kind, size: file.size, pending: true, preview: kind === "image" ? URL.createObjectURL(file) : null };
  state.attachments.push(entry);
  renderTray();
  try {
    const meta = await send("/api/uploads", "POST", { name, data: await readAsBase64(file) });
    Object.assign(entry, meta, { pending: false });
    if (entry.preview) imageUrls.set(meta.id, entry.preview);
  } catch (error) {
    state.attachments = state.attachments.filter((other) => other !== entry);
    toast(`${name}: ${error.message}`, true);
  }
  renderTray();
}

function attachFiles(files) {
  const room = MAX_ATTACHMENTS - state.attachments.length;
  if (files.length > room) toast(`Attach up to ${MAX_ATTACHMENTS} files at a time.`, true);
  return Promise.all([...files].slice(0, Math.max(0, room)).map(attachOne));
}

$("attach").onclick = () => $("file-input").click();
$("file-input").onchange = () => {
  attachFiles($("file-input").files);
  $("file-input").value = "";
};
$("input").addEventListener("paste", (event) => {
  const files = event.clipboardData?.files;
  if (files?.length) {
    event.preventDefault();
    attachFiles(files);
  }
});

// Dragging files over the chat shows where to drop them.
let dragDepth = 0;
const draggingFiles = (event) => [...(event.dataTransfer?.types ?? [])].includes("Files");
const chatPage = $("page-chat");
chatPage.addEventListener("dragenter", (event) => {
  if (!draggingFiles(event) || state.wake) return;
  event.preventDefault();
  dragDepth++;
  $("drop-zone").hidden = false;
});
chatPage.addEventListener("dragover", (event) => {
  if (draggingFiles(event)) event.preventDefault();
});
chatPage.addEventListener("dragleave", () => {
  dragDepth = Math.max(0, dragDepth - 1);
  if (!dragDepth) $("drop-zone").hidden = true;
});
chatPage.addEventListener("drop", (event) => {
  event.preventDefault();
  dragDepth = 0;
  $("drop-zone").hidden = true;
  if (event.dataTransfer?.files.length && !state.wake) attachFiles(event.dataTransfer.files);
});

async function copyText(text, button) {
  try {
    await navigator.clipboard.writeText(text);
    if (button) {
      const label = button.textContent;
      button.textContent = "Copied";
      setTimeout(() => (button.textContent = label), 1500);
    }
  } catch {
    toast("Couldn't copy to the clipboard.", true);
  }
}

// Code blocks in replies have a Copy button. One listener covers them all, including future ones.
$("messages").addEventListener("click", (event) => {
  const button = event.target.closest(".copy-code");
  if (button) copyText(button.closest(".code-block").querySelector("code").textContent, button);
});

function startVoice(wake) {
  if (state.socket?.readyState !== WebSocket.OPEN) {
    toast("Not connected to Fox yet. Try again in a moment.", true);
    return;
  }
  state.wake = wake;
  setBusy(true);
  state.socket.send(JSON.stringify({ type: "voice_start", wake, session_id: state.sessionId }));
}

const stopTurn = () => state.socket?.send(JSON.stringify({ type: "cancel" }));

function resizeInput() {
  const input = $("input");
  input.style.height = "auto";
  input.style.height = `${input.scrollHeight}px`;
}

$("input").addEventListener("input", resizeInput);
$("input").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    submit($("input").value);
  }
});
$("send").onclick = () => submit($("input").value);
$("stop").onclick = stopTurn;
$("talk").onclick = () => (state.busy ? stopTurn() : startVoice(false));
$("wake").onclick = () => (state.wake ? stopTurn() : startVoice(true));
$("speak").onchange = guarded(async () => {
  state.prefs.speak = $("speak").checked;
  await send("/api/prefs", "PUT", state.prefs);
});
$("new-chat").onclick = () => openSession(null);
$("model-pick").onchange = guarded(async () => {
  state.prefs.model = $("model-pick").value;
  $("model-label").textContent = state.prefs.model || state.status?.models[0] || "";
  await send("/api/prefs", "PUT", state.prefs);
});
$("go-settings").onclick = () => showPage("settings");

// --- Conversations ---

async function loadSessions() {
  const sessions = await api("/api/sessions");
  const list = $("session-list");
  list.replaceChildren(
    ...sessions.map((session) => {
      const open = el("button", {
        className: "session-open",
        textContent: session.title || "New chat",
        title: session.title,
        onclick: guarded(() => openSession(session.id)),
      });
      const remove = el("button", {
        className: "session-delete",
        textContent: "Delete",
        ariaLabel: `Delete ${session.title}`,
        onclick: guarded(async () => {
          if (!(await confirmDelete(`Delete "${session.title}"?`))) return;
          await send(`/api/sessions/${session.id}`, "DELETE");
          if (state.sessionId === session.id) await openSession(null);
          await loadSessions();
        }),
      });
      return el("li", { className: `session${session.id === state.sessionId ? " active" : ""}` }, open, remove);
    }),
  );
  const current = sessions.find((session) => session.id === state.sessionId);
  $("chat-title").textContent = current?.title || "New chat";
  updateControls();
}

async function openSession(id) {
  if (state.busy) return;
  state.sessionId = id;
  state.reply = null;
  state.activity.clear();
  messages.replaceChildren();
  if (id) {
    for (const message of await api(`/api/sessions/${id}/messages`)) {
      if (message.role === "human") {
        messages.append(userMessage(message.content, message.attachments ?? []));
      } else if (message.role === "ai") {
        if (message.content.trim()) addAssistantMessage(message.content);
      } else if (message.role === "tool") {
        const failed = /^(Permission denied|Error|Blocked)/.test(message.content);
        const group = stepsGroup();
        group.append(el("div", { className: `activity ${failed ? "failed" : "ok"}` },
          el("span", { className: "state", textContent: failed ? "Stopped " : "Done " }), message.tool));
        updateSteps(group);
      }
    }
    scrollDown(true);
  } else {
    showEmptyState();
  }
  await loadSessions();
  $("input").focus();
}

// --- Events from Fox ---

const events = {
  session({ session_id }) {
    const isNew = session_id !== state.sessionId;
    state.sessionId = session_id;
    if (isNew) guarded(loadSessions)();
  },
  text({ delta }) {
    startReply().text += delta;
    requestAnimationFrame(renderReply);
  },
  text_reset() {
    if (state.reply) {
      state.reply.text = "";
      renderReply();
    }
  },
  model_switch({ model }) {
    closeReply();
    addNote(`Switching to ${model}`);
    $("model-label").textContent = model;
  },
  tool_start({ tool, agent, summary }) {
    addActivity(tool, agent === "fox" ? summary : `${agent}: ${summary}`);
  },
  tool_end: finishActivity,
  agent_start({ agent }) {
    closeReply();
    addNote(`Handing off to agent ${agent}`);
  },
  agent_end({ agent }) {
    addNote(`Agent ${agent} finished`);
  },
  permission_request: showPrompt,
  permission_closed({ id }) {
    if (state.prompt?.id === id) closePrompt();
  },
  reminder({ text }) {
    toast(`Reminder: ${text}`);
    addNote(`Reminder: ${text}`);
  },
  control({ kill_switch, disabled }) {
    state.control = { kill_switch, disabled };
    applyControl();
    if (document.getElementById("page-control").classList.contains("active")) guarded(loadControl)();
  },
  done({ model }) {
    if (model) $("model-label").textContent = model;
    renderReply();
    closeReply();
    guarded(loadSessions)();
  },
  error({ message }) {
    closeReply();
    addError(message);
  },
  notice({ message }) {
    toast(message);
  },
  captures(stats) {
    showCaptureCount(stats);
  },
  cancelled() {
    closeReply();
    addNote("Stopped.");
  },
  turn_end() {
    closeReply();
    state.activity.clear();
    state.wake = false;
    state.voice = "idle";
    setBusy(false);
    $("input").focus();
  },
  voice_state({ state: voice }) {
    state.voice = voice;
    updateControls();
  },
  voice_heard({ text }) {
    addUserMessage(text);
  },
};

function connect() {
  const socket = new WebSocket(`ws://${location.host}/ws?token=${encodeURIComponent(token)}`);
  state.socket = socket;
  socket.onopen = () => {
    state.retry = 500;
    setConnection("online", "Connected");
  };
  socket.onmessage = (message) => {
    const event = JSON.parse(message.data);
    events[event.type]?.(event);
  };
  socket.onclose = () => {
    setConnection("offline", "Reconnecting");
    // Fox cancels the running turn and refuses open prompts when the connection drops.
    closePrompt();
    if (state.busy) events.turn_end();
    setTimeout(connect, state.retry);
    state.retry = Math.min(state.retry * 2, 8000);
  };
}

function setConnection(kind, text) {
  $("connection").className = `rail-status ${kind}`;
  $("connection").title = text;
  $("connection-text").textContent = text;
}

// --- Permission prompt ---
//
// The hard rule of this app: nothing runs until the person agrees. So Deny has the keyboard focus,
// buttons wait a moment so a click or Enter meant for something else can't land on them, and a
// critical action needs "yes" typed out, the same as in the terminal.

const ARM_DELAY_MS = 600;
const PROMPT_BUTTONS = {
  once: { label: "Allow once", kind: "primary" },
  session: { label: "Allow this session" },
  always: { label: "Always allow" },
  deny: { label: "Deny" },
  never: { label: "Never allow", kind: "danger" },
};
const PROMPT_ORDER = ["never", "deny", "always", "session", "once"];

function showPrompt(request) {
  closePrompt();
  const critical = request.risk === "critical";
  const dialog = $("prompt");
  dialog.classList.toggle("critical", critical);

  $("prompt-risk").textContent = critical ? "Critical" : "Needs permission";
  $("prompt-risk").className = `badge ${critical ? "critical" : "normal"}`;
  $("prompt-agent").textContent = request.agent === "fox" ? request.tool : `${request.tool}, asked by agent ${request.agent}`;
  $("prompt-title").textContent = request.summary;
  const detail = request.description.split("\n").slice(1).join("\n").trim();
  $("prompt-detail").textContent = detail;
  $("prompt-detail").hidden = !detail;
  $("prompt-confirm").hidden = !critical;
  $("prompt-yes").value = "";
  const spoken = state.voice !== "idle";
  const hint = critical ? 'Say "confirm" or "no".' : 'Say "yes", "always" or "no".';
  $("prompt-note").textContent = `${spoken ? `${hint} ` : ""}No answer means no.`;

  const answer = (decision) => {
    state.socket?.send(JSON.stringify({ type: "permission_response", id: request.id, decision }));
    closePrompt();
  };
  const buttons = new Map();
  const actions = PROMPT_ORDER.filter((option) => request.options.includes(option)).map((option) => {
    const { label, kind } = PROMPT_BUTTONS[option];
    const scoped = option === "always" && request.scope !== "*" ? `Always for "${request.scope}"` : label;
    const button = el("button", { className: `button${kind ? ` ${kind}` : ""}`, textContent: scoped, disabled: true });
    button.onclick = () => answer(option);
    buttons.set(option, button);
    return button;
  });
  $("prompt-actions").replaceChildren(...actions);

  const updateAllow = () => {
    const armed = Date.now() >= armedAt;
    for (const [option, button] of buttons) {
      const needsYes = critical && option === "once" && $("prompt-yes").value.trim().toLowerCase() !== "yes";
      button.disabled = !armed || needsYes;
    }
  };
  const armedAt = Date.now() + ARM_DELAY_MS;
  const armTimer = setTimeout(updateAllow, ARM_DELAY_MS);
  $("prompt-yes").oninput = updateAllow;
  $("prompt-yes").onkeydown = (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      if (!buttons.get("once").disabled) answer("once");
    }
  };

  const started = Date.now();
  const total = request.timeout * 1000;
  const timer = setInterval(() => {
    $("prompt-bar").style.width = `${Math.max(0, 100 - ((Date.now() - started) / total) * 100)}%`;
  }, 500);
  $("prompt-bar").style.width = "100%";

  dialog.oncancel = (event) => {
    event.preventDefault();
    answer("deny");
  };
  state.prompt = { id: request.id, cleanup: () => (clearInterval(timer), clearTimeout(armTimer)) };
  dialog.showModal();
  if (critical) $("prompt-yes").focus();
  else buttons.get("deny").focus();
  // showModal can leave a disabled button unfocused, so keep the focus somewhere that can't approve.
  setTimeout(() => {
    if (!critical && state.prompt?.id === request.id) buttons.get("deny").focus();
  }, ARM_DELAY_MS + 20);
}

function closePrompt() {
  state.prompt?.cleanup();
  state.prompt = null;
  if ($("prompt").open) $("prompt").close();
}

// --- Agents ---

let toolCache = [];
const loadTools = async () => (toolCache = await api("/api/tools"));

async function loadAgents() {
  const [agents] = await Promise.all([api("/api/agents"), loadTools()]);
  const list = $("agent-list");
  if (!agents.length) {
    list.replaceChildren(el("p", { className: "muted", textContent: "No agents yet." }));
    return;
  }
  list.replaceChildren(
    ...agents.map((agent) =>
      el("article", { className: "card" },
        el("h3", { textContent: agent.name }),
        el("p", { textContent: agent.description }),
        el("div", { className: "meta", textContent: `Tools: ${agent.tools.join(", ") || "none"}` }),
        el("div", { className: "meta", textContent: `Models: ${agent.models.join(", ") || "same as Fox"}` }),
        el("div", { className: "button-row" },
          el("button", { className: "button small", textContent: "Edit", onclick: () => editAgent(agent) }),
          el("button", {
            className: "button small danger",
            textContent: "Delete",
            onclick: guarded(async () => {
              if (!(await confirmDelete(`Delete agent "${agent.name}"?`))) return;
              await send(`/api/agents/${agent.name}`, "DELETE");
              await loadAgents();
            }),
          })))),
  );
}

function editAgent(agent) {
  const existing = Boolean(agent);
  $("agent-dialog-title").textContent = existing ? `Edit ${agent.name}` : "New agent";
  $("agent-name").value = agent?.name ?? "";
  $("agent-name").readOnly = existing;
  $("agent-description").value = agent?.description ?? "";
  $("agent-prompt").value = agent?.system_prompt ?? "";
  $("agent-models").value = agent?.models.join(",") ?? "";
  $("agent-steps").value = agent?.max_steps ?? 10;
  const all = agent?.tools.includes("*") ?? false;
  $("agent-all-tools").checked = all;
  $("agent-tools").replaceChildren(
    ...toolCache.map((tool) =>
      el("label", { className: "check", title: tool.description },
        el("input", { type: "checkbox", value: tool.name, checked: agent?.tools.includes(tool.name) ?? false, disabled: all }),
        tool.name)),
  );
  $("agent-dialog").showModal();
}

$("add-agent").onclick = () => editAgent(null);
$("agent-cancel").onclick = () => $("agent-dialog").close();
$("agent-all-tools").onchange = () => {
  document.querySelectorAll("#agent-tools input").forEach((box) => (box.disabled = $("agent-all-tools").checked));
};
$("agent-form").onsubmit = guarded(async (event) => {
  event.preventDefault();
  const tools = $("agent-all-tools").checked
    ? ["*"]
    : [...document.querySelectorAll("#agent-tools input:checked")].map((box) => box.value);
  await send(`/api/agents/${$("agent-name").value}`, "PUT", {
    description: $("agent-description").value,
    system_prompt: $("agent-prompt").value,
    models: $("agent-models").value.split(",").map((model) => model.trim()).filter(Boolean),
    max_steps: Number($("agent-steps").value),
    tools,
  });
  $("agent-dialog").close();
  await loadAgents();
});

// --- Permissions ---

const RULE_LABELS = { allow: "Always allow", session: "This session", deny: "Never" };
const targetText = (scope) => (scope === "*" ? "Everything" : scope);
const cell = (text, className = "") => el("td", { textContent: text, className });
const emptyRow = (columns, text) => el("tr", {}, el("td", { colSpan: columns, className: "empty-row", textContent: text }));

async function loadRules() {
  const rules = await api("/api/permissions");
  const rows = [];
  for (const kind of ["allow", "session", "deny"]) {
    for (const rule of rules[kind]) {
      const revoke = el("button", {
        className: "button small",
        textContent: "Remove",
        onclick: guarded(async () => {
          await send("/api/permissions/revoke", "POST", rule);
          await loadRules();
        }),
      });
      rows.push(el("tr", {},
        el("td", { className: "nowrap" }, el("span", { className: `badge ${kind}`, textContent: RULE_LABELS[kind] })),
        cell(rule.tool, "nowrap"), cell(targetText(rule.scope)), el("td", { className: "nowrap" }, revoke)));
    }
  }
  $("rule-list").replaceChildren(...(rows.length ? rows : [emptyRow(4, "Nothing remembered. Fox asks every time.")]));
}

function toolToggle(tool) {
  const box = el("input", { type: "checkbox", checked: tool.enabled });
  box.onchange = guarded(async () => {
    box.disabled = true;
    try {
      await send("/api/control/tools", "POST", { tool: tool.name, enabled: box.checked });
      tool.enabled = box.checked;
    } catch (error) {
      box.checked = !box.checked; // put it back if the server refused
      throw error;
    } finally {
      box.disabled = false;
    }
  });
  const label = el("label", { className: "check", title: tool.enabled ? "On" : "Off" }, box);
  return el("td", { className: "nowrap" }, label);
}

function renderTools() {
  const filter = $("tool-search").value.trim().toLowerCase();
  const rows = toolCache
    .filter((tool) => !tool.name.startsWith("_") && `${tool.name} ${tool.description}`.toLowerCase().includes(filter))
    .map((tool) =>
      el("tr", { className: tool.enabled ? "" : "off" },
        toolToggle(tool),
        cell(tool.name, "nowrap"),
        el("td", {}, el("span", { className: `badge ${tool.risk}`, textContent: tool.risk })),
        cell((tool.description ?? "").split("\n")[0])));
  $("tool-list").replaceChildren(...(rows.length ? rows : [emptyRow(4, "No tools match.")]));
}

async function loadAudit() {
  const rows = (await api("/api/audit?limit=200")).map((row) =>
    el("tr", {},
      cell(row.at.replace("T", " "), "nowrap"),
      cell(row.agent),
      cell(row.tool, "nowrap"),
      cell(row.outcome.replaceAll("_", " "), "nowrap"),
      cell(row.description.split("\n")[0])));
  $("audit-list").replaceChildren(...(rows.length ? rows : [emptyRow(5, "No decisions yet.")]));
}

async function loadPermissions() {
  await loadTools();
  renderTools();
  const grantable = toolCache.filter((tool) => tool.risk !== "critical" && tool.risk !== "safe" && !tool.name.startsWith("_"));
  $("grant-tool").replaceChildren(...grantable.map((tool) => el("option", { value: tool.name, textContent: tool.name })));
  await Promise.all([loadRules(), loadAudit()]);
}

$("tool-search").oninput = renderTools;
document.querySelectorAll(".tab").forEach((tab) => {
  tab.onclick = () => {
    document.querySelectorAll(".tab").forEach((other) => other.classList.toggle("active", other === tab));
    document.querySelectorAll(".tab-panel").forEach((panel) => (panel.hidden = panel.id !== `tab-${tab.dataset.tab}`));
  };
});
$("grant-form").onsubmit = guarded(async (event) => {
  event.preventDefault();
  await send("/api/permissions/grant", "POST", { tool: $("grant-tool").value, scope: $("grant-scope").value.trim() || "*" });
  $("grant-scope").value = "";
  await loadRules();
});

// --- Control: kill switch, plugins, reset ---

function applyControl() {
  const on = state.control.kill_switch;
  $("kill-banner").hidden = !on;
  const toggle = $("kill-toggle");
  if (toggle) {
    toggle.textContent = on ? "Turn off kill switch" : "Engage kill switch";
    toggle.classList.toggle("primary", on);
    toggle.classList.toggle("danger", !on);
    $("kill-state").textContent = on ? "Kill switch is ON. Fox can't run any tool." : "Fox is running normally.";
    $("kill-card").classList.toggle("alarm", on);
  }
}

async function setKill(on) {
  const result = await send("/api/control/kill", "POST", { on });
  state.control.kill_switch = result.kill_switch;
  applyControl();
  if (on && result.stopped) toast(`Kill switch on. Stopped ${result.stopped} running task${result.stopped > 1 ? "s" : ""}.`);
}

async function loadControl() {
  state.control = await api("/api/control");
  applyControl();
  const plugins = await api("/api/plugins");
  const rows = plugins.map((plugin) => {
    const status = plugin.ok
      ? el("span", { className: "badge safe", textContent: "Loaded" })
      : el("span", { className: "badge critical", textContent: "Failed", title: plugin.error ?? "" });
    const name = plugin.file.replace(/\.py$/, "");
    const edit = el("button", { className: "button small", textContent: "Edit", onclick: guarded(() => editPlugin(name)) });
    const remove = el("button", {
      className: "button small danger",
      textContent: "Delete",
      onclick: guarded(async () => {
        if (!(await confirmDelete(`Delete plugin "${plugin.file}"?`))) return;
        await send(`/api/plugins/${name}`, "DELETE");
        await Promise.all([loadControl(), refreshStatus()]);
      }),
    });
    return el("tr", {},
      cell(plugin.file, "nowrap"), el("td", {}, status),
      cell(plugin.tools.join(", ") || (plugin.ok ? "none" : plugin.error ?? "")),
      el("td", { className: "nowrap" }, el("div", { className: "button-row no-margin" }, edit, remove)));
  });
  $("plugin-list").replaceChildren(...(rows.length ? rows : [emptyRow(4, "No plugins yet.")]));
}

async function editPlugin(name) {
  $("plugin-dialog-title").textContent = name ? `Edit ${name}` : "Add plugin";
  $("plugin-name").value = name ?? "";
  $("plugin-name").readOnly = Boolean(name);
  $("plugin-code").value = name
    ? (await api(`/api/plugins/${name}`)).code
    : 'from fox.core.permissions import Risk\nfrom fox.core.tooling import fox_tool\n\n\n@fox_tool(risk=Risk.SAFE)\ndef my_tool() -> str:\n    """Describe what this tool does. Fox reads this."""\n    return "hello from my tool"\n';
  $("plugin-dialog").showModal();
}

$("kill-toggle").onclick = guarded(() => setKill(!state.control.kill_switch));
$("kill-banner-off").onclick = guarded(() => setKill(false));
$("add-plugin").onclick = guarded(() => editPlugin(null));
$("plugin-cancel").onclick = () => $("plugin-dialog").close();
$("plugin-form").onsubmit = guarded(async (event) => {
  event.preventDefault();
  await send(`/api/plugins/${$("plugin-name").value}`, "PUT", { code: $("plugin-code").value });
  $("plugin-dialog").close();
  toast("Plugin saved. Its tools are on, behind the usual permission prompts.");
  await Promise.all([loadControl(), refreshStatus()]);
});

const RESET_SCOPES = ["conversations", "memory", "permissions", "audit", "tools", "captures", "cache"];
$("reset-run").onclick = guarded(async () => {
  const scopes = RESET_SCOPES.filter((scope) => $(`reset-${scope}`).checked);
  if (!scopes.length) {
    toast("Pick what to reset first.");
    return;
  }
  if (!(await confirmDelete(`Permanently clear: ${scopes.join(", ")}? This can't be undone.`))) return;
  const { reset } = await send("/api/reset", "POST", { scopes });
  RESET_SCOPES.forEach((scope) => ($(`reset-${scope}`).checked = false));
  toast(`Reset: ${reset.join(", ") || "nothing"}.`);
  await Promise.all([loadControl(), refreshStatus()]);
});

// --- Memory ---

function fillList(list, items, emptyText, describe, removeLabel, remove) {
  if (!items.length) {
    list.replaceChildren(el("li", { className: "none", textContent: emptyText }));
    return;
  }
  list.replaceChildren(
    ...items.map((item) =>
      el("li", {},
        el("span", { textContent: describe(item) }),
        el("button", { className: "button small", textContent: removeLabel, onclick: guarded(() => remove(item)) }))),
  );
}

async function loadMemory() {
  const [reminders, facts] = await Promise.all([api("/api/reminders"), api("/api/facts")]);
  fillList($("reminder-list"), reminders, "No reminders set.", (r) => `${r.due.replace("T", " ")}: ${r.text}`, "Cancel", async (r) => {
    await send(`/api/reminders/${r.id}`, "DELETE");
    await loadMemory();
  });
  fillList($("fact-list"), facts, "Nothing saved yet.", (f) => f.text, "Forget", async (f) => {
    await send(`/api/facts/${f.id}`, "DELETE");
    await loadMemory();
  });
}

// --- Settings ---

const MODEL_LAYOUT = [
  { title: "Fallback order", intro: "Fox tries these left to right, and skips any without a key. Use provider names, provider:model, or the names of models you added above.", fields: [
    ["FOX_MODEL_CHAIN", "Fallback order", "Such as azure,gemini,groq:llama-3.3-70b-versatile,deepseek"],
    ["FOX_VISION_CHAIN", "Order for messages with images", "Empty: models marked \"can read images\" first, then the fallback order"],
  ] },
  { title: "Provider keys", intro: "The built-in providers. A model you add can use these keys too.", providers: true, fields: [
    ["AZURE_OPENAI_API_KEY", "Azure OpenAI key"],
    ["AZURE_OPENAI_ENDPOINT", "Azure endpoint"],
    ["AZURE_OPENAI_CHAT_DEPLOYMENT", "Azure deployment name"],
    ["GOOGLE_API_KEY", "Google Gemini key"],
    ["GEMINI_MODEL", "Gemini model"],
    ["GROQ_API_KEY", "Groq key"],
    ["GROQ_MODEL", "Groq model"],
    ["OPENAI_API_KEY", "OpenAI key"],
    ["OPENAI_MODEL", "OpenAI model"],
    ["ANTHROPIC_API_KEY", "Anthropic key"],
    ["ANTHROPIC_MODEL", "Anthropic model"],
    ["OLLAMA_MODEL", "Ollama model (local, no key)"],
    ["OLLAMA_BASE_URL", "Ollama address"],
  ] },
];

const SETTINGS_LAYOUT = [
  { title: "Voice", intro: "Keep pyttsx3 last in the output order. It works offline.", fields: [
    ["FOX_TTS_CHAIN", "Voice output order", "pyttsx3, edge, elevenlabs, openai, azure"],
    ["FOX_TTS_ROBOT_EFFECT", "Robot effect", "true or false"],
    ["FOX_EDGE_VOICE", "Edge voice"],
    ["ELEVENLABS_API_KEY", "ElevenLabs key"],
    ["AZURE_SPEECH_KEY", "Azure Speech key"],
    ["AZURE_SPEECH_REGION", "Azure Speech region"],
    ["FOX_STT_CHAIN", "Voice input order", "faster_whisper, groq, openai"],
    ["FOX_WHISPER_MODEL", "Whisper model"],
    ["FOX_WAKE_WORD", "Wake word"],
  ] },
  { title: "Email", intro: "For Gmail, use an App Password, not your normal password.", fields: [
    ["EMAIL_ADDRESS", "Email address"],
    ["EMAIL_PASSWORD", "Email password"],
    ["SMTP_HOST", "SMTP server"],
    ["SMTP_PORT", "SMTP port"],
    ["IMAP_HOST", "IMAP server"],
    ["IMAP_PORT", "IMAP port"],
  ] },
  { title: "Advanced", intro: "Changes apply as soon as you save. The defaults suit most people.", fields: [
    ["FOX_MAX_STEPS", "Max tool steps per turn", "How many tools Fox may chain before it must answer"],
    ["FOX_CACHE_HOURS", "Reuse answers for (hours)", "A repeated question is answered from cache, without the model. 0 turns this off"],
    ["FOX_TEMPERATURE", "Model temperature", "0 is focused, 1 is creative"],
    ["FOX_MAX_CONTEXT_TOKENS", "Context size (tokens)", "How much of a conversation Fox keeps in view"],
    ["FOX_MODEL_TIMEOUT", "Model timeout (seconds)"],
    ["FOX_MODEL_COOLDOWN", "Model cooldown (seconds)", "How long a failing model is skipped"],
    ["FOX_PERMISSION_TIMEOUT", "Permission prompt timeout (seconds)", "No answer counts as no"],
    ["FOX_TOOL_TIMEOUT", "Tool timeout (seconds)"],
    ["FOX_MAX_TOOL_OUTPUT", "Max tool output (characters)"],
    ["FOX_MAX_UPLOAD_MB", "Largest attachment (MB)"],
    ["FOX_MAX_ATTACHMENT_CHARS", "Document text sent with a message (characters)", "Fox reads longer documents in parts"],
    ["FOX_MAX_CAPTURES", "Captured requests kept", "The oldest are deleted beyond this"],
  ] },
];

let settingsDraft = {};

function secretField(key, label, isSet) {
  const input = el("input", {
    type: "password",
    autocomplete: "off",
    placeholder: isSet ? "Saved. Type to replace it." : "Not set",
    oninput: () => (settingsDraft[key] = input.value),
  });
  input.setAttribute("aria-label", label);
  const row = el("div", { className: "with-action" }, input);
  if (isSet) {
    const clear = el("button", {
      className: "button small",
      type: "button",
      textContent: "Clear",
      onclick: () => {
        settingsDraft[key] = "";
        input.value = "";
        input.placeholder = "Will be removed when you save";
      },
    });
    row.append(clear);
  }
  return el("div", { className: "field" }, el("span", { textContent: label }), row);
}

function plainField(key, label, help, value) {
  const input = el("input", { value, autocomplete: "off", oninput: () => (settingsDraft[key] = input.value) });
  const field = el("label", { className: "field" }, el("span", { textContent: label }), input);
  if (help) field.append(el("small", { textContent: help }));
  return field;
}

function renderSettings(container, layout, settings) {
  settingsDraft = {};
  const sections = layout.map(({ title, intro, fields, providers }) => {
    const grid = el("div", { className: "field-grid" });
    for (const [key, label, help] of fields) {
      grid.append(key in settings.secrets
        ? secretField(key, label, settings.secrets[key])
        : plainField(key, label, help, settings.values[key] ?? ""));
    }
    const section = [el("h2", { textContent: title }), el("p", { className: "muted", textContent: intro })];
    if (providers) {
      section.push(el("div", { className: "status-row" }, ...Object.entries(settings.providers).map(([name, ready]) =>
        el("span", { className: `badge ${ready ? "safe" : ""}`, textContent: `${name}: ${ready ? "ready" : "not set"}` }))));
    }
    return el("section", {}, ...section, grid);
  });
  container.replaceChildren(...sections);
}

function showCaptureCount({ count, latest }) {
  $("capture-count").textContent = count
    ? `${count} request${count === 1 ? "" : "s"} captured, the latest at ${String(latest).replace("T", " ")}. Ask Fox to analyse them.`
    : "No requests captured yet.";
  $("clear-captures").disabled = !count;
}

async function loadSettings() {
  const [settings, captures] = await Promise.all([api("/api/settings"), api("/api/captures?limit=1")]);
  $("settings-path").textContent = `Saved in ${settings.path}. Changes apply as soon as you save.`;
  renderSettings($("settings-form"), SETTINGS_LAYOUT, settings);
  $("capture-address").value = location.origin;
  $("capture-token").value = token;
  showCaptureCount(captures);
  $("theme").value = state.prefs.theme;
  $("about").textContent = `Fox ${state.status?.version ?? ""}. ${state.status?.tools_on ?? 0} of ${state.status?.tools ?? 0} tools on.`;
  if (state.status?.voice_problem) $("test-voice").title = state.status.voice_problem;
}

function fillModelPicker() {
  const available = state.status?.available_models ?? [];
  const chosen = state.prefs.model;
  const options = [el("option", { value: "", textContent: "Auto" }), ...available.map((name) => el("option", { value: name, textContent: name }))];
  if (chosen && !available.includes(chosen)) options.push(el("option", { value: chosen, textContent: `${chosen} (not set up)` }));
  $("model-pick").replaceChildren(...options);
  $("model-pick").value = chosen;
}

async function refreshStatus() {
  state.status = await api("/api/status");
  state.control.kill_switch = state.status.kill_switch;
  applyControl();
  const hasModel = state.status.models.length > 0 || state.status.available_models.length > 0;
  $("no-model").hidden = hasModel;
  if (hasModel && !$("model-label").textContent) $("model-label").textContent = state.prefs.model || state.status.models[0] || "";
  if (!hasModel) $("model-label").textContent = "";
  fillModelPicker();
  updateControls();
}

function applyTheme() {
  const dark = state.prefs.theme === "dark" || (state.prefs.theme === "system" && matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.dataset.theme = dark ? "dark" : "light";
}
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", applyTheme);

const saveSettings = (reload) => guarded(async () => {
  if (!Object.keys(settingsDraft).length) {
    toast("Nothing has changed.");
    return;
  }
  await send("/api/settings", "PUT", { values: settingsDraft });
  $("model-label").textContent = "";
  await Promise.all([reload(), refreshStatus()]);
  toast("Saved.");
});
$("save-settings").onclick = saveSettings(() => loadSettings());
$("save-model-settings").onclick = saveSettings(() => loadModels());
$("copy-address").onclick = () => copyText($("capture-address").value, $("copy-address"));
$("copy-token").onclick = () => copyText($("capture-token").value, $("copy-token"));
$("clear-captures").onclick = guarded(async () => {
  if (!(await confirmDelete("Delete every captured network request?"))) return;
  showCaptureCount(await send("/api/captures", "DELETE"));
});

// --- Models ---

const PROVIDER_INFO = {
  openai_compatible: {
    label: "OpenAI-compatible server",
    model: "As the server names it, such as deepseek-chat or mistral-large-latest",
    url: "Required, such as https://openrouter.ai/api/v1, https://api.deepseek.com/v1 or http://localhost:1234/v1",
    key: "The server's API key. Leave empty for a local server.",
  },
  openai: { label: "OpenAI", model: "Such as gpt-4.1-mini or gpt-4o", url: "Leave empty for OpenAI itself", key: "Leave empty to use the OpenAI key below" },
  azure: { label: "Azure OpenAI", model: "Your deployment name", url: "Endpoint. Leave empty to use the one below", key: "Leave empty to use the Azure key below" },
  gemini: { label: "Google Gemini", model: "Such as gemini-2.5-pro", url: "Not used", key: "Leave empty to use the Google key below" },
  groq: { label: "Groq", model: "Any model Groq lists", url: "Not used", key: "Leave empty to use the Groq key below" },
  anthropic: { label: "Anthropic", model: "Such as claude-haiku-5-5", url: "Leave empty for Anthropic itself", key: "Leave empty to use the Anthropic key below" },
  ollama: { label: "Ollama (on this computer)", model: "A model you've pulled, such as llama3.2-vision", url: "Leave empty for http://localhost:11434", key: "Not needed" },
};

async function testModel(name, button) {
  button.disabled = true;
  const label = button.textContent;
  button.textContent = "Testing\u2026";
  try {
    const result = await send("/api/models/test", "POST", { model: name });
    if (result.ok) toast(`${result.model} answered "${result.reply.trim()}" in ${result.seconds} s.`);
    else toast(`${result.model} failed: ${result.error}`, true);
  } finally {
    button.disabled = false;
    button.textContent = label;
  }
}

async function loadModels() {
  const [models, settings] = await Promise.all([api("/api/models"), api("/api/settings")]);
  state.models = models;
  $("model-chain-text").replaceChildren(
    el("span", { textContent: models.chain.length ? `Fallback order: ${models.chain.join(" \u2192 ")}` : "No model is ready yet. Add a key below, or add a model." }),
    el("div", { className: "status-row top-gap" }, ...models.available.map((name) => {
      const button = el("button", { className: "chip", textContent: `Test ${name}` });
      button.onclick = guarded(() => testModel(name, button));
      return button;
    })),
  );
  const rows = models.custom.map((model) => {
    const test = el("button", { className: "button small", textContent: "Test", disabled: !model.ready });
    test.onclick = guarded(() => testModel(model.name, test));
    const edit = el("button", { className: "button small", textContent: "Edit", onclick: () => editModel(model) });
    const remove = el("button", {
      className: "button small danger",
      textContent: "Delete",
      onclick: guarded(async () => {
        if (!(await confirmDelete(`Delete model "${model.name}"?`))) return;
        await send(`/api/models/${model.name}`, "DELETE");
        await Promise.all([loadModels(), refreshStatus()]);
      }),
    });
    return el("tr", {},
      cell(model.name, "nowrap"),
      cell(PROVIDER_INFO[model.provider]?.label ?? model.provider, "nowrap"),
      cell(model.model),
      cell(model.vision ? "Yes" : ""),
      el("td", {}, el("span", { className: `badge ${model.ready ? "safe" : "normal"}`, textContent: model.ready ? "Ready" : "Needs a key or address" })),
      el("td", { className: "nowrap" }, el("div", { className: "button-row no-margin" }, test, edit, remove)));
  });
  $("model-list").replaceChildren(...(rows.length ? rows : [emptyRow(6, "No models added yet. The providers below work without adding anything.")]));
  renderSettings($("model-settings"), MODEL_LAYOUT, settings);
}

let modelKeyCleared = false;

function describeProvider() {
  const provider = $("model-provider").value;
  const info = PROVIDER_INFO[provider] ?? {};
  $("model-id-help").textContent = info.model ?? "";
  $("model-url-help").textContent = info.url ?? "";
  $("model-key-help").textContent = info.key ?? "";
  $("model-url").required = provider === "openai_compatible";
}

function editModel(model) {
  const providers = state.models?.custom_providers ?? Object.keys(PROVIDER_INFO);
  $("model-dialog-title").textContent = model ? `Edit ${model.name}` : "Add model";
  $("model-name").value = model?.name ?? "";
  $("model-name").readOnly = Boolean(model);
  $("model-provider").replaceChildren(...providers.map((name) => el("option", { value: name, textContent: PROVIDER_INFO[name]?.label ?? name })));
  $("model-provider").value = model?.provider ?? "openai_compatible";
  $("model-id").value = model?.model ?? "";
  $("model-url").value = model?.base_url ?? "";
  $("model-key").value = "";
  $("model-key").placeholder = model?.has_key ? "Saved. Type to replace it." : "Not set";
  $("model-key-clear").hidden = !model?.has_key;
  modelKeyCleared = false;
  $("model-vision").checked = model?.vision ?? false;
  describeProvider();
  $("model-dialog").showModal();
}

$("add-model").onclick = guarded(async () => {
  if (!state.models) state.models = await api("/api/models");
  editModel(null);
});
$("model-provider").onchange = describeProvider;
$("model-cancel").onclick = () => $("model-dialog").close();
$("model-key-clear").onclick = () => {
  modelKeyCleared = true;
  $("model-key").value = "";
  $("model-key").placeholder = "Will be removed when you save";
};
$("model-form").onsubmit = guarded(async (event) => {
  event.preventDefault();
  const name = $("model-name").value;
  const key = $("model-key").value.trim();
  await send(`/api/models/${name}`, "PUT", {
    provider: $("model-provider").value,
    model: $("model-id").value.trim(),
    base_url: $("model-url").value.trim(),
    vision: $("model-vision").checked,
    api_key: key || (modelKeyCleared ? "" : null),
  });
  $("model-dialog").close();
  toast(`Saved ${name}. Test it, then add it to the fallback order or pick it from the Model menu in chat.`);
  await Promise.all([loadModels(), refreshStatus()]);
});
$("theme").onchange = guarded(async () => {
  state.prefs.theme = $("theme").value;
  applyTheme();
  await send("/api/prefs", "PUT", state.prefs);
});
$("test-voice").onclick = guarded(async () => {
  await send("/api/voice/test", "POST");
  toast("Voice test finished.");
});
$("open-env").onclick = guarded(() => send("/api/open/env", "POST"));
$("open-log").onclick = guarded(() => send("/api/open/log", "POST"));

// --- Start ---

async function start() {
  applyTheme();
  if (!token) {
    document.body.replaceChildren(
      el("main", { className: "page-inner" },
        el("h1", { textContent: "Open Fox from its shortcut" }),
        el("p", { className: "muted", textContent: "This page needs the private token that the Fox desktop app passes to it." })),
    );
    return;
  }
  showEmptyState();
  connect();
  try {
    state.prefs = await api("/api/prefs");
    $("speak").checked = state.prefs.speak;
    applyTheme();
    await refreshStatus();
    await loadSessions();
  } catch (error) {
    toast(error.message, true);
  }
  $("input").focus();
}

start();

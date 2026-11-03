// A small Markdown renderer for Fox's replies.
//
// Replies can repeat text from web pages and emails, so nothing in them may become live HTML. All input
// is escaped before any tag is added, only http and https links are allowed, and images are never loaded,
// because an image address is a way to send data out without anyone clicking.
//
// Beyond the basics it understands task checklists, which Fox uses to report what it did:
//   - [x] done   - [ ] not done   - [~] partly done or in progress   - [!] failed or refused

export function escapeHtml(text) {
  return text
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function renderInline(raw) {
  const held = [];
  const hold = (html) => `\u0000${held.push(html) - 1}\u0000`;
  // The NUL marks below belong to this function, so they can't be supplied by the input.
  let text = escapeHtml(raw.replaceAll("\u0000", ""));

  text = text.replace(/`([^`]+)`/g, (_, code) => hold(`<code>${code}</code>`));
  text = text.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, (_, label, url) =>
    hold(`<a href="${url}" target="_blank" rel="noopener noreferrer">${label}</a>`));
  text = text.replace(/https?:\/\/[^\s<]+[^\s<.,;:!?)]/g, (url) =>
    hold(`<a href="${url}" target="_blank" rel="noopener noreferrer">${url}</a>`));
  text = text.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  text = text.replace(/(^|[^*])\*([^*\s][^*]*)\*/g, "$1<em>$2</em>");
  text = text.replace(/~~([^~]+)~~/g, "<del>$1</del>");
  return text.replace(/\u0000(\d+)\u0000/g, (_, index) => held[Number(index)]);
}

// --- Tables ---

const isTableDivider = (line) => line.includes("-") && /^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$/.test(line) && !/^\s*-+\s*$/.test(line);
const startsTable = (lines, index) => lines[index].includes("|") && index + 1 < lines.length && isTableDivider(lines[index + 1]);
const tableCells = (line) => line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((cell) => cell.trim());
const alignOf = (cell) => (/^:-+:$/.test(cell) ? "center" : /^-+:$/.test(cell) ? "right" : "");

// A cell that is only a status word becomes a coloured badge, so a table of tasks reads at a glance.
const STATUS_WORDS = [
  [/^(?:✅\s*)?(?:done|complete|completed|success|successful|succeeded|passed|finished|fixed)$/i, "ok"],
  [/^(?:❌\s*)?(?:failed|fail|error|blocked|denied|refused|cancelled|canceled)$/i, "bad"],
  [/^(?:⏳\s*)?(?:in progress|running|partial|partly done|working)$/i, "partial"],
  [/^(?:pending|todo|to do|not done|not started|waiting|queued|skipped)$/i, "todo"],
];

function renderCell(cell) {
  const plain = cell.replace(/\*\*/g, "").trim();
  const status = STATUS_WORDS.find(([pattern]) => pattern.test(plain));
  return status ? `<span class="status ${status[1]}">${renderInline(plain)}</span>` : renderInline(cell);
}

function renderTable(header, divider, rows) {
  const aligns = tableCells(divider).map(alignOf);
  const cellTag = (tag, cell, column) =>
    `<${tag}${aligns[column] ? ` class="align-${aligns[column]}"` : ""}>${renderCell(cell)}</${tag}>`;
  const head = tableCells(header).map((cell, column) => cellTag("th", cell, column)).join("");
  const body = rows.map((row) => `<tr>${tableCells(row).map((cell, column) => cellTag("td", cell, column)).join("")}</tr>`);
  return `<div class="table-scroll"><table><thead><tr>${head}</tr></thead><tbody>${body.join("")}</tbody></table></div>`;
}

// --- Lists ---

const LIST_ITEM = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/;
const TASK = /^\[([ xX~!-])\]\s+(.*)$/;
const TASK_STATES = { " ": "todo", x: "done", X: "done", "~": "partial", "-": "partial", "!": "failed" };
const TASK_LABELS = { todo: "Not done", done: "Done", partial: "In progress", failed: "Failed" };

function readListItems(lines, index) {
  const items = [];
  while (index < lines.length) {
    const match = lines[index].match(LIST_ITEM);
    if (match) {
      items.push({ indent: match[1].replace(/\t/g, "    ").length, ordered: /\d/.test(match[2]), number: parseInt(match[2], 10), text: match[3] });
      index++;
    } else if (items.length && /^\s{2,}\S/.test(lines[index])) {
      items[items.length - 1].text += `\n${lines[index].trim()}`; // a wrapped line belonging to the item above
      index++;
    } else if (!lines[index].trim() && index + 1 < lines.length && LIST_ITEM.test(lines[index + 1])) {
      index++; // a blank line between items doesn't end the list
    } else {
      break;
    }
  }
  return [items, index];
}

function renderItem(item) {
  const task = item.text.match(TASK);
  const inline = (text) => text.split("\n").map(renderInline).join("<br>");
  if (!task) return { html: inline(item.text), state: null };
  const state = TASK_STATES[task[1]];
  return {
    html: `<span class="task-box" aria-hidden="true"></span><span class="sr-only">${TASK_LABELS[state]}: </span><span class="task-text">${inline(task[2])}</span>`,
    state,
  };
}

function taskSummary(states) {
  const count = (state) => states.filter((s) => s === state).length;
  const done = count("done");
  const extra = [["partial", "in progress"], ["failed", "failed"]]
    .filter(([state]) => count(state))
    .map(([state, words]) => `<span class="task-count ${state}">${count(state)} ${words}</span>`);
  return `<div class="task-summary"><progress max="${states.length}" value="${done}"></progress>` +
    `<span class="task-count done">${done} of ${states.length} done</span>${extra.join("")}</div>`;
}

function renderList(items, start, depth) {
  const base = items[start].indent;
  const ordered = items[start].ordered;
  const entries = [];
  let index = start;
  while (index < items.length && items[index].indent >= base) {
    const item = items[index];
    if (item.indent > base) {
      const [html, next] = renderList(items, index, depth + 1);
      if (entries.length) entries[entries.length - 1].children += html;
      else entries.push({ html: "", state: null, children: html });
      index = next;
      continue;
    }
    if (item.ordered !== ordered) break;
    entries.push({ ...renderItem(item), children: "" });
    index++;
  }
  const states = entries.map((entry) => entry.state).filter(Boolean);
  const isTasks = states.length > 0 && states.length === entries.length;
  const tag = ordered ? "ol" : "ul";
  const startAt = ordered && items[start].number > 1 ? ` start="${items[start].number}"` : "";
  const lis = entries.map((entry) => `<li${entry.state ? ` class="task ${entry.state}"` : ""}>${entry.html}${entry.children}</li>`);
  const list = `<${tag}${isTasks ? ' class="tasks"' : ""}${startAt}>${lis.join("")}</${tag}>`;
  // The progress line only on top-level checklists of two or more, where it says something.
  return [depth === 0 && isTasks && states.length > 1 ? `<div class="tasklist">${taskSummary(states)}${list}</div>` : list, index];
}

// --- Blocks ---

function renderCode(language, code) {
  const label = /^[\w#+.-]{1,20}$/.test(language) ? language : "";
  return `<div class="code-block"><div class="code-head"><span>${label || "code"}</span>` +
    `<button type="button" class="copy-code">Copy</button></div><pre><code>${escapeHtml(code)}</code></pre></div>`;
}

const startsBlock = (line) =>
  /^\s*```/.test(line) || /^#{1,6}\s/.test(line) || LIST_ITEM.test(line) || /^\s*>/.test(line) ||
  /^\s*([-*_])\s*\1\s*\1[\s\-*_]*$/.test(line);

export function renderMarkdown(source) {
  const lines = source.replace(/\r\n/g, "\n").split("\n");
  const out = [];
  let index = 0;

  while (index < lines.length) {
    const line = lines[index];

    const fence = line.match(/^\s*```\s*([^\s`]*)/);
    if (fence) {
      const code = [];
      index++;
      while (index < lines.length && !/^\s*```/.test(lines[index])) code.push(lines[index++]);
      index++;
      out.push(renderCode(fence[1], code.join("\n")));
      continue;
    }
    if (!line.trim()) {
      index++;
      continue;
    }
    const heading = line.match(/^(#{1,6})\s+(.*)$/);
    if (heading) {
      // Never as large as the page's own headings.
      const level = Math.min(heading[1].length + 2, 5);
      out.push(`<h${level}>${renderInline(heading[2].replace(/\s+#+\s*$/, ""))}</h${level}>`);
      index++;
      continue;
    }
    if (/^\s*([-*_])\s*\1\s*\1[\s\-*_]*$/.test(line)) {
      out.push("<hr>");
      index++;
      continue;
    }
    if (startsTable(lines, index)) {
      const header = line;
      const divider = lines[index + 1];
      const rows = [];
      index += 2;
      while (index < lines.length && lines[index].includes("|") && lines[index].trim()) rows.push(lines[index++]);
      out.push(renderTable(header, divider, rows));
      continue;
    }
    if (/^\s*>/.test(line)) {
      const quote = [];
      while (index < lines.length && /^\s*>/.test(lines[index])) quote.push(lines[index++].replace(/^\s*>\s?/, ""));
      out.push(`<blockquote>${renderMarkdown(quote.join("\n"))}</blockquote>`);
      continue;
    }
    if (LIST_ITEM.test(line)) {
      const [items, next] = readListItems(lines, index);
      let at = 0;
      while (at < items.length) {
        const [html, after] = renderList(items, at, 0);
        out.push(html);
        at = after;
      }
      index = next;
      continue;
    }
    const paragraph = [];
    while (index < lines.length && lines[index].trim() && !startsBlock(lines[index]) && !startsTable(lines, index)) {
      paragraph.push(renderInline(lines[index++]));
    }
    if (!paragraph.length) paragraph.push(renderInline(lines[index++])); // a line nothing else claimed
    out.push(`<p>${paragraph.join("<br>")}</p>`);
  }
  return out.join("");
}

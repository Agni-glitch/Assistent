// A small Markdown renderer for Fox's replies.
//
// Replies can repeat text from web pages and emails, so nothing in them may become live HTML. All input
// is escaped before any tag is added, only http and https links are allowed, and images are never loaded,
// because an image address is a way to send data out without anyone clicking.

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
  return text.replace(/\u0000(\d+)\u0000/g, (_, index) => held[Number(index)]);
}

const isTableDivider = (line) => /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(line);
const tableCells = (line) => line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((cell) => cell.trim());

export function renderMarkdown(source) {
  const lines = source.replace(/\r\n/g, "\n").split("\n");
  const out = [];
  let index = 0;

  while (index < lines.length) {
    const line = lines[index];

    if (/^\s*```/.test(line)) {
      const code = [];
      index++;
      while (index < lines.length && !/^\s*```/.test(lines[index])) code.push(lines[index++]);
      index++;
      out.push(`<pre><code>${escapeHtml(code.join("\n"))}</code></pre>`);
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
      out.push(`<h${level}>${renderInline(heading[2])}</h${level}>`);
      index++;
      continue;
    }
    if (/^\s*([-*_])\s*\1\s*\1[\s\-*_]*$/.test(line)) {
      out.push("<hr>");
      index++;
      continue;
    }
    if (line.includes("|") && index + 1 < lines.length && isTableDivider(lines[index + 1])) {
      const head = tableCells(line).map((cell) => `<th>${renderInline(cell)}</th>`).join("");
      const rows = [];
      index += 2;
      while (index < lines.length && lines[index].includes("|") && lines[index].trim()) {
        rows.push(`<tr>${tableCells(lines[index++]).map((cell) => `<td>${renderInline(cell)}</td>`).join("")}</tr>`);
      }
      out.push(`<table><thead><tr>${head}</tr></thead><tbody>${rows.join("")}</tbody></table>`);
      continue;
    }
    if (/^\s*>/.test(line)) {
      const quote = [];
      while (index < lines.length && /^\s*>/.test(lines[index])) quote.push(lines[index++].replace(/^\s*>\s?/, ""));
      out.push(`<blockquote>${renderMarkdown(quote.join("\n"))}</blockquote>`);
      continue;
    }
    const listKind = (text) => (/^\s*[-*+]\s+/.test(text) ? "ul" : /^\s*\d+[.)]\s+/.test(text) ? "ol" : null);
    const kind = listKind(line);
    if (kind) {
      const items = [];
      while (index < lines.length && listKind(lines[index]) === kind) {
        items.push(`<li>${renderInline(lines[index++].replace(/^\s*(?:[-*+]|\d+[.)])\s+/, ""))}</li>`);
      }
      out.push(`<${kind}>${items.join("")}</${kind}>`);
      continue;
    }
    const paragraph = [];
    while (
      index < lines.length && lines[index].trim() && !/^\s*```/.test(lines[index]) &&
      !/^#{1,6}\s/.test(lines[index]) && !listKind(lines[index]) && !/^\s*>/.test(lines[index])
    ) {
      paragraph.push(renderInline(lines[index++]));
    }
    out.push(`<p>${paragraph.join("<br>")}</p>`);
  }
  return out.join("");
}

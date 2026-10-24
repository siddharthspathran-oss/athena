// Markdown to HTML for the studios' previews. Everything is escaped first and only a fixed set
// of tags comes back, so a repo's Markdown can't inject script into the board.
import { esc, highlight, langOf } from './hl.js'

const safeUrl = u => (/^(https?:|mailto:|#|\.{0,2}\/|[\w-]+\.md\b)/i.test(u) && !/^javascript:/i.test(u) ? u : '#')

function inline(s) {
  const codes = []
  s = s.replace(/`([^`]+)`/g, (_, c) => `\u0000${codes.push(c) - 1}\u0000`)
  s = esc(s)
    .replace(/!\[([^\]]*)\]\(([^)\s]+)[^)]*\)/g, (_, alt, u) => (/^https?:/i.test(u) ? `<img alt="${alt}" src="${u}">` : `<em>[image: ${alt}]</em>`))
    .replace(/\[\[([^\]|]+)(?:\|([^\]]+))?\]\]/g, (_, t, label) => `<a class="wiki" data-wiki="${t.trim()}">${label || t}</a>`)
    .replace(/\[([^\]]+)\]\(([^)\s]+)[^)]*\)/g, (_, t, u) => (/^[^:]+\.md(#.*)?$/i.test(u)
      ? `<a class="wiki" data-href="${u}">${t}</a>`                                     // a note in the vault: opened in place
      : `<a href="${safeUrl(u)}" target="_blank" rel="noreferrer">${t}</a>`))
    .replace(/\*\*([^*]+)\*\*|__([^_]+)__/g, (_, a, b) => `<strong>${a || b}</strong>`)
    .replace(/(^|[^*\w])\*([^*\n]+)\*(?!\*)|(^|\W)_([^_\n]+)_(?!\w)/g, (_, p1, a, p3, b) => `${p1 ?? p3}<em>${a || b}</em>`)
    .replace(/~~([^~]+)~~/g, '<del>$1</del>')
  return s.replace(/\u0000(\d+)\u0000/g, (_, i) => `<code>${esc(codes[i])}</code>`)
}

export function markdown(src) {
  const lines = String(src || '').replace(/\r\n?/g, '\n').split('\n')
  const out = []
  let i = 0
  const para = []
  const flush = () => { if (para.length) { out.push(`<p>${inline(para.join(' '))}</p>`); para.length = 0 } }
  while (i < lines.length) {
    const ln = lines[i]
    const fence = ln.match(/^\s*(```|~~~)\s*([\w+-]*)/)
    if (fence) {
      flush()
      const body = []
      for (i++; i < lines.length && !lines[i].trim().startsWith(fence[1]); i++) body.push(lines[i])
      i++
      const lang = langOf('x.' + (fence[2] || '')) || ''
      out.push(`<pre class="code"><code>${highlight(body.join('\n'), lang)}</code></pre>`)
      continue
    }
    const h = ln.match(/^(#{1,6})\s+(.*?)\s*#*\s*$/)
    if (h) { flush(); out.push(`<h${h[1].length}>${inline(h[2])}</h${h[1].length}>`); i++; continue }
    if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(ln)) { flush(); out.push('<hr>'); i++; continue }
    if (/^\s*>/.test(ln)) {
      flush()
      const q = []
      for (; i < lines.length && /^\s*>/.test(lines[i]); i++) q.push(lines[i].replace(/^\s*>\s?/, ''))
      out.push(`<blockquote>${markdown(q.join('\n'))}</blockquote>`)
      continue
    }
    if (/^\s*\|.*\|\s*$/.test(ln) && /^\s*\|?[\s:|-]+\|?\s*$/.test(lines[i + 1] || '')) {
      flush()
      const row = r => r.trim().replace(/^\||\|$/g, '').split('|').map(c => c.trim())
      const head = row(ln)
      const body = []
      for (i += 2; i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i]); i++) body.push(row(lines[i]))
      out.push(`<div class="tbl"><table><thead><tr>${head.map(c => `<th>${inline(c)}</th>`).join('')}</tr></thead><tbody>${
        body.map(r => `<tr>${r.map(c => `<td>${inline(c)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`)
      continue
    }
    const li = ln.match(/^(\s*)([-*+]|\d+[.)])\s+(.*)/)
    if (li) {
      flush()
      const ordered = /\d/.test(li[2]), items = []
      for (; i < lines.length; i++) {
        const m = lines[i].match(/^(\s*)([-*+]|\d+[.)])\s+(.*)/)
        if (m && m[1].length <= li[1].length) items.push([m[3]])
        else if (lines[i].trim() && /^\s{2,}/.test(lines[i]) && items.length) items[items.length - 1].push(lines[i].replace(/^\s{2,4}/, ''))
        else break
      }
      const tag = ordered ? 'ol' : 'ul'
      out.push(`<${tag}>${items.map(([first, ...rest]) => {
        const task = first.match(/^\[([ xX])\]\s+(.*)/)
        const head = task ? `<input type="checkbox" disabled${task[1] !== ' ' ? ' checked' : ''}> ${inline(task[2])}` : inline(first)
        return `<li>${head}${rest.length ? markdown(rest.join('\n')) : ''}</li>`
      }).join('')}</${tag}>`)
      continue
    }
    if (!ln.trim()) { flush(); i++; continue }
    para.push(ln.trim()); i++
  }
  flush()
  return out.join('\n')
}

"""Turn a model answer into a page you can actually read.

The model already writes good Markdown with LaTeX in it - headings, tables,
\\[ ... \\] equations. Spoken aloud that becomes "backslash frac backslash",
and printed in a terminal it is not much better. Rendering it to HTML gets
the equations and tables looking the way they do in a chat app.

No Markdown library is installed and this needs to work offline, so the
converter here is deliberately small: headings, bold, italics, code, lists,
tables and paragraphs. Equations are handed to MathJax when there is a
network, and left as written when there is not.
"""
from __future__ import annotations

import html
import re
import time
import webbrowser
from pathlib import Path

from . import plot
from .config import DATA

ANSWERS = DATA / "answers"

# The phone interface turns this off: asking a question from your phone
# should not make a browser window appear on the PC in another room.
AUTO_OPEN = True

_CSS = """
:root { color-scheme: light dark; }
body { margin: 0 auto; padding: 2.5rem 1.5rem 4rem; max-width: 46rem;
       font: 17px/1.7 -apple-system, "Segoe UI", Roboto, sans-serif;
       background: #faf9f7; color: #1c1b19; }
h1 { font-size: 1.6rem; margin: 0 0 .3rem; }
h2 { font-size: 1.25rem; margin: 2rem 0 .6rem; }
h3 { font-size: 1.08rem; margin: 1.5rem 0 .5rem; }
.q { color: #6b6862; font-size: .95rem; margin-bottom: 2rem;
     padding-bottom: 1rem; border-bottom: 1px solid #e3e0da; }
table { border-collapse: collapse; margin: 1.2rem 0; width: 100%; }
th, td { border: 1px solid #ddd9d2; padding: .5rem .7rem; text-align: left; }
th { background: #f0ede7; font-weight: 600; }
code { background: #f0ede7; padding: .12rem .35rem; border-radius: 4px;
       font: .9em ui-monospace, Consolas, monospace; }
pre { background: #f0ede7; padding: .9rem 1rem; border-radius: 8px;
      overflow-x: auto; }
pre code { background: none; padding: 0; }
li { margin: .3rem 0; }
blockquote { margin: 1.4rem 0; padding: .8rem 1.1rem; border-radius: 8px;
             background: #fdf4e7; border-left: 3px solid #c2884a;
             color: #5c4a33; font-size: .95rem; }
blockquote p { margin: .4rem 0; }
blockquote ul { margin: .4rem 0; padding-left: 1.2rem; }
img { max-width: 100%; border-radius: 8px; margin: 1.2rem 0;
      border: 1px solid #e3e0da; display: block; }
strong { font-weight: 650; }
@media (prefers-color-scheme: dark) {
  body { background: #1a1917; color: #e8e6e1; }
  .q { color: #9a968e; border-bottom-color: #33312d; }
  th { background: #26241f; } th, td { border-color: #33312d; }
  code, pre { background: #26241f; }
  blockquote { background: #2b2419; border-left-color: #a9762f; color: #e0d3bf; }
}
"""

_MATHJAX = """
<script>window.MathJax={tex:{inlineMath:[['\\\\(','\\\\)']],
displayMath:[['\\\\[','\\\\]'],['$$','$$']]},options:{skipHtmlTags:['script','style','pre','code']}};</script>
<script async src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js"></script>
"""


_DISPLAY_OR_FENCE = re.compile(r"(\$\$.*?\$\$|```.*?```|`[^`\n]*`)", re.S)
_DOLLAR_MATH = re.compile(r"(?<!\$)\$(?!\$)[ \t]*([^$\n]+?)[ \t]*\$(?!\$)")


def dollar_math(md: str) -> str:
    r"""Rewrite `$ x $` as `\( x \)` so MathJax renders it.

    Models do not agree on how to mark inline maths. qwen2.5 writes \( ... \)
    and qwen3 writes $ ... $, and MathJax is deliberately not configured for a
    single dollar - it would turn "costs $5 to $10" into an equation. So the
    conversion happens here, where it can be limited to a pair of dollars on
    one line, which currency almost never is.

    Display maths ($$ ... $$) already works and is left alone, as is anything
    inside code, where a dollar is a dollar.
    """
    parts = _DISPLAY_OR_FENCE.split(md)
    # split() gives text, delimiter, text, delimiter, ...; only the text needs
    # converting.
    for i in range(0, len(parts), 2):
        parts[i] = _DOLLAR_MATH.sub(r"\\(\1\\)", parts[i])
    return "".join(parts)


def _inline(text: str) -> str:
    """Bold, italics and inline code, leaving LaTeX untouched for MathJax."""
    # Escape first. Maths is full of "<" - P(X < 2), \(1 < x \leq 2\) - and
    # written into the page raw, the browser reads from that "<" to the next
    # ">" as a tag and silently eats the text between them. Whole clauses of
    # an answer disappeared this way. MathJax is unaffected: the browser
    # turns &lt; back into "<" before MathJax ever sees it.
    text = html.escape(text, quote=False)

    # Images after escaping: a data URI has no angle brackets to protect, and
    # the tag built here must survive as a tag.
    text = re.sub(r"!\[([^\]]*)\]\((data:image/[^)]+|[^)]+)\)",
                  lambda m: f'<img alt="{m.group(1)}" src="{m.group(2)}">',
                  text)
    text = re.sub(r"`([^`]+)`", lambda m: "<code>" + m.group(1) + "</code>", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<em>\1</em>", text)
    return text


def _table(rows: list[str]) -> str:
    """A Markdown pipe table. The dashed separator row is not data."""
    cells = [[c.strip() for c in r.strip().strip("|").split("|")] for r in rows]
    body = [r for r in cells if not all(re.fullmatch(r":?-{2,}:?", c or "-")
                                        for c in r)]
    if not body:
        return ""
    out = ["<table>", "<tr>" + "".join(f"<th>{_inline(c)}</th>"
                                       for c in body[0]) + "</tr>"]
    for row in body[1:]:
        out.append("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in row) + "</tr>")
    out.append("</table>")
    return "".join(out)


def to_html_body(md: str) -> str:
    """Markdown (with LaTeX left alone) to an HTML fragment."""
    lines = dollar_math(md.replace("\r\n", "\n")).split("\n")
    out: list[str] = []
    para: list[str] = []
    table: list[str] = []
    quote: list[str] = []
    list_tag = ""
    in_code = False

    def flush_para():
        if para:
            out.append("<p>" + _inline(" ".join(para)) + "</p>")
            para.clear()

    def flush_quote():
        """A "> ..." block, rendered by running its contents through again."""
        if quote:
            inner = "\n".join(quote)
            quote.clear()
            out.append("<blockquote>" + to_html_body(inner) + "</blockquote>")

    def flush_table():
        if table:
            out.append(_table(table))
            table.clear()

    def flush_list():
        nonlocal list_tag
        if list_tag:
            out.append(f"</{list_tag}>")
            list_tag = ""

    for raw in lines:
        line = raw.rstrip()

        if line.strip().startswith("```"):
            flush_para(); flush_table(); flush_list()
            out.append("</code></pre>" if in_code else "<pre><code>")
            in_code = not in_code
            continue
        if in_code:
            out.append(html.escape(raw) + "\n")
            continue

        m = re.match(r"^\s*>\s?(.*)$", line)
        if m:
            flush_para(); flush_table(); flush_list()
            quote.append(m.group(1))
            continue
        flush_quote()

        if line.strip().startswith("|") and line.count("|") >= 2:
            flush_para(); flush_list()
            table.append(line)
            continue
        flush_table()

        if not line.strip():
            flush_para(); flush_list()
            continue

        m = re.match(r"^(#{1,4})\s+(.*)$", line)
        if m:
            flush_para(); flush_list()
            level = min(len(m.group(1)) + 1, 4)
            out.append(f"<h{level}>{_inline(m.group(2))}</h{level}>")
            continue

        m = re.match(r"^\s*[-*+]\s+(.*)$", line)
        if m:
            flush_para()
            if list_tag != "ul":
                flush_list(); out.append("<ul>"); list_tag = "ul"
            out.append(f"<li>{_inline(m.group(1))}</li>")
            continue

        m = re.match(r"^\s*\d+[.)]\s+(.*)$", line)
        if m:
            flush_para()
            if list_tag != "ol":
                flush_list(); out.append("<ol>"); list_tag = "ol"
            out.append(f"<li>{_inline(m.group(1))}</li>")
            continue

        flush_list()
        para.append(line.strip())

    flush_para(); flush_table(); flush_list(); flush_quote()
    if in_code:
        out.append("</code></pre>")
    return "\n".join(out)


def write_page(question: str, answer: str, open_it: bool = True) -> Path:
    """Render an answer to an HTML file and open it. Returns the path.

    Any ```plot block the model wrote becomes a real graph here, embedded in
    the page as a data URI so the file stands on its own.
    """
    try:
        answer, _drawn = plot.expand(answer)
    except Exception:
        pass        # a failed graph must not cost the whole answer
    ANSWERS.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    slug = re.sub(r"[^a-z0-9]+", "-", question.lower())[:40].strip("-") or "answer"
    path = ANSWERS / f"{stamp}-{slug}.html"

    page = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{html.escape(question[:70])}</title>"
        f"<style>{_CSS}</style>{_MATHJAX}</head><body>"
        f"<h1>{html.escape(question)}</h1>"
        f"<div class='q'>Answered by Athena - {time.strftime('%d %b %Y, %H:%M')}</div>"
        f"{to_html_body(answer)}"
        "</body></html>"
    )
    path.write_text(page, encoding="utf-8")
    if open_it and AUTO_OPEN:
        try:
            webbrowser.open(path.as_uri())
        except Exception:
            pass
    return path

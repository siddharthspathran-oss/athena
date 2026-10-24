// A small syntax highlighter for previews: comments, strings, numbers, keywords and
// function calls in the common languages. Escapes first, so file contents can't inject HTML.
const KW = {
  js: 'await async break case catch class const continue default delete do else export extends finally for from function if import in instanceof let new of return static super switch this throw try typeof var void while yield true false null undefined interface type enum implements readonly private public protected as',
  py: 'and as assert async await break class continue def del elif else except finally for from global if import in is lambda nonlocal not or pass raise return try while with yield True False None self',
  go: 'break case chan const continue default defer else fallthrough for func go goto if import interface map package range return select struct switch type var true false nil',
  rs: 'as async await break const continue crate dyn else enum extern false fn for if impl in let loop match mod move mut pub ref return self Self static struct super trait true type unsafe use where while',
  c: 'auto break case char const continue default do double else enum extern float for goto if int long register return short signed sizeof static struct switch typedef union unsigned void volatile while class public private protected new delete this true false null namespace using template virtual override bool string var final abstract interface extends implements package import',
  sh: 'if then else elif fi for while do done case esac function return in export local echo exit',
  css: '',
}
const LANG = {
  js: 'js', jsx: 'js', ts: 'js', tsx: 'js', mjs: 'js', cjs: 'js', vue: 'js', svelte: 'js',
  py: 'py', pyw: 'py', go: 'go', rs: 'rs',
  c: 'c', h: 'c', cpp: 'c', hpp: 'c', cc: 'c', cs: 'c', java: 'c', kt: 'c', swift: 'c', dart: 'c', php: 'c',
  sh: 'sh', bash: 'sh', ps1: 'sh', bat: 'sh', css: 'css', scss: 'css', less: 'css',
  json: 'json', yaml: 'json', yml: 'json', toml: 'json', ini: 'json', html: 'html', xml: 'html', svg: 'html', md: 'md',
}
export const esc = s => String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]))
export const langOf = name => LANG[(name.split('.').pop() || '').toLowerCase()] || ''

const sets = {}
const kwSet = l => (sets[l] ||= new Set((KW[l] || '').split(' ').filter(Boolean)))

export function highlight(code, lang) {
  if (!lang || lang === 'md' || code.length > 400_000) return esc(code)
  if (lang === 'html') return esc(code).replace(/(&lt;\/?)([\w-]+)/g, '$1<b class="h-k">$2</b>').replace(/([\w-]+)=(&quot;.*?&quot;)/g, '<i class="h-a">$1</i>=<i class="h-s">$2</i>')
  const hash = ['py', 'sh', 'json'].includes(lang), slash = !['py', 'sh'].includes(lang)
  const re = new RegExp([
    slash ? '(\\/\\*[\\s\\S]*?\\*\\/|\\/\\/[^\\n]*)' : '((?!))',
    hash ? '(#[^\\n]*)' : '((?!))',
    lang === 'py' ? '("""[\\s\\S]*?"""|\'\'\'[\\s\\S]*?\'\'\')' : '((?!))',
    '("(?:\\\\.|[^"\\\\\\n])*"|\'(?:\\\\.|[^\'\\\\\\n])*\'|`(?:\\\\.|[^`\\\\])*`)',
    '(\\b\\d[\\d_]*(?:\\.\\d+)?(?:e[+-]?\\d+)?\\b|\\b0x[\\da-f]+\\b)',
    '([A-Za-z_$][\\w$]*)',
  ].join('|'), 'gi')
  let out = '', i = 0, m
  const kws = kwSet(lang)
  while ((m = re.exec(code))) {
    if (m[0] === '') { re.lastIndex++; continue }
    out += esc(code.slice(i, m.index))
    const t = esc(m[0])
    if (m[1] || m[2]) out += `<i class="h-c">${t}</i>`
    else if (m[3] || m[4]) out += `<i class="h-s">${t}</i>`
    else if (m[5]) out += `<i class="h-n">${t}</i>`
    else if (kws.has(m[0])) out += `<b class="h-k">${t}</b>`
    else if (/^\s*\(/.test(code.slice(re.lastIndex, re.lastIndex + 4))) out += `<i class="h-f">${t}</i>`
    else out += t
    i = re.lastIndex
  }
  return out + esc(code.slice(i))
}

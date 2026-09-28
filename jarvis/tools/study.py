"""Explaining things properly, for class work.

The ordinary voice reply is deliberately terse - it is read aloud, and every
word costs the user real seconds. That is the wrong shape for "explain how a
transformer works" or "solve this integral": the model produces headings,
tables and LaTeX, which then get spoken as "backslash frac backslash" or
printed as unreadable markup.

So a study question takes a different route: ask for the full worked answer,
render it to a page, open it, and say one short sentence out loud.
"""
from __future__ import annotations

import re
import time

from .. import cloud, plot, render, verify
from ..config import DATA, cfg
from ..llm import LLM
from ..registry import tool
from ..store import load_json, save_json

TAG = ["study"]

STUDY_PROMPT = """You are helping a university engineering student understand
something for their classes. Answer in Markdown, formatted to be read on a
page rather than spoken.

- Open with one short sentence saying what the answer is. For a question
  you have to calculate, say instead what is being found and by which
  method - do not put a number in that sentence. The result comes after the
  working, never before it.
- Use ## headings to separate the parts of a longer answer.
- Put every formula in LaTeX: \\( inline \\) and \\[ displayed \\].
- Derive things step by step, numbering the steps, and say what each step
  does rather than only showing the algebra.
- Set out every calculation the way a marker expects to see it, for every
  part of the question: the formula in symbols first, then the same formula
  with the numbers substituted in, then each intermediate value, then the
  result - with its unit, and one sentence saying what it means. Marks are
  given for the working, so a result without these steps earns little even
  when it is right.
- Frequency data (x against f): make a table with columns x, f, x*f and
  x^2*f and a totals row, and get the mean and standard deviation from those
  totals. Add a long column as a running total written out - 0 + 7 = 7,
  7 + 120 = 127, 127 + 594 = 721, and so on - never as one line of twelve
  numbers with the total after it. That one line is where the total goes
  wrong.
- Never claim a value is exact, or "from a calculator or software", unless
  the working that produces it is on the page. If an exact sum is too long
  to do by hand, say so, use a named approximation (normal, Poisson), show
  it, and call the result approximate.
- Write the finished working, not your drafts: no "wait", no "let me
  re-check" - if something does not add up, fix it and present the corrected
  working only.
- End every calculated answer with a line starting **Final answer:** that
  lists the result of each part.
- If the question compares two or more things, you MUST give a Markdown
  table with one row per property, before any prose. A list of bullet points
  is not an acceptable substitute - the whole point is to see the two side
  by side. For example:

  | Property | BJT | MOSFET |
  | --- | --- | --- |
  | Controlled by | Base current | Gate voltage |
  | Input impedance | Low | Very high |

- Also use a table for sets of values, units, or a truth table.
- If the question says draw, sketch, plot or graph something, you MUST
  produce a ```plot block for it. Describing the shape in words is not
  answering that question. A step function is plotted with heaviside: the
  distribution function of a discrete variable with steps at 0, 1 and 2 is
  "0.11*heaviside(x) + 0.22*heaviside(x-1) + 0.67*heaviside(x-2)".
- When a graph would show something a table cannot - a waveform, a response
  curve, how one quantity varies with another - draw one by writing a ```plot
  block containing JSON. Do not write Python; this is the only way to make a
  graph, and it is rendered for you:

  ```plot
  {"title": "Inverting amplifier output", "xlabel": "t (ms)",
   "ylabel": "V", "xmin": 0, "xmax": 4,
   "series": [{"expr": "sin(2*pi*x)", "label": "Vin"},
              {"expr": "-10*sin(2*pi*x)", "label": "Vout"}]}
  ```

  x is the variable. sin, cos, exp, log, log10, sqrt, abs, pi and e are
  available. Add "xscale": "log" for a frequency axis. Every expression must
  be written in terms of x with real numbers - no symbols left undefined.

  Every number in a plot must be one you worked out above, not an
  impression of it. Before writing the block, say in one line what the
  amplitude and the frequency are, then make the expression match:

  - amplitude: a gain of -10 on a 1 V peak input means the output expression
    has 10 in front of it, and a minus sign. Not 7, not "about 10".
  - frequency: decide what one unit of x means first. With x in ms, a 1 kHz
    signal is sin(2*pi*1*x) and shows one cycle per unit - so 4 cycles across
    0 to 4. With x in seconds it would be sin(2*pi*1000*x). Count the cycles
    your expression gives over the range and check it against the frequency
    you were given before you write it down.
- Define each symbol the first time it appears, with its unit.
- Finish with a short "In short" line.

Checking your own work is part of the answer:

- An equation can have several roots and only one that means anything. Solve
  it, then test every root against the conditions in the question and say
  which ones you are discarding and why. A probability cannot be negative or
  above 1; a resistance cannot be negative; a count must be a whole number.
- Substitute your answer back into the original statement and confirm it
  holds. If it does not, the answer is wrong - go back rather than writing it
  down with a note that something looks off.
- Do arithmetic one step at a time and write each step out. Do not collapse
  several operations into a single line of mental arithmetic.

Be correct before being brief. If the question is ambiguous, answer the most
likely reading and say which one you took.
"""


_QUESTION = re.compile(r"(?:^|\n)[ \t]*(\d{1,2})[.)]?[ \t]*\n(.{40,}?)(?=\n[ \t]*\d{1,2}[.)]?[ \t]*\n|\Z)",
                       re.S)


def split_questions(text: str) -> list[tuple[str, str]]:
    """Break a worksheet into its numbered questions.

    Answering eight questions in one pass is where the quality goes: asked
    all at once the model drops the instructions it was given - it stopped
    drawing graphs entirely - and its arithmetic slips. The same model
    answering one question on its own draws the graph and gets it right.
    """
    # Look only for the next number in sequence. A worksheet is full of bare
    # digits that are not question numbers - a stacked fraction puts "1" and
    # "5" on lines of their own - and matching any digit found those instead,
    # which broke the numbering and gave up entirely.
    lines = text.splitlines()
    want = 1
    starts: list[tuple[int, int]] = []
    for i, line in enumerate(lines):
        if re.fullmatch(rf"\s*{want}[.)]?\s*", line):
            starts.append((want, i))
            want += 1
    if len(starts) < 2:
        return []

    found: list[tuple[str, str]] = []
    for idx, (number, start) in enumerate(starts):
        end = starts[idx + 1][1] if idx + 1 < len(starts) else len(lines)
        body = "\n".join(lines[start + 1:end]).strip()
        # Drop the marks/outcome tag the sheet carries: "2 CO1".
        body = re.sub(r"\s*\d+\s+CO\s*\d+\s*$", "", body).strip()
        if len(body) > 40:
            found.append((str(number), body))
    return found if len(found) >= 2 else []


# What the model picker offers. (label, model string, what it needs)
#
# Checked against each provider's live model list rather than written from
# memory - gemini-2.5-flash is already refused for new keys, and DeepSeek and
# Kimi are not among OpenRouter's free models however often they are
# described as free. Anything needing a key you have not added says so in the
# picker instead of failing when you use it.
STUDY_CHOICES: list[tuple[str, str, str]] = [
    ("Gemini 3.5 Flash  (free)", "gemini:gemini-3.5-flash", "gemini"),
    ("Gemini 3.5 Flash-Lite  (free)", "gemini:gemini-3.5-flash-lite", "gemini"),
    ("Qwen 3.8 27B  (free)", "openrouter:qwen/qwen3.8-27b:free", "openrouter"),
    ("GLM 5.2  (free)", "openrouter:z-ai/glm-5.2:free", "openrouter"),
    ("GPT-OSS 120B  (free, Groq - fastest)", "groq:openai/gpt-oss-120b", "groq"),
    ("Qwen 3.8 27B  (free, Groq)", "groq:qwen/qwen3.8-27b", "groq"),
    ("GPT-OSS 20B  (free, Groq)", "groq:openai/gpt-oss-20b", "groq"),
    ("qwen3:8b  (on this PC, slow)", "qwen3:8b", ""),
    ("qwen2.5:7b  (on this PC, quick)", "qwen2.5:7b", ""),
]

# Kimi, DeepSeek and Grok are deliberately absent. Kimi and DeepSeek are on
# OpenRouter but every variant is paid; Grok (xAI, not to be confused with
# Groq) gives $25 of signup credit and then charges - its free data-sharing
# programme ended and the replacement requires spending $5 first. Offering
# any of them here would be offering something that stops working.

# Chosen in the interface rather than the config file, so it survives a
# restart without anyone hand-editing YAML.
_CHOICE_FILE = DATA / "study_model.json"


def choices() -> list[dict]:
    """The picker's options, each marked with whether it can actually run."""
    from .. import cloud

    live = current_model()
    out = []
    for label, model, needs in STUDY_CHOICES:
        ready = not needs or bool(cloud.get_key(needs))
        out.append({"label": label, "model": model, "ready": ready,
                    "needs": needs, "live": model == live})
    return out


def current_model() -> str:
    """The study model in force: the picker's choice, else config.yaml."""
    picked = load_json(_CHOICE_FILE, {}).get("model", "")
    return picked or str(cfg.get("study.model") or "")


def set_model(model: str) -> str:
    """Change the study model, and prove it actually answers.

    Switching used to be a silent write to a file, so a retired model name,
    an expired key or a provider having a bad afternoon only showed up when a
    worksheet came back empty. A two-token question costs a second and turns
    all three into something you are told immediately.
    """
    import time

    from .. import cloud

    known = {m for _, m, _ in STUDY_CHOICES}
    if model and model not in known:
        return f"ERROR: '{model}' is not one of the models on offer."
    save_json(_CHOICE_FILE, {"model": model})

    needs = next((n for _, m, n in STUDY_CHOICES if m == model), "")
    if needs and not cloud.get_key(needs):
        return (f"Switched to {model}, but there is no {needs} key yet.\n"
                f"Run:  python run.py key {needs}")

    hello = [{"role": "user", "content": "Reply with the single word: ready"}]
    # A generous budget for a two-word answer, because a reasoning model
    # spends most of it thinking before it says anything: gpt-oss-120b
    # returned an empty string on 20 tokens and "391" on 2,000. A tight
    # budget here would report a perfectly good model as broken.
    t0 = time.time()
    try:
        if cloud.is_cloud(model):
            reply = cloud.chat(model, hello, temperature=0, max_tokens=2000,
                               timeout=45)
        else:
            msg = LLM(model or None).chat(hello, temperature=0, max_tokens=2000,
                                          think=False)
            reply = getattr(msg, "content", "") or ""
    except Exception as e:
        first = str(e).splitlines()[0][:130]
        return (f"Switched to {model}, but it did not answer: {first}\n"
                f"Pick another, or run:  python run.py models "
                f"{needs or 'ollama'}")

    took = time.time() - t0
    if not reply.strip():
        return f"Switched to {model}, but it replied with nothing."
    return f"Class work now goes to {model} - answered in {took:.1f}s."


def _model() -> LLM:
    """The model that answers class work - `study.model`, or the everyday one.

    A hosted model is not an Ollama model, so the LLM object built here is
    only a carrier; _ask notices the prefix and never calls it.
    """
    configured = current_model() or None
    if configured and cloud.is_cloud(configured):
        return LLM(None)
    return LLM(configured)


# Questions worth deliberating over. A reasoning model earns its time working
# something out - it scored 10/10 on the probability worksheet where the
# everyday model managed 4, 5 and 7 - and wastes it on prose.
#
# Keyed on whether there is something to calculate, not on the subject. An
# earlier version listed topic words, "probability" among them, which meant
# every question on a probability course deliberated: "state the classical
# and empirical definitions of probability" took 266 seconds to answer, and
# there is not a number in it.
_ASKS_FOR_WORK = re.compile(
    r"\d\s*[-+*/^=]|[-+*/^=]\s*\d"          # arithmetic, not a bare year
    r"|\\frac|\\int|\\sum|\\sqrt|\^\d"       # written as maths
    r"|\b(?:solve|calculate|compute|evaluate|derive|prove|integrate|"
    r"differentiate)\b"
    r"|\b(?:find|determine|obtain|what\s+is|what\s+are)\s+(?:the\s+)?"
    r"(?:value|probability|mean|median|mode|variance|standard\s+deviation|"
    r"deviation|number|smallest|largest|expected|distribution\s+function)\b"
    r"|\bhow\s+many\b|\bhow\s+much\b|\bfit\s+the\b", re.I)

# ...and prose that only looks like work because of the subject it is in.
_JUST_PROSE = re.compile(
    r"^\W*(?:state|define|explain|describe|list|compare|contrast|"
    r"discuss|outline|summari[sz]e|what\s+is\s+meant\s+by|"
    r"what\s+(?:is|are)\s+the\s+difference)\b", re.I)


def _thinking(question: str) -> bool | None:
    """Whether to let the model reason at length before answering."""
    mode = str(cfg.get("study.think", "auto")).strip().lower()
    if mode in ("true", "yes", "on", "always"):
        return True
    if mode in ("false", "no", "off", "never"):
        return False
    if mode not in ("auto", ""):
        return None                      # leave the model's own default alone
    # "State the definitions and explain how they differ" is prose however
    # mathematical the course is; only real arithmetic inside it overrides.
    if _JUST_PROSE.match(question.strip()) and not _ASKS_FOR_WORK.search(question):
        return False
    return bool(_ASKS_FOR_WORK.search(question))


def _retry_after(error: str) -> float | None:
    """Seconds to wait before asking a rate-limited model again, or None if
    this failure will not fix itself by waiting.

    Groq words it "Please try again in 7.3s" (or "1m2s", "450ms"). A request
    that is simply too big for the free tier - "Request too large ... reduce
    max_tokens": qwen3.8-27b allows 1,000 output tokens a minute and a worked
    answer needs several thousand - fails the same way every time, so it is
    not worth a wait.
    """
    low = error.lower()
    if "429" not in low and "rate limit" not in low:
        return None
    if "request too large" in low or "exceed the enforced limit" in low:
        return None
    m = re.search(r"try again in\s+(?:(\d+)m)?\s*(\d+(?:\.\d+)?)(ms|s)\b", low)
    if not m:
        return 5.0
    seconds = int(m.group(1) or 0) * 60 + float(m.group(2)) / (
        1000 if m.group(3) == "ms" else 1)
    # Longer than this and the next rung is the better bet; a daily quota
    # says "try again in 7h" and must not hang a worksheet.
    return max(3.0, seconds + 0.5) if seconds <= 30 else None


# Which rung of the fallback chain produced the last answer. A worksheet
# page on 23 Sep came back 8/10 with question 6 plainly written by the local
# 7B (E(x) = n*p*x, a mean of 5.45) while its neighbours were cloud answers,
# and nothing recorded that it had fallen through. studybench prints this.
last_answered_by = ""


def _ask(llm: LLM, messages: list[dict], think: bool | None = None) -> str:
    """One turn at the study settings, with any reasoning trace removed.

    A reasoning model works through the problem out loud before answering,
    and that scratch work is not the answer - left in, it lands on the page
    ahead of the actual working. Ollama returns it separately for models it
    knows think; the ones that emit <think> tags inline are stripped here.
    """
    temperature = float(cfg.get("study.temperature", 0.1))
    max_tokens = int(cfg.get("study.max_tokens", 6000))

    # A hosted model, if study.model names one ("groq:...", "gemini:...").
    # Nothing else in Athena takes this route.
    #
    # Free tiers run out, and they run out on the evening before a deadline
    # rather than at a convenient moment. So the chain is tried in order and
    # ends at the local model, which has no quota and cannot be rate limited:
    # slower, but it always answers.
    # Deduplicated, keeping order: the primary is usually also the first
    # fallback in a hand-written config, and retrying the model that just
    # answered 429 spends another request to be refused again.
    chain: list[str] = []
    for m in [current_model()] + [str(x) for x in (cfg.get("study.fallback") or [])]:
        if m and m not in chain:
            chain.append(m)
    trouble: list[str] = []

    context = int(cfg.get("study.context_window",
                          cfg.get("llm.context_window", 8192)))
    text = ""
    answered_by = ""
    queue = list(chain)
    retried: dict[str, float] = {}       # seconds already spent waiting
    while queue:
        model = queue.pop(0)
        try:
            if cloud.is_cloud(model):
                text = cloud.chat(model, messages, temperature, max_tokens)
            else:
                # A local name here, or "" meaning the everyday model.
                msg = LLM(model or None).chat(
                    messages, temperature=temperature, context=context,
                    think=think, max_tokens=max_tokens)
                text = getattr(msg, "content", "") or ""
            if text.strip():
                if trouble:
                    print(f"[study] {model} answered after " + "; ".join(trouble))
                answered_by = model
                break
        except Exception as e:
            first = str(e).splitlines()[0]
            trouble.append(f"{model} failed ({first[:80]})")
            wait = _retry_after(str(e))
            waited = retried.get(model, 0.0)
            if wait is not None and waited + wait <= 90:
                # A rate limit is the one failure that fixes itself. Free
                # tiers count per minute and a worksheet fires eight
                # questions in a row, so waiting the few seconds the provider
                # names and asking the same model again beats dropping a
                # rung - dropping rungs is how a worksheet ends up on the
                # local 7B. This used to sleep and then move on regardless,
                # so the wait bought nothing: on 24 Sep questions 3 and 5
                # went to qwen2.5:7b that way.
                #
                # More than one retry, up to 90s in all: gpt-oss-120b's
                # free tier is 8,000 tokens a minute and each request books
                # ~5,800 of them up front, so after one full answer the next
                # is refused with "try again in 4.9s" - and refused again
                # after that 4.9s, because the window drains gradually.
                retried[model] = waited + wait
                time.sleep(wait)
                queue.insert(0, model)

    if not text.strip() and trouble:
        raise RuntimeError("every model in study.fallback failed - "
                           + "; ".join(trouble))
    global last_answered_by
    last_answered_by = answered_by
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S | re.I)
    text = re.sub(r"^.*?</think>", "", text, flags=re.S | re.I)   # trace cut short
    return text.strip()


def _faults(answer: str) -> list[str]:
    """Everything checkable that is wrong with an answer."""
    try:
        return verify.problems(answer) + plot.problems(answer)
    except Exception:
        # The checker is a safety net, not a gate. If it throws, the answer
        # still goes out.
        return []


def _ask_checked(llm: LLM, messages: list[dict], note=None, label: str = "",
                 think: bool | None = None) -> str:
    """Ask, check the arithmetic, and give the model its mistakes back.

    The model is perfectly capable of the method and unreliable at the
    sums - it wrote (4/5)^9 as 0.214748 and a twelve-term total as 22806 -
    so being handed the specific line that does not add up is far more use
    to it than another sentence in the prompt telling it to be careful.

    Whichever attempt has the fewest surviving faults is the one returned,
    so a retry can never make the answer worse than the first try.
    """
    rounds = 1 + max(0, int(cfg.get("study.recheck_attempts", 1)))
    if not cfg.get("study.verify", True):
        rounds = 1

    best_text, best_faults = "", None
    for attempt in range(rounds):
        try:
            text = _ask(llm, messages, think)
        except Exception:
            # A retry that fails must not cost the answer we already have.
            if best_faults is not None:
                break
            raise
        if not text:
            continue
        faults = _faults(text)
        if best_faults is None or len(faults) < len(best_faults):
            best_text, best_faults = text, faults
        if not faults:
            return text
        if attempt + 1 >= rounds:
            break
        if note:
            note(f"rechecking {label}".strip())
        listed = "\n".join(f"- {f}" for f in faults)
        messages = messages + [
            {"role": "assistant", "content": text},
            {"role": "user", "content":
                f"I checked that answer and these specific steps are wrong:\n\n"
                f"{listed}\n\nThe method may well be right - these are "
                f"arithmetic and bookkeeping mistakes. Redo the whole answer "
                f"from the start, recomputing each of those steps carefully "
                f"and carrying the corrected values through everything that "
                f"depends on them. Give the complete answer again, not just "
                f"the corrections."},
        ]

    if best_faults:
        best_text += ("\n\n> **Check these steps yourself.** An automatic check "
                      "of the arithmetic still disagrees with the working here, "
                      "and asking again did not resolve it:\n>\n"
                      + "\n".join(f"> - {f}" for f in best_faults))
    return best_text


_MATH_WORDS = {
    r"\cdot": "times", r"\times": "times", r"\div": "divided by",
    r"\approx": "is about", r"\leq": "is at most", r"\geq": "is at least",
    r"\neq": "is not", r"\pm": "plus or minus", r"\infty": "infinity",
    r"\alpha": "alpha", r"\beta": "beta", r"\theta": "theta", r"\pi": "pi",
    r"\mu": "mu", r"\sigma": "sigma", r"\omega": "omega", r"\Delta": "delta",
    r"\rightarrow": "gives", r"\to": "gives", r"\ldots": "and so on",
}


def _speakable(text: str) -> str:
    r"""Turn a line of LaTeX-laden Markdown into something a person can read.

    This line is both the chat bubble on the phone and the sentence spoken
    aloud. Left as written it arrives as "$ V = I \cdot R $", which reads back
    as "dollar V equals I backslash cdot R dollar" - the exact complaint that
    sent the whole page to MathJax in the first place, reappearing in the one
    place MathJax never sees.
    """
    text = re.sub(r"\$\$(.+?)\$\$", r"\1", text, flags=re.S)
    text = re.sub(r"\\\[(.+?)\\\]", r"\1", text, flags=re.S)
    text = re.sub(r"\\\((.+?)\\\)", r"\1", text, flags=re.S)
    text = re.sub(r"(?<!\$)\$(?!\$)\s*([^$\n]+?)\s*\$(?!\$)", r"\1", text)
    text = re.sub(r"\\frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}", r"\1 over \2", text)
    text = re.sub(r"\\sqrt\s*\{([^{}]*)\}", r"the square root of \1", text)
    text = re.sub(r"\^\{?(\w+)\}?", r" to the power \1", text)
    text = re.sub(r"_\{?(\w+)\}?", r" \1", text)
    for command, word in _MATH_WORDS.items():
        text = text.replace(command, f" {word} ")
    text = re.sub(r"\\[a-zA-Z]+", " ", text)        # anything else left over
    text = text.replace("**", "").replace("{", "").replace("}", "")
    return re.sub(r"\s+", " ", text).strip()


_FINAL = re.compile(r"^\W*final\s+answer\W*?:\**\s*(\S.*)$", re.I | re.M)


def _lead_line(answer: str, prefer_final: bool = False) -> str:
    # A calculated answer no longer opens with its result - asked to, a model
    # without a reasoning pass guessed it: question 5 opened with "1024/1025"
    # and then worked its way to the correct 0.6993 underneath. So the result
    # to speak is on the closing "Final answer:" line when there is one.
    final = _FINAL.search(answer) if prefer_final else None
    if final:
        line = _speakable(final.group(1).strip())
        if line:
            line = "Final answer: " + line
            return line[:177].rsplit(" ", 1)[0] + "..." if len(line) > 180 else line
    for line in answer.splitlines():
        line = line.strip()
        if line and not line.startswith(("#", "|", "-", "*", "\\[", "```", ">")):
            line = _speakable(line)
            if not line:
                continue
            return line[:177].rsplit(" ", 1)[0] + "..." if len(line) > 180 else line
    return ""


def _finished(started: float) -> None:
    """Phone the user when a long answer is ready. A worksheet on the local
    model has taken most of an hour, which is long enough to walk away from.
    Only the fact goes out - never the question - because the alert text
    passes through the call and push services."""
    took = time.time() - started
    if took < float(cfg.get("alerts.study.min_minutes", 2)) * 60:
        return
    from .. import alerts

    alerts.submit("study", f"study:{started:.0f}",
                  f"Your study answer is ready. It took {round(took / 60)} minutes.")


def answer_worksheet(context: str, source: str, note=None) -> str | None:
    """Answer a numbered worksheet one question at a time, or None if it
    does not look like one."""
    questions = split_questions(context)
    if not questions:
        return None

    llm = _model()
    parts: list[str] = []
    for number, body in questions:
        if note:
            note(f"question {number} of {len(questions)}")
        try:
            text = _ask_checked(llm, [
                {"role": "system", "content": STUDY_PROMPT},
                {"role": "user", "content":
                    f"This is question {number} of an assignment"
                    f"{' in ' + source if source else ''}. Answer only this "
                    f"question, completely, with every step:\n\n{body}"},
            ], note, f"question {number}", _thinking(body))
        except Exception as e:
            text = f"_(question {number} failed: {e})_"
        parts.append(f"## Question {number}\n\n{text or '_(no answer)_'}")
    return "\n\n".join(parts)


def answer_page(question: str, context: str = "", source: str = "") -> str:
    """Work through a question properly and render it to a page.

    `explain` and an attached document want exactly the same treatment - full
    working, real equations, tables - and differ only in whether there is a
    document in front of it. An attached file used to go through the ordinary
    chat reply instead, which is why it came back as unrendered text with the
    working missing.
    """
    if not question.strip() and not context.strip():
        return "ERROR: say what you would like explained."
    started = time.time()

    # A numbered worksheet is answered question by question; see
    # answer_worksheet for why one pass over all of them is worse.
    if context.strip():
        sheet = answer_worksheet(context, source)
        if sheet:
            title = question.strip() or f"Working through {source or 'the attached file'}"
            try:
                path = render.write_page(title, sheet)
            except Exception as e:
                return sheet[:1500] + f"\n\n[could not open the page: {e}]"
            lead = _lead_line(sheet)
            _finished(started)
            return (f"{lead}\n\nI've worked through every question. "
                    f"[{path.name}]") if lead else f"I've worked through every question. [{path.name}]"

    messages = [{"role": "system", "content": STUDY_PROMPT}]
    if context.strip():
        messages.append({
            "role": "user",
            "content": (f"This is the content of {source or 'the attached file'}:\n\n"
                        f"{context.strip()}\n\n"
                        f"Work through it and answer completely. If it contains "
                        f"questions or problems, solve every one of them in order, "
                        f"showing the steps - do not summarise and do not skip any."),
        })
    messages.append({"role": "user", "content": question.strip() or
                     "Answer everything in that file."})

    llm = _model()
    try:
        answer = _ask_checked(llm, messages, think=_thinking(question + context))
    except Exception as e:
        return f"ERROR: could not answer that: {e}"

    if not answer:
        return "The model returned nothing for that question."

    title = question.strip() or f"Working through {source or 'the attached file'}"
    try:
        path = render.write_page(title, answer)
    except Exception as e:
        return answer[:1500] + f"\n\n[could not open the page: {e}]"

    lead = _lead_line(answer, prefer_final=True)
    _finished(started)

    opened = f"I've opened the full answer with the working. [{path.name}]"
    return f"{lead}\n\n{opened}" if lead else opened


@tool(tags=TAG)
def explain(question: str) -> str:
    """Answer a study question properly and open it as a formatted page.

    Use this for anything from classes - concepts, derivations, worked
    problems, comparisons, "explain", "derive", "solve", "what is the
    difference between". The answer opens in the browser with real equations
    and tables instead of being read out as raw LaTeX.

    question: the question, in the user's own words
    """
    if not question.strip():
        return "ERROR: say what you would like explained."
    started = time.time()

    llm = _model()
    try:
        answer = _ask_checked(llm, [
            {"role": "system", "content": STUDY_PROMPT},
            {"role": "user", "content": question.strip()},
        ], think=_thinking(question))
    except Exception as e:
        return f"ERROR: could not answer that: {e}"

    if not answer:
        return "The model returned nothing for that question."

    try:
        path = render.write_page(question.strip(), answer)
    except Exception as e:
        # Losing the rendering is no reason to lose the answer.
        return answer[:1500] + f"\n\n[could not open the page: {e}]"

    # Spoken reply: the first real sentence, not the whole derivation.
    lead = _lead_line(answer, prefer_final=True)
    _finished(started)

    name = cfg.get("assistant.name", "Athena")
    opened = f"I've opened the full answer with the working. [{path.name}]"
    return f"{lead}\n\n{opened}" if lead else opened

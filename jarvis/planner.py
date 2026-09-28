"""The planner: requests that take several steps, worked through one at a time.

The everyday model (qwen2.5:7b) manages one to three tool calls and then
loses the thread - "find the scheduler pdf, rename it with today's date and
send it to Rahul" comes apart after the first step. So a request that plainly
has several parts goes to a bigger hosted model instead (planner.model,
gpt-oss-120b on Groq by default), which decides one step at a time, sees
each result, and says when it is done.

One step per turn rather than a whole plan up front: a plan written before
anything runs cannot know that the file it meant to rename is called
"scheduler (2).pdf", or that the search found nothing.

What this sends to the hosted model: the request, the list of tool names
and descriptions, and every tool result along the way - file names, the
brief, page text. That is the price of the better model; turn it off with
planner.enabled: false and multi-step requests go to the local model as
before. The same limits apply as anywhere else in Athena: protected folders,
blocked shell commands, no typing passwords, and no pressing a form's final
button - that still needs you to say "submit it".
"""
from __future__ import annotations

import json
import re
import time
from typing import Callable

from . import cloud, registry
from .config import DATA, cfg

PLAN_LOG = DATA / "planner.log"

# "and then", "then", "after that", or two or more action verbs joined by
# "and" / commas: "open X and play Y", "find A, rename it and send it".
_VERBS = (r"open|close|find|search|look\s+up|download|rename|move|copy|delete|send|"
          r"message|text|whatsapp|email|mail|call|play|pause|set|turn|remind|"
          r"schedule|fill|submit|go\s+to|visit|read|summari[sz]e|save|write|"
          r"create|make|take|screenshot|check|tell|show|list|lock|mute|type|"
          r"launch|start|organi[sz]e|add|compare|use|work\s+out|calculate|press|click|"
          r"choose|select|enter|put|turn\s+on|turn\s+off|switch|change|update")
_THEN = re.compile(r"\b(?:and\s+then|then|after\s+that|afterwards|once\s+(?:that'?s|it'?s)\s+done|"
                   r"and\s+also|followed\s+by)\b", re.I)
_VERB = re.compile(rf"\b(?:{_VERBS})\b", re.I)
_JOIN = re.compile(r"\s*(?:,|\band\b)\s*", re.I)


def is_multistep(text: str) -> bool:
    """Worth the planner: says "then", or joins two or more actions."""
    t = text.strip()
    if len(t.split()) < 5:
        return False
    if _THEN.search(t) and len(_VERB.findall(t)) >= 2:
        return True
    parts = [p for p in _JOIN.split(t) if p.strip()]
    acting = [p for p in parts if _VERB.match(p.strip())]
    return len(acting) >= 2


_SYSTEM = """You are the planner inside Athena, a voice assistant running on the user's Windows PC. \
You complete the user's request by calling Athena's tools one at a time. After each call you \
see its result, then choose the next step.

Reply with exactly ONE JSON object and nothing else:
  {{"tool": "<tool name>", "args": {{...}}}}   to run a tool, or
  {{"done": "<short reply to speak to the user>"}}   when finished or stuck.

Rules:
- You know NOTHING about this PC, its files, the user's schedule, battery, mail or anything \
else except what tool results tell you. Every fact in your final reply must come from a tool \
result you have seen. Your first reply is always a tool call.
- Never conclude that something does not exist (no classes, no file, no mail) unless the tool \
that looks for it said so. Not having looked is not the same as none.
- Where to look: classes, timetable, schedule, calendar, meetings, mail -> daily_brief \
(day: "today" or "tomorrow"); a file on the PC -> find_files / open_file / reveal_path; \
the time or date -> get_datetime; websites to act on -> browser_*; a quick web fact -> web_search.
- Use only the tools listed below, with their exact parameter names.
- Pass file and app names as the user said them; the tools search for them.
- Use results you have seen (real paths, names) in later steps; never invent them.
- If a step fails, try one sensible alternative, then stop and say what went wrong.
- Web forms: browser_open, then browser_read, then browser_fill / browser_click using the \
numbers from browser_read. A final submit/pay button is held for the user to confirm - \
when you see that, you are done: tell them to say "submit it".
- Desktop apps: open_app, then app_read, then app_click / app_type with the numbers from \
app_read. Press several controls in a row with ONE app_click, comma-separated \
("33, 34, 28, 27, 40") - every step you take costs the user seconds. Read the app again \
only when you need to see what changed.
- Never type passwords, PINs, OTPs or card details; tell the user to type them.
- Do only what was asked. Keep the final reply to one or two plain sentences.

Tools:
{tools}

Now: {now}"""


def _tool_list(only: list[str] | None = None) -> str:
    lines = []
    for t in registry.REGISTRY.values():
        if only and t.name not in only:
            continue
        props = t.schema["function"]["parameters"].get("properties", {})
        req = set(t.schema["function"]["parameters"].get("required", []))
        params = ", ".join(f"{k}{'' if k in req else '?'}: {v.get('type', 'string')}"
                           for k, v in props.items())
        first = (t.description or "").strip().split("\n")[0][:110]
        lines.append(f"- {t.name}({params}): {first}")
    return "\n".join(lines)


def _parse(text: str) -> dict | None:
    """The first JSON object in the reply. Models wrap it in ``` or add a
    sentence either side, whatever they are told."""
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S)
    start = text.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[start:i + 1])
                        if isinstance(obj, dict):
                            return obj
                    except ValueError:
                        break
        start = text.find("{", start + 1)
    return None


def _chat(messages: list[dict], models: list[str] | None = None) -> tuple[str, str]:
    """(reply, model that answered), trying the chain in order.

    A free tier answering 429 is waited out a few times before moving on:
    Groq's ~8,000 tokens a minute gives a browser task one step every 10-20
    seconds, and giving up at the second refusal stopped a Zomato booking
    with the date and guests already chosen (25 Sep 2026).
    """
    chain = [str(m) for m in (models or [cfg.get("planner.model", "groq:openai/gpt-oss-120b"),
                                        *(cfg.get("planner.fallback") or [])]) if m]
    trouble = []
    for model in dict.fromkeys(chain):
        for attempt in range(4):
            try:
                return cloud.chat(model, messages, temperature=0.0,
                                  max_tokens=int(cfg.get("planner.max_tokens", 1500)),
                                  timeout=60), model
            except RuntimeError as e:
                msg = str(e)
                # Gemini says "quota" for its per-minute limit too; only a
                # per-DAY limit is worth abandoning the model for.
                daily = re.search(r"per\s*day|PerDay|daily", msg, re.I)
                if "429" in msg and not daily and attempt < 3:
                    time.sleep(8 + 6 * attempt)   # per-minute limit: wait for it to refill
                    continue
                trouble.append(f"{model}: {msg[:80]}")
                break                             # daily quota gone: next model
    raise RuntimeError("; ".join(trouble) or "no planner model configured")


_MORE_TO_DO = re.compile(r"\b(?:let me|i(?:'ll| will)|next,? i|now i(?:'ll| will)|i am going to|i'm going to)\b", re.I)


class Stuck(RuntimeError):
    """The planner could not run at all - caller should fall back."""


def run(request: str, on_step: Callable[[str, dict, str], None] | None = None,
        only: list[str] | None = None, result_chars: int = 3000,
        models: list[str] | None = None) -> str:
    """Work through a multi-step request. Returns the reply for the user.
    Raises Stuck if no hosted model answered before anything ran."""
    now = time.strftime("%A %d %B %Y, %H:%M")
    from . import profile
    about = profile.prompt_text()
    # A task that names its tools sends only those. The full list is ~2,500
    # tokens on every step, and Groq's free tier allows ~8,000 a minute: a
    # booking ran out of quota after 87 s on 25 Sep 2026.
    system = _SYSTEM.format(tools=_tool_list(only), now=now) + (
        f"\n\nAbout the user (from earlier conversations):\n{about}" if about else "")
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": request}]
    max_steps = int(cfg.get("planner.max_steps", 10))
    ran: list[str] = []
    seen: dict[str, int] = {}
    pushed = False
    nudged_done = False

    for _ in range(max_steps):
        try:
            reply, model = _chat(messages, models)
        except RuntimeError as e:
            if not ran:
                raise Stuck(str(e))
            # The provider errors go to the HUD log, not out loud: once they
            # were read out in full - three JSON rate-limit messages - after
            # "open Spotify and play music" (25 Sep 2026).
            try:
                from . import live
                live.note(f"planner stopped: {str(e)[:150]}")
                with open(PLAN_LOG, "a", encoding="utf-8") as f:
                    f.write(f"{time.strftime('%H:%M:%S')} PLANNER STOPPED: {str(e)[:400]}\n")
            except Exception:
                pass
            return "I got partway through that, but the online planner is busy right now. Try the rest in a minute."
        step = _parse(reply)
        messages.append({"role": "assistant", "content": reply})
        if step is None:
            messages.append({"role": "user", "content":
                             'Reply with one JSON object only: {"tool": ..., "args": {...}} or {"done": ...}.'})
            continue
        if "done" in step:
            # gpt-oss-120b answered "check my battery and tell me tomorrow's
            # classes" with {"done": "87%, Physics at 9..."} - no tool run,
            # every word invented (25 Sep 2026). Nothing is "done" before a
            # tool has run; say so once, and if it insists, hand over.
            if not ran:
                if pushed:
                    raise Stuck("planner answered without running any tool")
                pushed = True
                messages.append({"role": "user", "content":
                                 "You have not run any tool, so you cannot know that - it would "
                                 "be made up. Call the tool that finds it out."})
                continue
            said = str(step["done"]).strip()
            # "I've opened the page. Let me read the form to fill in..." came
            # back as the final answer mid-booking (25 Sep 2026): a plan, not
            # a result. Send it back to do what it just said.
            if _MORE_TO_DO.search(said) and not nudged_done:
                nudged_done = True
                messages.append({"role": "user", "content":
                                 "You said what you will do next, but stopped. Do it now with a tool call; "
                                 "only reply with done when nothing is left to do."})
                continue
            return said or "Done."
        name = str(step.get("tool", ""))
        # Models disagree on the shape: {"tool", "args"}, "arguments",
        # "parameters", or the arguments laid out beside "tool" - which is
        # how a fallback model sent web_search with no query at all.
        for k in ("args", "arguments", "parameters"):
            if k in step:
                args = step[k] if isinstance(step[k], dict) else {}
                break
        else:
            args = {k: v for k, v in step.items() if k not in ("tool", "done", "thought", "reason", "why")}
        t = registry.get(name)
        if t is None or not isinstance(args, dict):
            result = f"ERROR: there is no tool '{name}'. Use one from the list."
        else:
            # A repeat is only a loop if it also saw the same thing: reading a
            # page after each click is how a form gets filled in, and three
            # reads were stopping a Zomato booking half-way (25 Sep 2026).
            key = name + json.dumps(args, sort_keys=True)
            if name.endswith("_read"):
                key += str(hash(t.run(**args)[:600]))
            seen[key] = seen.get(key, 0) + 1
            if seen[key] > 2:
                return (f"I was going round in circles on {name}, so I stopped. "
                        f"Done so far: {', '.join(ran) or 'nothing'}.")
            result = t.run(**args)
            ran.append(name)
            try:                             # step log, to see why a task went wrong
                with open(PLAN_LOG, "a", encoding="utf-8") as f:
                    f.write(f"{time.strftime('%H:%M:%S')} {name} {json.dumps(args)[:200]}\n"
                            f"    -> {result[:1500]}\n")
            except OSError:
                pass
            if on_step:
                on_step(name, args, result)
        # Older results shrink to a line: the whole history is resent every
        # step, and on a free tier it is what runs the quota out.
        for m in messages:
            if m["role"] == "user" and m["content"].startswith("Result of ") and len(m["content"]) > 400:
                m["content"] = m["content"][:350] + "\n[...older result shortened]"
        messages.append({"role": "user",
                         "content": f"Result of {name}:\n{result[:result_chars]}\n\nNext step?"})
    return f"I stopped after {max_steps} steps. Done so far: {', '.join(ran)}."

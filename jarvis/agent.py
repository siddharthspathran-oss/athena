"""The agent loop: prompt -> model -> tool calls -> results -> answer."""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Callable

from . import planner, registry
from .config import cfg
from .llm import LLM
from .memory import Memory

# Nothing in here may change from one turn to the next. qwen2.5's template
# renders the system message first and the ~4k tokens of tool definitions
# straight after it, and Ollama reuses its KV cache only for an identical
# prefix. The time used to sit on the second line, so each new minute threw
# the whole prefix away: measured 3.3 s (2.8 s of prompt eval over 5.5k
# tokens) for the first question after the minute rolled, against 0.4 s warm.
# The clock now rides on each user message instead (Memory.build).
SYSTEM_PROMPT = """You are {name}, a local AI assistant running on the user's own Windows PC.
Each user message ends with the date and time it was sent, in square brackets.
Use the latest one when asked the time, date or day; do not mention it otherwise.

You have tools that genuinely control this computer: launching apps, reading and
organising files, searching the web, reading the user's documents, and scheduling
tasks. Use them. Do not claim you cannot do something that a tool covers, and do
not describe what a tool would do instead of calling it.

Rules:
- Prefer calling a tool over guessing. If asked about files, search for them.
- Call one tool at a time and read its result before deciding the next step.
- If one sentence asks for several things - "open Spotify and play music" -
  carry out every part. Call the next tool instead of answering, and only
  reply once nothing is left to do.
- If a tool returns an error, read the error and try a corrected call once.
  Do not retry the identical call.
- When you have the answer, reply in plain prose. Be brief: one or two sentences
  unless the user asked for detail. No preamble, no restating the question.
- If a request is genuinely ambiguous, or you did not understand it, ask one
  short clarifying question. Never repeat your previous answer to cover for
  not having understood - say you did not catch it.
- Never invent file paths, search results, or command output. Only report what a
  tool actually returned.

You cannot do anything in the real world by describing it. Sending a message,
opening an app, changing a setting, saving a fact - each happens only when the
matching tool runs and returns. Never report an action as done unless its tool
just returned successfully. Saying "sent", "opened", "done" or "remembered"
when no tool ran is the worst mistake you can make, because the user believes
you and finds out later that nothing happened.

Memory is a tool, not something you can do by thinking:
- Anything the user tells you to remember, save, note or keep MUST go through
  the `remember` tool. Knowing it for the rest of this conversation is not
  remembering it - the moment the assistant restarts it is gone.
- Never say "remembered", "saved", "noted" or "I will keep that in mind" unless
  you have just called `remember` and it returned successfully.
- To answer a question about something stored earlier, call `recall`. Do not
  rely on it being in the conversation.
- When the user states a lasting personal fact - their name, a birthday, a
  preference, a folder they use - call `remember` even if they did not say the
  word "remember".
"""


@dataclass
class Step:
    """One tool invocation, surfaced to the UI so the user can see the work."""
    tool: str
    args: dict
    result: str


VOICE_STYLE = """
Your reply is spoken aloud in a natural human voice, so talk like a capable,
friendly person rather than a machine: contractions, everyday words, no lists,
headings, markdown, emoji or symbols. Length costs the user real seconds of
waiting: usually one short sentence, under fifteen words. State the result and
stop - no restating the question, no offering further help.
Personality: calm, quick, quietly confident, with a dry sense of humour now and
then - like a trusted assistant who has known the user for years, not a help
desk. Confirm actions in a word or two: "Done." "On it." "Playing it now."
Use the user's name only occasionally. One light remark is fine when nothing is
urgent; never joke about bad news.
Good: "You're on 43 percent, running on battery." Bad: "Your battery is
currently at 43 percent and you are running on battery power. Let me know if
you need anything else."
"""


def warm(voice: bool = True, load: bool = False) -> None:
    """Put the command prompt back in Ollama's cache before anyone asks.

    Ollama keeps one prompt prefix cached. The learner, the deadline scan and
    the typed chat (a different system prompt from voice) each replace it,
    and the next spoken command then re-reads ~9k tokens of instructions and
    tool definitions: 6-10 s instead of under one (28 Sep 2026). This reads
    them now, in the background, for one token of output.

    Only while the model is already on the GPU: warming must never be what
    keeps it loaded when keep_alive or gaming mode wants the VRAM back.
    load=True is the start-up call, which loads the model on purpose.
    """
    try:
        from . import gaming
        if gaming.on() and not load:
            return      # Ollama still lists a model it just freed; warming would reload it mid-game
        llm = LLM()
        loaded = [m.model for m in llm.client.ps().models]
        if not load and not any(m.split(":")[0] == str(llm.model).split(":")[0]
                                and m.endswith(str(llm.model).split(":")[-1]) for m in loaded):
            return
        prompt = SYSTEM_PROMPT.format(name=cfg.get("assistant.name", "Athena"))
        if voice:
            prompt += VOICE_STYLE
        system = Memory(prompt).build()[0]
        llm.chat([system, {"role": "user", "content": "hi"}],
                 tools=registry.schemas(""), max_tokens=1)
    except Exception as e:                      # warming is a nicety, never an error
        print(f"[warm: {e}]")


class Agent:
    def __init__(self, on_step: Callable[[Step], None] | None = None,
                 confirm: Callable[[str, dict], bool] | None = None,
                 voice: bool = False):
        self.llm = LLM()
        prompt = SYSTEM_PROMPT.format(name=cfg.get("assistant.name", "Athena"))
        if voice:
            prompt += VOICE_STYLE
        self.memory = Memory(prompt)
        # UI hooks: show progress, and ask before destructive actions
        self._last_answer = ""
        self.on_step = on_step or (lambda s: None)
        self.confirm = confirm or (lambda name, args: True)
        # Set by the voice loop: called with each finished sentence of a model
        # reply, so Athena starts talking before the whole answer is written.
        # self.spoken is what was handed over this turn.
        self.speak: Callable[[str], None] | None = None
        self.spoken = ""
        # Set when the user cuts in ("Athena, stop" / a new command): the
        # running turn stops generating and says nothing more (26 Sep 2026:
        # an interrupted answer kept talking and held up the next command 9 s).
        self.cancelled = False

    @property
    def expects_reply(self) -> bool:
        """True when the last answer asked something - the voice loop then
        listens for the reply without the wake word."""
        from . import offer
        return bool(offer.pending()) or self._last_answer.rstrip().endswith("?")

    def _needs_confirm(self, tool: registry.Tool) -> bool:
        if not tool.destructive:
            return False
        # Some tags always ask, even with confirm_destructive off: changing the
        # RAGAZZA shop reaches real customers (27 Sep 2026: an unconfirmed
        # "set stock to 50" looped six times before the step limit stopped it).
        if set(tool.tags) & set(cfg.get("safety.always_confirm_tags", []) or []):
            return True
        return bool(cfg.get("safety.confirm_destructive", True))

    def _follow_up(self, text: str) -> tuple[str, dict] | None:
        """The last command again with one thing changed, for a follow-up
        said within three minutes of it: "and tomorrow?" after the classes,
        "what about DSA?" after the attendance, "open it" after LMS."""
        last = getattr(self, "_last_route", None)
        if not last or time.time() - last[2] > 180:
            return None
        name, args, _ = last
        if _OPEN_IT.match(text) and name == "lms_deadlines":
            from . import upes
            from .store import load_json
            open_ = sorted((e for e in load_json(upes.LMS_CACHE, {}).get("events", []) if not e["done"]),
                           key=lambda e: e["due"])
            return ("open_lms", {"what": open_[0]["name"]}) if open_ else None
        m = _FOLLOW.match(text)
        if not m:
            return None
        x = (m.group("x") or m.group("x2")).strip()
        # "and open Chrome" is a new command, not a follow-up.
        if len(x.split()) > 4 or re.match(r"(?:open|close|play|send|call|set|remind|turn|search|find|show|tell|"
                                          r"what|how|who|message|text|start|stop|add|delete|remove)\b", x, re.I):
            return None
        import inspect
        params = inspect.signature(registry.get(name).fn).parameters
        is_day = re.fullmatch(r"(?:the\s+)?(?:today|tomorrow|next(?:\s+one)?)", x, re.I)
        if is_day:
            return (name, {**args, "day": "next" if x.lower().startswith("next") else x.lower()}) if "day" in params else None
        for key in ("course", "what", "which", "place", "query", "name", "contact"):
            if key in params:
                return name, {**args, key: x}
        return None

    def _declined(self, name: str, args: dict) -> str:
        """End the turn when a confirmation is refused or still pending.

        Fed back as "Cancelled by the user" and left to carry on, a 7B model
        read the word "cancel" as a cue and called cancel_reminder("all")
        seven times in a row (27 Sep 2026). Nothing further runs this turn.
        """
        note = "Not done: waiting for the user's approval."
        self.on_step(Step(tool=name, args=args, result=note))
        self.memory.add_raw({"role": "tool", "name": name, "content": note})  # every tool call gets its result
        answer = "I haven't done that - it needs your OK first."
        self.memory.add("assistant", answer)
        return answer

    # A similarity check on short inputs used to live here, replacing repeated
    # answers with "I didn't catch that". It fired on real two-word commands
    # like "Experiment 4.1" and blocked them. _is_vague already catches the
    # genuine fragments, so the heuristic cost more than it earned.

    def ask(self, user_input: str) -> str:
        """Run one full request to completion. Returns the final text answer.

        Anything that went wrong on the way is written to data/failures.jsonl
        (jarvis/failures.py), so the fixes that matter can be found from
        what actually happened rather than guessed.
        """
        self._trouble: list[str] = []
        started = time.time()
        try:
            answer = self._ask(user_input)
        except Exception as e:
            # A crash in here used to reach the user as a blank reply - the
            # voice thread died and nothing was said (3 Oct 2026, Ollama
            # restarting mid-request). Say what happened instead.
            self._trouble.append(f"crashed: {type(e).__name__}")
            dropped = type(e).__name__ in getattr(self.llm, "_DROPPED", ())
            answer = ("I lost my connection to the model for a moment - say that again?" if dropped
                      else "Something went wrong on my side with that - try once more?")
            self._last_answer = answer
        try:
            self._log_trouble(user_input, answer, time.time() - started)
        except Exception:
            pass
        return answer

    _FAIL_ANSWERS = (
        ("I couldn't do that - nothing ran", "claimed an action without a tool"),
        ("I couldn't finish that in one go", "ran out of steps"),
        ("I could not do that", "tool failed"),
        ("(no response)", "empty answer"),
        ("going round in circles", "planner looped"),
        ("I stopped after", "planner ran out of steps"),
        ("the online planner is busy", "planner unavailable"),
    )

    def _log_trouble(self, said: str, answer: str, took: float) -> None:
        from . import failures

        kinds = list(self._trouble)
        kinds += [k for text, k in self._FAIL_ANSWERS if text in (answer or "")]
        msgs = self.memory.messages
        start = max((i for i, m in enumerate(msgs)
                     if m.get("role") == "user" and m.get("content") == said), default=len(msgs))
        for m in msgs[start:]:
            body = str(m.get("content") or "")
            if m.get("role") == "tool" and body.startswith("ERROR"):
                kinds.append(("tool timeout: " if "did not finish within" in body else "tool error: ")
                             + str(m.get("name")))
        slow = float(cfg.get("failures.slow_seconds", 20))
        if took > slow and not any(n in str(msgs[start:]) for n in ("explain", "index_documents")):
            kinds.append(f"slow ({took:.0f}s)")
        if kinds and answer != "(interrupted)":
            failures.log(", ".join(dict.fromkeys(kinds)), said, answer)

    def _ask(self, user_input: str) -> str:
        from . import alarm, gaming, offer
        # An alarm ringing: almost anything short you say means "stop it".
        if alarm.ringing():
            sn = re.match(r"^\W*(?:(?:athena|jarvis)\W*)?snooze(?:\s+(?:it|the\s+alarm))?(?:\s+for\s+(\d+)\s*min\w*)?\W*$",
                          user_input, re.I)
            if sn:
                return alarm.snooze(int(sn.group(1) or 10))
            if re.match(r"^\W*(?:(?:athena|jarvis)\W*)?(?:stop|enough|ok(?:ay)?|i'?m\s+up|yes|thanks?|dismiss|"
                        r"silence|quiet|shut\s+up|turn\s+(?:it|that|the\s+alarm)\s+off|alarm\s+off|"
                        r"stop\s+(?:it|the\s+alarm|ringing)|got\s+it|done)?\W*(?:(?:the\s+)?alarm)?\W*$",
                        user_input, re.I):
                return alarm.stop()
        gaming.set_on(False)     # you asked something: the model may load again
        self.spoken = ""
        self.cancelled = False

        # An answer to what Athena just offered ("which one?", "want me to
        # open it?"): "yes", "two", "all", "no". Checked before anything else,
        # because every one of those is a fragment to the rest of this method.
        picked = offer.resolve(user_input)
        if picked is not None:
            offer.clear()
            self.memory.add("user", user_input)
            if picked == "no":
                answer = "Okay, I'll leave it."
            else:
                said = []
                for c in picked:
                    t = registry.get(c.tool)
                    result = t.run(**c.args) if t else f"ERROR: no tool {c.tool}"
                    self.on_step(Step(tool=c.tool, args=c.args, result=result))
                    self.memory.add_raw({"role": "tool", "name": c.tool, "content": _truncate(result)})
                    said.append(re.split(r"(?<=[.!?])\s", result.removeprefix("ERROR:").strip(), 1)[0])
                answer = " ".join(said)[:400] if len(picked) == 1 else (
                    f"Opened {len(picked)}: " + ", ".join(c.label for c in picked) + ".")
            self._last_answer = answer
            self.memory.add("assistant", answer)
            return answer
        offer.clear()              # anything else is a new request
        # Half-caught speech reaches here as a fragment. Answering it from the
        # previous topic looks like Athena ignoring you, so ask instead - and
        # do it without spending a model call.
        if _is_greeting(user_input):
            # Answered here rather than by the model: it is instant, and it
            # does not drag the last topic into a fresh hello.
            answer = _pick_line("hello")
            self.memory.add("user", user_input)
            self.memory.add("assistant", answer)
            self._last_answer = answer
            return answer

        if _UNLOCK.match(user_input.strip()):
            # No tool can do this - the sign-in screen is a separate desktop
            # that ordinary programs cannot type into - so the model's best
            # effort was "I could not do that - no tool ran", which reads as
            # a bug rather than a limit.
            answer = ("I can't unlock Windows - the sign-in screen is protected "
                      "from programs like me. Use your PIN, and I'll be right here.")
            self.memory.add("user", user_input)
            self.memory.add("assistant", answer)
            self._last_answer = answer
            return answer

        # A command word with nothing after it ("open" cut off mid-sentence):
        # ask, instead of letting the model guess from earlier chat - it
        # launched OpenGL Switcher, then tried to open "RAGAZZA" (27 Sep 2026).
        bare = re.fullmatch(r"\W*(open|close|launch|start|play|call|message|text|send|search|find)\W*",
                            user_input, re.I)
        if bare:
            answer = f"{bare.group(1).capitalize()} what?"
            self._last_answer = answer
            return answer

        # "yes" / "no" right after Athena asked something is an answer, not a
        # fragment: the model has the question in front of it.
        offered = _OFFERED.search(self._last_answer or "")
        replying = ((self._last_answer.rstrip().endswith("?") or bool(offered))
                    and bool(offer.YES.match(user_input) or offer.NO.match(user_input)))
        # A bare "please" after "let me know if you want to play it" was read
        # as "pause" (3 Oct 2026). Spell the yes out with what was offered.
        if offered and offer.YES.match(user_input):
            user_input = f"Yes, {offered.group(1).strip()}."
        if _is_vague(user_input) and not replying:
            self._trouble.append("not understood")
            answer = _pick_line("missed")
            self._last_answer = answer
            return answer

        self.memory.add("user", user_input)

        # Reminders were reaching the model and coming back as invented tool
        # names - set_alarm, setReminder - or "no tool available". The phrasing
        # is regular, so it is parsed here and the tool called outright. Only
        # unmistakable requests are caught; anything else falls through.
        # A form Athena filled is waiting on its final button. Only the
        # user's own words press it - the model has no tool that can.
        from .tools import apps, browser, places
        held = (places if places.has_pending() else apps if apps.has_pending()
                else browser if browser.has_pending() else None)
        if held is not None:
            answer = None
            if _SUBMIT_YES.match(user_input.strip()):
                answer = held.submit_pending()
            elif _SUBMIT_NO.match(user_input.strip()):
                answer = held.cancel_pending()
            if answer is not None:
                self._last_answer = answer
                self.memory.add("assistant", answer)
                return answer

        # A multi-step request must not be half-caught by a single-action
        # router: "find the scheduler pdf and send it to Rahul" would parse
        # as a WhatsApp message to Rahul reading "find the scheduler pdf".
        multi = cfg.get("planner.enabled", True) and planner.is_multistep(user_input)
        # "and tomorrow?" / "what about DSA?" / "open it" right after a command
        # carries on from it, instead of starting from nothing.
        follow = self._follow_up(user_input)
        if follow is not None:
            multi = False
        direct = follow or (None if multi else route(user_input))
        # "open WhatsApp and message maa ..." is one job - the message tool opens
        # WhatsApp itself. Sent to the planner it re-ran whatsapp_message until
        # it timed out: 101 s and nothing sent (3 Oct 2026).
        lead = _OPEN_WHATSAPP.match(user_input)
        if multi and lead:
            rest = route(user_input[lead.end():])
            if rest is not None and rest[0] in ("whatsapp_message", "whatsapp_call"):
                multi, direct = False, rest
        if direct is not None and registry.get(direct[0]) is None:
            # A router naming a tool that is not registered crashed "brief
            # me" outright (25 Sep 2026, a misplaced @tool). Let the model
            # have it instead of saying nothing.
            direct = None
        if direct is not None:
            name, args = direct
            tool = registry.get(name)
            if self._needs_confirm(tool) and not self.confirm(name, args):
                answer = "Cancelled."
            else:
                result = tool.run(**args)
                self.on_step(Step(tool=name, args=args, result=result))
                self.memory.add_raw({"role": "tool", "name": name, "content": result})
                if result.startswith("ERROR") and (name in _FILE_TOOLS or "store" in tool.tags):
                    # "is protected" or "outside the allowed folders" is the
                    # answer here, not a failure to hide behind a stock line.
                    # Same for the store: "Which one? ..." is the useful reply.
                    answer = result.removeprefix("ERROR:").strip()
                else:
                    answer = result if not result.startswith("ERROR") else (
                        "I could not do that.")
                self._last_route = (name, args, time.time())
            self._last_answer = answer
            self.memory.add("assistant", answer)
            return answer
        # Several actions in one request: the local model loses the thread
        # after two or three tool calls, so the planner takes these, one step
        # at a time on a bigger hosted model. If no hosted model answers, the
        # local loop below still gets its try.
        # Two quick commands in one sentence need neither model. Tried for any
        # sentence with a joiner, not only what is_multistep calls multi-step:
        # "what's my battery and list my reminders" has one action verb by its
        # count and went to the model (28 Sep 2026).
        simple = split_simple(user_input, strict=not multi) if (multi or direct is None) else None
        if simple:
            said = []
            if all(n in planner.PARALLEL_SAFE for n, _ in simple):
                # Only lookups ("battery and my reminders"): all at once.
                results = planner.run_parallel(simple)
            else:
                results, prev = [], ""
                for name, args in simple:
                    if prev == "open_app" and name in ("music", "play_song", "play_track", "play_my_taste"):
                        time.sleep(3)      # a player that has just launched ignores the play key
                    prev = name
                    results.append(registry.get(name).run(**args))
            for (name, args), result in zip(simple, results):
                self.on_step(Step(tool=name, args=args, result=result))
                self.memory.add_raw({"role": "tool", "name": name, "content": _truncate(result)})
                # An action is confirmed in its first sentence; a lookup's
                # answer can need the second ("Battery 81%. Charging.").
                keep = 2 if name in planner.PARALLEL_SAFE else 1
                parts = re.split(r"(?<=[.!?])\s", result.removeprefix("ERROR:").strip(), keep)
                first = " ".join(parts[:keep])
                said.append(("Couldn't: " + parts[0]) if result.startswith("ERROR") else first)
            answer = " ".join(said)[:400]
            self._last_answer = answer
            self.memory.add("assistant", answer)
            return answer

        if multi:
            self._acknowledge()
            answer = self._plan(user_input)
            if answer is not None:
                return answer

        shown = registry.select(user_input)
        tools = [registry.get(n).schema for n in shown]
        # The model is shown only the tools this request seems to need
        # (registry.select). If that guess was wrong it says "I can't", and
        # gets one more try with the tools that look related to what was
        # asked - not all of them, which would overflow the context again.
        widened = False
        max_steps = int(cfg.get("llm.max_steps", 8))
        used_tools = False
        nudged = False

        done_calls: dict[str, str] = {}      # call -> its result, to stop repeat loops
        for _ in range(max_steps):
            if self.speak is None:
                msg = self.llm.chat(self.memory.build(), tools=tools)
            else:
                blocked = [False]

                def out(sentence: str, used=used_tools, wide=widened) -> None:
                    # The same guards as below, applied before the sentence is
                    # said: an unbacked "Screen locked." is never spoken, nor
                    # an "I can't" that is about to be retried with every tool.
                    if self.cancelled:
                        return
                    if blocked[0] or (not used and (
                            _claims_action(sentence)
                            or (not wide and not self.spoken and _CANT.search(sentence)))):
                        blocked[0] = True
                        return
                    self.spoken += (" " if self.spoken else "") + sentence
                    self.speak(sentence)
                # Spoken replies are meant to be a dozen words; 600 tokens of
                # runaway is ten seconds of generating nobody wants to hear.
                msg = self.llm.chat_stream(self.memory.build(), tools, out,
                                           stop=lambda: self.cancelled,
                                           max_tokens=cfg.get("llm.voice_max_tokens", 300))
                if self.cancelled:
                    return "(interrupted)"

            calls = getattr(msg, "tool_calls", None)
            if not calls:
                answer = (msg.content or "").strip()

                # The model sometimes writes the call out as text rather than
                # calling it. Run it instead of reading JSON aloud.
                written = _parse_text_tool_call(answer)
                if written is not None:
                    name, args = written
                    t = registry.get(name)
                    if self._needs_confirm(t) and not self.confirm(name, args):
                        return self._declined(name, args)
                    result = _truncate(t.run(**args))
                    used_tools = True
                    self.on_step(Step(tool=name, args=args, result=result))
                    self.memory.add_raw({"role": "tool", "name": name, "content": result})
                    continue

                if not used_tools and not widened and not self.spoken and _CANT.search(answer):
                    self._trouble.append("model said it can't - retried with more tools")
                    widened = True
                    extra = registry.related(user_input, shown)
                    if extra:
                        shown = shown + extra
                        tools = [registry.get(n).schema for n in shown]
                        continue

                # A small model will happily reply "Screen locked." without
                # having locked anything. Wording in the system prompt does not
                # reliably stop it, so catch the claim and demand the tool.
                if not used_tools and _claims_action(answer):
                    if not nudged:
                        nudged = True
                        self.memory.add_raw({
                            "role": "user",
                            "content": (
                                "You said that was done, but you did not call any tool, "
                                "so nothing actually happened. Call the correct tool now. "
                                "If no tool can do it, say plainly that you cannot."
                            ),
                        })
                        continue
                    # Nudged once and still claiming. Never pass an unverified
                    # claim on to the user - they will believe it. The bigger
                    # planner gets a go first.
                    rescued = self._rescue(user_input, "claimed an action without a tool")
                    if rescued is not None:
                        return rescued
                    answer = ("I couldn't do that - nothing ran. "
                              "Try saying it more directly, like 'open Outlook'.")

                # Nothing at all - typically after being told to call the tool
                # it skipped. "(no response)" left the user with silence;
                # the planner gets a go, and failing that she says so.
                if not answer:
                    if not used_tools:
                        rescued = self._rescue(user_input, "empty answer")
                        if rescued is not None:
                            return rescued
                        answer = "I couldn't work that one out. Could you ask it another way?"
                    else:
                        answer = "Done."

                self._last_answer = answer

                self.memory.add("assistant", answer)
                return answer

            used_tools = True

            # Record the assistant's tool-call turn verbatim so the model can
            # see its own request alongside the result.
            self.memory.add_raw({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {"function": {"name": c.function.name, "arguments": c.function.arguments}}
                    for c in calls
                ],
            })

            for call in calls:
                name = call.function.name
                args = call.function.arguments or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}

                t = registry.get(name)
                key = name + json.dumps(args, sort_keys=True, default=str)
                if key in done_calls:
                    # The same call again: "what can you do?" ran list_skills
                    # eight times and hit the step limit (27 Sep 2026). Not
                    # run twice - the model is told to answer; a third try
                    # ends the turn with the result it already has.
                    if done_calls[key].startswith("\x00"):
                        rescued = self._rescue(user_input, "went round in circles")
                        if rescued is not None:
                            return rescued
                        answer = done_calls[key][1:][:600]
                        self._last_answer = answer
                        self.memory.add("assistant", answer)
                        return answer
                    done_calls[key] = "\x00" + done_calls[key]
                    self.memory.add_raw({"role": "tool", "name": name, "content":
                        "Already done - the result is above. Do not call it again; answer the user now."})
                    continue
                if t is None:
                    result = f"ERROR: no such tool '{name}'. Available: {', '.join(registry.REGISTRY)}"
                elif self._needs_confirm(t) and not self.confirm(name, args):
                    return self._declined(name, args)
                else:
                    result = t.run(**args)

                result = _truncate(result)
                done_calls[key] = result
                self.on_step(Step(tool=name, args=args, result=result))
                self.memory.add_raw({"role": "tool", "name": name, "content": result})

        rescued = self._rescue(user_input, "ran out of steps")
        if rescued is not None:
            return rescued
        final = "I couldn't finish that in one go. Try it in smaller pieces."
        self.memory.add("assistant", final)
        return final

    # ---------- handing over to the planner ----------

    def _acknowledge(self, line: str = "On it.") -> None:
        """Say something before a task that takes a while. The planner can
        take 10-20 seconds, and silence that long sounds like nothing heard.
        Spoken, not added to self.spoken, so the real answer still follows."""
        if self.speak is not None and not self.cancelled:
            try:
                self.speak(line)
            except Exception:
                pass

    def _plan(self, request: str) -> str | None:
        def step(name, args, result):
            self.on_step(Step(tool=name, args=args, result=result))
            self.memory.add_raw({"role": "tool", "name": name, "content": _truncate(result)})
        try:
            answer = planner.run(request, on_step=step)
        except planner.Stuck:
            return None
        except Exception as e:
            print(f"[planner failed: {e}]")
            return None
        self._last_answer = answer
        self.memory.add("assistant", answer)
        return answer

    def _rescue(self, request: str, why: str) -> str | None:
        """The local model got stuck; let the planner finish the job.

        It is told what already ran, with the results, so nothing is done
        twice - a message sent once must not be sent again.
        """
        if (self.cancelled or not cfg.get("planner.enabled", True)
                or not cfg.get("planner.rescue", True)):
            return None
        msgs = self.memory.messages
        start = max((i for i, m in enumerate(msgs)
                     if m.get("role") == "user" and m.get("content") == request), default=len(msgs))
        done = [f"- {m.get('name')}: {str(m.get('content'))[:300]}"
                for m in msgs[start:] if m.get("role") == "tool"]
        brief = request
        if done:
            brief += ("\n\nAlready done this turn (do NOT repeat these; use their results):\n"
                      + "\n".join(done))
        print(f"   [local model {why} - handing to the planner]")
        self._trouble.append(f"rescued by the planner: local model {why}")
        self._acknowledge("One moment.")
        return self._plan(brief)

    def chat_only(self, user_input: str):
        """Streaming plain chat with no tools - used for obvious conversation."""
        self.memory.add("user", user_input)
        parts: list[str] = []
        for piece in self.llm.stream(self.memory.build()):
            parts.append(piece)
            yield piece
        self.memory.add("assistant", "".join(parts))


# A person does not greet you with the same sentence every time. A few
# ways of saying the fixed lines, picked at random but never the same one
# twice in a row.
_LINES = {
    "hello": ["Hey{you}. What can I do?", "Hi{you} - what do you need?",
              "Hey{you}, I'm here. What's up?", "Hello{you}. What can I do for you?"],
    "missed": ["Sorry, I didn't catch that.", "Say that again?",
               "I missed that - what was it?", "Sorry, could you repeat that?"],
}
_last_line: dict[str, str] = {}


def _pick_line(kind: str) -> str:
    import random

    who = str(cfg.get("assistant.user_name", "") or "").strip()
    you = "" if not who or who.lower() in ("operator", "user") else f" {who}"
    options = [o for o in _LINES[kind] if o != _last_line.get(kind)] or _LINES[kind]
    line = random.choice(options)
    _last_line[kind] = line
    return line.format(you=you)


# Past-tense claims that something was carried out. Checked only when the turn
# used no tools at all, so a genuine "volume set to 50%" after a real call is
# never affected.
# Matched on the verb stem with any ending, because the model dodges a fixed
# list by changing tense: blocked "closed", it answers "closing WhatsApp".
_ACTION_CLAIMS = re.compile(
    r"\b("
    # Stems, so "clos" covers close/closed/closing. Irregular past tenses
    # (sent, wrote) need spelling out - no suffix rule produces them.
    r"lock|unlock|send|sent|open|launch|start|clos|quit|exit|"
    r"delet|remov|mov|renam|creat|writ|wrote|sav|download|"
    r"remember|note|stor|schedul|sett|set|turn|mut|unmut|"
    r"play|paus|skipp|skip|resum|"
    # "shutting" doubles its t, which no suffix below produces - "Shutting
    # down the PC." went through unchecked on 24 Sep 2026 with nothing run.
    r"shutting|shut|restart|reboot|organi[sz]|cop|typ|clear|cancel"
    r")(s|ed|ing|e|es|d)?\b",
    re.IGNORECASE,
)

# "I can't do that" with no tool used: on a narrowed tool list the right
# tool may simply not have been shown, so the request is retried with all.
_CANT = re.compile(
    r"\b(?:cannot|can't|can not|could not|couldn't|unable to|not able to|no tool|"
    r"don't have (?:a |the |any )?(?:tool|way|ability|access)|"
    r"do not have (?:a |the |any )?(?:tool|way|ability|access)|not possible for me)\b",
    re.IGNORECASE,
)

# Phrases that report a fact or a refusal rather than claiming an action.
_NOT_A_CLAIM = re.compile(
    r"\b(cannot|can't|could not|couldn't|unable|no tool|don't have|"
    r"do not have|not saved|no contact|already|is at|are at|"
    r"nothing is|nothing to|no music|no player|not playing|"
    r"would you|shall i|should i|do you want)\b",
    re.IGNORECASE,
)


def _parse_text_tool_call(content: str) -> tuple[str, dict] | None:
    """Recover a tool call the model wrote as text instead of calling.

    Small models intermittently emit `open_app {"name": "outlook"}` in the
    message body rather than through the tool-call channel. Left alone, that
    text gets read aloud and nothing happens. Recognised forms:

        open_app {"name": "outlook"}
        {"name": "open_app", "arguments": {"name": "outlook"}}
        ```json { ... } ```
        open_app(name="outlook")
    """
    if not content:
        return None
    text = content.strip()
    text = re.sub(r"^```(?:json|tool_code)?\s*|\s*```$", "", text, flags=re.I).strip()

    # {"name": "tool", "arguments": {...}}
    if text.startswith("{"):
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            obj = None
        if isinstance(obj, dict):
            name = obj.get("name") or obj.get("tool") or obj.get("function")
            args = obj.get("arguments") or obj.get("parameters") or obj.get("args") or {}
            if isinstance(name, str) and registry.get(name) and isinstance(args, dict):
                return name, args

    # A bare tool name, no arguments: "get_volume"
    bare = re.fullmatch(r"([a-z_][a-z0-9_]*)\s*(?:\(\s*\))?\.?", text, re.I)
    if bare:
        t = registry.get(bare.group(1))
        if t and not t.schema["function"]["parameters"]["required"]:
            return bare.group(1), {}

    # toolname {"a": 1}
    m = re.match(r"^([a-z_][a-z0-9_]*)\s*(\{.*\})\s*$", text, re.S | re.I)
    if m and registry.get(m.group(1)):
        try:
            args = json.loads(m.group(2))
        except json.JSONDecodeError:
            return None
        if isinstance(args, dict):
            return m.group(1), args

    # toolname followed by a bare argument, no quotes or braces:
    #   open_file C:/some/path/Experiment 4.1
    m = re.match(r"^([a-z_][a-z0-9_]*)[ \t]+(\S.*)$", text, re.S | re.I)
    if m:
        t = registry.get(m.group(1))
        if t:
            required = t.schema["function"]["parameters"]["required"]
            if len(required) == 1:
                value = m.group(2).strip().strip("\"'").replace("\\ ", " ")
                return m.group(1), {required[0]: value}

    # toolname(a="b")
    m = re.match(r"^([a-z_][a-z0-9_]*)\s*\((.*)\)\s*$", text, re.S | re.I)
    if m and registry.get(m.group(1)):
        args: dict = {}
        for k, v in re.findall(r"([a-z_][a-z0-9_]*)\s*=\s*(\"[^\"]*\"|'[^']*'|[^,\s)]+)",
                               m.group(2), re.I):
            args[k] = v.strip("\"'")
        return m.group(1), args

    return None


# Fragments that carry no instruction. Speech produces these constantly when
# it catches the tail of a sentence or a noise.
_VAGUE = re.compile(
    r"^\W*("
    r"what|what'?s? (that|this|it|up)|huh|eh|sorry|pardon|come again|"
    r"say (that )?again|again|wait|hold on|hmm+|um+|uh+|ah+|oh|ok(ay)?|"
    r"yes|no|yeah|nope|and|so|then|now|this|that|it|"
    r"(now|and|so|but|well) (this|that|it|then)"
    r")\W*$",
    re.IGNORECASE,
)


_GREETING = re.compile(
    r"^\W*(?:hi|hey|hello|yo|hiya|good\s+(?:morning|afternoon|evening)|"
    r"what'?s?\s*up|sup)"
    r"(?:\s+(?:athena|jarvis|there|buddy|mate))?\W*$", re.I)


# "Athena, unlock" / "unlock my laptop" / "log me in". Not "unlock my phone
# screen rotation" or anything naming another thing to unlock.
# What the last reply offered to do: "let me know if you want to play it or
# add it to your queue" -> "play it". The first option only, up to " or ".
_FOLLOW = re.compile(r"^\W*(?:(?:athena|jarvis|ok|okay|so|hmm|and)\W+)*(?:and\s+)?(?:what\s+about|how\s+about|what\s+of|"
                     r"and\s+for|same\s+for|and)\s+(?P<x>[^.?!]+?)\W*$|"
                     r"^\W*(?:(?:athena|jarvis|and|so)\W+)*(?P<x2>today|tomorrow)\W*$", re.I)
_OPEN_IT = re.compile(r"^\W*(?:(?:athena|jarvis|ok|okay|then|so|yes)\W+)*(?:open|show|start)\s+"
                      r"(?:it|that|that\s+one|the\s+first\s+one)(?:\s+please)?\W*$", re.I)

_OFFERED = re.compile(
    r"\b(?:let me know if you(?:'d)? (?:want|like) (?:me )?to|(?:do )?you want me to|want me to|"
    r"shall i|should i|would you like me to|i can)\s+(.+?)(?:\s+or\s+.*)?[.?!]?\s*$", re.I)

_OPEN_WHATSAPP = re.compile(
    r"^\W*(?:(?:athena|jarvis)\W+)?(?:please\s+)?open\s+(?:the\s+)?whats\s?app(?:\s+app)?\s*(?:,\s*)?(?:and|then|&)?\s+", re.I)

_UNLOCK = re.compile(
    r"^\W*(?:(?:athena|jarvis|hey|ok|okay|please)\W+)*(?:(?:can|could|would|will)\s+you\s+)?"
    r"(?:please\s+)?(?:unlock|log\s*me\s+in|sign\s+me\s+in|log\s+in|sign\s+in)"
    r"(?:\s+(?:the\s+|my\s+|this\s+)?(?:pc|computer|laptop|screen|lock\s*screen|"
    r"windows|system|device))?(?:\s+(?:now|please))*\W*$", re.I)


def _is_vague(text: str) -> bool:
    """True if the input is a fragment with nothing actionable in it."""
    return bool(_VAGUE.match(text.strip()))


def _is_greeting(text: str) -> bool:
    """A hello is not a half-heard fragment.

    "hi", "hello" and "hey" used to sit in the vague list, which made sense
    when the only ear was a microphone - a stray "hi" is usually someone
    talking in the room. Typed into the chat window it is a person saying
    hello, and answering "Sorry, I didn't catch that" reads as broken.
    """
    return bool(_GREETING.match(text.strip()))


# Opening and closing apps is the most-used command and the one speech mangles
# most, because app names are proper nouns with common-English neighbours. The
# model was picking open_file for "and Claude" and inventing apps for "Lord
# App". Resolving the name against what is actually installed is both more
# accurate than the model and two round-trips faster.
_CLOSE_VERBS = {"close", "quit", "exit", "kill", "stop", "shut", "shut down"}

_APP_CMD = re.compile(
    r"^\W*(?:(?:athena|jarvis|and|um|uh|so|now|then|just|please|ok|okay|hey)\W+)*"
    r"(?:(?P<verb>open|launch|start|run|close|quit|exit|kill|"
    r"shut\s*down|shut|stop)\s+)?"
    r"(?:up\s+)?(?:the\s+|my\s+)?"
    r"(?P<name>[a-z0-9][a-z0-9 .+&'_-]{0,40}?)"
    r"(?:\s+(?:app|application|program|window))?"
    r"\W*$",
    re.I,
)

# Without a verb the sentence has to look like a name, not a question.
_QUESTION_WORD = re.compile(r"^(what|who|when|where|why|how|is|are|do|does|"
                            r"can|could|should|would|will|tell|give|show me)\b", re.I)


def _direct_app(text: str) -> tuple[str, dict] | None:
    """Turn 'open X' / 'close X' into a tool call when X is a real app.

    Falls through to the model whenever the name does not resolve, so
    "open experiment 4.1" still reaches the file tools.
    """
    m = _APP_CMD.match(text.strip())
    if not m:
        return None
    name = (m.group("name") or "").strip()
    verb = (m.group("verb") or "").strip().lower()
    if not name:
        return None
    # A lone verb or filler is not an app: a cut-off "open" launched OpenGL
    # Switcher, the nearest name starting with "open" (27 Sep 2026).
    if name.lower().strip(" .,!?") in ("open", "close", "launch", "start", "quit", "exit", "run",
                                       "play", "stop", "it", "the", "this", "that", "a", "an", "app"):
        return None

    # A bare app name is a fair instruction - "Spotify" means open it - but
    # only when it is short and not a question.
    if not verb:
        if len(name.split()) > 3 or _QUESTION_WORD.match(name):
            return None

    # _match_app falls back to fuzzy matching, which is right when the model
    # has already decided this is an app but far too loose here: it matched
    # "lock my screen" to an installed program. Routing away from the model
    # needs certainty, so require the resolved name to really correspond.
    try:
        from .tools.system import APP_ALIASES, _match_app, _user_aliases

        hit = _match_app(name)
        if hit is None:
            return None
        low = name.lower()
        want = (_user_aliases().get(low) or APP_ALIASES.get(low) or low).lower()
        found = hit[0].lower()
        if not (found == want or found.startswith(want) or want in found):
            return None
        # "open scheduler" launched Task Scheduler on the word inside its
        # name while scheduler.pdf sat in Downloads. A file named exactly
        # that beats an app that merely contains the word.
        if (verb not in _CLOSE_VERBS and found != want
                and not found.startswith(want) and _exact_personal_file(name)):
            return None
    except Exception:
        return None

    tool = "close_app" if verb in _CLOSE_VERBS else "open_app"
    return tool, {"name": name}


# ---------------------------------------------------------------- files
# "open my downloads" reached the model and came back "(no response)";
# "open documents folder" became open_app("documents folder"), failed, and
# was then reported as done after File Explorer was merely brought forward;
# "open resume.docx" made the model guess C:/Users/Siddharth/resume.docx and
# then call `remember`. The phrasings are regular and the file index answers
# in milliseconds, so these are routed here. Anything that does not clearly
# name a folder or a file still falls through - "open youtube" must reach
# the model - and apps are tried before files.
_FILE_TOOLS = {"open_file", "open_folder", "reveal_path"}

_LEAD = (r"^\W*(?:(?:athena|jarvis|hey|ok|okay|please|now|just|and|um|uh|so|then)\W+)*"
         r"(?:(?:can|could|would|will)\s+you\s+)?(?:please\s+)?")

_EXPLORER_CMD = re.compile(
    _LEAD + r"(?:open|launch|start|show|bring\s+up|pull\s+up)\s+(?:up\s+)?"
    r"(?:the\s+|my\s+)?(?:file\s+explorer|windows\s+explorer|explorer|"
    r"file\s+manager|files\s+app|my\s+files|this\s+pc)"
    r"(?:\s+(?:app|window))?(?:\s+please)?\W*$", re.I)

_FOLDER_CMD = re.compile(
    _LEAD + r"(?P<verb>open|show(?:\s+me)?|go\s+to|take\s+me\s+to|browse|"
    r"bring\s+up|pull\s+up)\s+(?:up\s+)?(?P<my>the\s+|my\s+)?"
    r"(?P<name>.+?)(?:\s+(?P<suffix>folder|directory))?"
    r"(?P<exp>\s+in\s+(?:the\s+)?(?:file\s+)?explorer)?(?:\s+please)?\W*$", re.I)

# Known folders a bare "open X" may mean. "music" is left out on purpose -
# "open music" is Spotify here (APP_ALIASES) - and "desktop" alone only with
# open/go to, since "show desktop" is the minimise-everything gesture.
_BARE_FOLDERS = {"downloads", "download", "documents", "my documents",
                 "pictures", "onedrive", "one drive", "desktop", "videos"}

_REVEAL_CMD = re.compile(
    _LEAD + r"(?:"
    r"(?:show|find|locate|reveal|open)\s+(?:me\s+)?(?:where\s+)?(?:the\s+|my\s+)?"
    r"(?:file\s+|folder\s+)?(?P<a>.+?)\s+(?:is\s+)?in\s+(?:the\s+)?(?:file\s+)?explorer"
    r"|(?:where\s+is|where'?s|show\s+me\s+where)\s+(?:the\s+|my\s+)?"
    r"(?P<file>file\s+|folder\s+)?(?P<b>.+?)(?:\s+is)?"
    r"(?:\s+(?:saved|stored|located|kept))?"
    r")\W*$", re.I)

_OPEN_CMD = re.compile(
    _LEAD + r"(?:open|view|display|show\s+me|pull\s+up|bring\s+up)\s+(?:up\s+)?"
    r"(?P<q>.+?)(?:\s+(?:for\s+me|please))?\W*$", re.I)

# Words said about a file, never about an app or a website.
_FILE_HINT = re.compile(
    r"\b(?:file|document|pdf|docx?|pptx?|xlsx?|csv|txt|power\s*point|"
    r"presentation|slides|spreadsheet|word\s+(?:file|doc)|excel\s+file|"
    r"screenshot|image|photo|picture|video|notes|report|assignment|"
    r"worksheet|syllabus)\b|\.\w{2,5}$|\s+dot\s+\w{2,5}$", re.I)

_NOT_A_FILE = re.compile(
    r"^(?:a\s+)?(?:new|blank)\b|https?:|www\.|\.(?:com|org|net|in|io)\b|"
    r"\s(?:and|then)\s+(?:open|play|search|send|call|close|start|tell)\b", re.I)


def _best_file(q: str):
    """The index's confident match for q, or None. Never waits on the disk:
    the router runs on every command, including ones that are not files."""
    try:
        from .tools import finder

        r = finder.find(q, refresh=False, wide_wait=0)
    except Exception:
        return None
    return r.best if r.confident else None


def _exact_personal_file(name: str) -> bool:
    """True if a file in the user's own folders is named exactly this."""
    b = _best_file(name)
    return bool(b and b.entry.rank == 0 and not b.entry.is_dir
                and b.coverage == 1.0 and b.extra == 0)


def _confident_file(q: str) -> bool:
    b = _best_file(q)
    if b is None:
        return False
    e = b.entry
    ext = e.name.rsplit(".", 1)[-1].lower() if "." in e.name else ""
    # Only the user's own folders, only files, never an installer: "open
    # valorant" must not run "Install VALORANT.exe" out of Downloads, and
    # "open youtube" must not find a youtube.png deep inside a driver folder.
    return (e.rank == 0 and not e.is_dir and ext not in ("exe", "msi")
            and b.coverage >= 0.85)     # no guessing on a misheard word


def _direct_folder(text: str) -> tuple[str, dict] | None:
    """File Explorer, a known folder, or "show X in explorer" / "where is X"."""
    t = text.strip()
    if _EXPLORER_CMD.match(t):
        return "open_folder", {"name": "explorer"}

    m = _REVEAL_CMD.match(t)
    if m:
        if m.group("a"):
            return "reveal_path", {"path": m.group("a").strip()}
        target = m.group("b").strip()
        # "where is my phone" is not a file question. Only take it when it
        # says file, or the name really is a file in the user's folders.
        if m.group("file") or _FILE_HINT.search(target) or _confident_file(target):
            return "reveal_path", {"path": target}
        return None

    m = _FOLDER_CMD.match(t)
    if not m:
        return None
    name = re.sub(r"\s+", " ", m.group("name")).strip().lower()
    verb = m.group("verb").lower()
    explicit = bool(m.group("suffix") or m.group("exp"))
    from .tools.finder import FOLDER_WORDS

    if name in FOLDER_WORDS and name not in ("home", "user folder", "photos folder"):
        if explicit:
            return "open_folder", {"name": name}
        if name in _BARE_FOLDERS:
            if name == "desktop" and not verb.startswith(("open", "go", "take")):
                return None
            return "open_folder", {"name": name}
        return None
    if m.group("suffix") and not _NOT_A_FILE.search(name):
        return "open_folder", {"name": name}   # "open the ltspice folder"
    return None


def _direct_file(text: str) -> tuple[str, dict] | None:
    """Turn "open <file>" into open_file once it is clearly not an app.

    Runs after _direct_app. Taken when the words say it is a file ("pdf",
    "notes", "resume.docx") or the name really is a file in the user's own
    folders; otherwise the model gets it.
    """
    m = _OPEN_CMD.match(text.strip())
    if not m:
        return None
    q = re.sub(r"\s+", " ", m.group("q")).strip()
    if not q or _NOT_A_FILE.search(q) or _QUESTION_WORD.match(q):
        return None
    if _FILE_HINT.search(q) or _confident_file(q):
        return "open_file", {"path": q}
    return None


# "Play the first song" went to the media keys, which can only skip, so it
# played the second. The position is stated plainly enough to read.
_ORDINALS = {
    "first": 1, "1st": 1, "one": 1, "second": 2, "2nd": 2, "two": 2,
    "third": 3, "3rd": 3, "three": 3, "fourth": 4, "4th": 4, "four": 4,
    "fifth": 5, "5th": 5, "five": 5, "sixth": 6, "6th": 6, "six": 6,
    "seventh": 7, "7th": 7, "seven": 7, "eighth": 8, "8th": 8, "eight": 8,
    "ninth": 9, "9th": 9, "nine": 9, "tenth": 10, "10th": 10, "ten": 10,
    "eleventh": 11, "11th": 11, "eleven": 11, "twelfth": 12, "12th": 12,
    "twelve": 12, "thirteenth": 13, "13th": 13, "thirteen": 13,
    "fourteenth": 14, "14th": 14, "fourteen": 14, "fifteenth": 15,
    "15th": 15, "fifteen": 15, "twentieth": 20, "20th": 20, "twenty": 20,
    "last": -1, "final": -1,          # play_track scrolls to the end of the list
}

# Speech does not deliver "the seventh song" tidily. It came back as "a seven
# song" and as "fifth number music", so the article, the filler and whatever
# noun lands at the end all have to be optional.
_TRACK_CMD = re.compile(
    r"^\W*(?:(?:athena|jarvis)\W+)?(?:please\s+)?(?:can you\s+)?"
    r"play\s+(?:me\s+)?(?:the\s+|a\s+|an\s+)?"
    r"(?:(?:song|track|number)\s+){0,2}(?:the\s+)?"
    r"(?P<num>\d+|" + "|".join(sorted(_ORDINALS, key=len, reverse=True)) + r")"
    r"(?:\s*(?:st|nd|rd|th))?"
    r"(?:\s+(?:number|song|songs|track|music|one))*"
    r"(?:\s+(?:of|in|from|on)\s+(?:my|the|this)\s+(?:playlist|list|queue|album|liked songs))?"
    r"\W*$",
    re.I,
)


def _direct_track(text: str) -> tuple[str, dict] | None:
    """Turn 'play the first song' into play_track, not a media-key skip."""
    m = _TRACK_CMD.match(text.strip())
    if not m:
        return None
    raw = m.group("num").lower()
    number = int(raw) if raw.isdigit() else _ORDINALS.get(raw, 0)
    if number == 0:
        return None
    return "play_track", {"position": number}


# "Play <something>" where the something is a song, not a position and not
# one of the words that mean "just play anything".
_SONG_GENERIC = {
    "music", "song", "songs", "some music", "something", "anything",
    "next", "previous", "last", "spotify", "playlist", "my playlist",
    "a song", "some songs", "it", "that", "this", "one", "more",
    "this song", "that song", "the song", "it again", "this one",
}

_SONG_CMD = re.compile(
    r"^\W*(?:(?:athena|jarvis)\W+)?(?:please\s+)?(?:can you\s+)?"
    r"play\s+(?:me\s+)?(?P<q>.+?)\s*\W*$",
    re.I,
)


def _direct_song(text: str) -> tuple[str, dict] | None:
    """Turn 'play <song>' into a Spotify search, once nothing else fits.

    Runs after the position and app routers, so "play the third song" and
    "play spotify" are already gone by the time this sees anything.
    """
    m = _SONG_CMD.match(text.strip())
    if not m:
        return None
    query = re.sub(r"\s+", " ", m.group("q")).strip()
    trimmed = re.sub(r"^(?:the|a|an)\s+", "", query, flags=re.I)
    trimmed = re.sub(r"\s+(?:on|in)\s+spotify$", "", trimmed, flags=re.I).strip()
    # "something by arijit singh" searched Spotify for the word "something".
    trimmed = re.sub(r"^(?:something|anything|songs?|music|some\s+(?:songs?|music)|a\s+song)\s+"
                     r"(?:by|from|of)\s+", "", trimmed, flags=re.I).strip()

    low = trimmed.lower()
    # "songs that I like" is a request for her pick, not a title.
    if re.match(r"^(?:songs?|music|tracks?)\s+(?:that\s+)?i\s+(?:like|love|listen\s+to)\b", low):
        return "play_my_taste", {}
    if not low or low in _SONG_GENERIC:
        return None
    if re.match(r"^(?:next|previous|last|some|any|more)\b", low):
        return None
    if "playlist" in low:
        return None
    if len(low) < 4 and len(low.split()) == 1:
        return None
    return "play_song", {"query": trimmed}


# Questions about this machine, which look like study questions but are not.
# "What is my battery" must not open a page about electrochemistry.
_SYSTEM_TOPIC = re.compile(
    r"\b(battery|volume|time|date|day|weather|wifi|wi-fi|bluetooth|reminder|"
    r"reminders|alarm|file|files|folder|folders|downloads|documents|desktop|"
    r"explorer|pdf|song|track|playing|playlist|spotify|"
    r"whatsapp|outlook|email|screen|screenshot|window|windows|cpu|ram|disk|"
    r"storage|contact|contacts|my name|running|open apps)\b"
    # "what is my pc doing" is a question about this machine, and went to the
    # explainer, which spent 44 seconds writing an essay on what computers do
    # in general. Matched as whole phrases rather than by adding "pc",
    # "process" or "memory" to the words above: an engineering student really
    # does ask about the Poisson process, task scheduling and cache memory,
    # and those must still reach the explainer.
    r"|\b(?:my|this|the)\s+(?:pc|computer|laptop|machine|desktop)\b"
    r"|\btask\s*manager\b"
    r"|\b(?:tasks?|processes|programs|apps|applications)\s+(?:are\s+|is\s+)?"
    r"(?:running|open|active)\b"
    r"|\brunning\s+(?:tasks?|processes|programs|apps|applications)\b",
    re.I,
)

_STUDY_VERB = re.compile(
    r"^\W*(?:(?:athena|jarvis)\W+)?(?:please\s+|can you\s+|could you\s+)*"
    r"(?:explain|derive|solve|prove|calculate|compute|define|describe|"
    r"compare|contrast|simplify|integrate|differentiate|"
    r"summari[sz]e|teach me|walk me through|help me understand|"
    r"what is the difference between|difference between|how does|how do|"
    r"why does|why do|what causes)\b",
    re.I,
)

# "what is X" is a class question when X is not something on this PC.
_STUDY_WHAT = re.compile(
    r"^\W*(?:(?:athena|jarvis)\W+)?what\s+(?:is|are|was|were)\s+(?P<topic>.+)$", re.I)

# Whatever opens the question, so the rest can be looked at on its own.
_STUDY_PREFIX = re.compile(
    r"^\W*(?:(?:athena|jarvis)\W+)?(?:please\s+|can you\s+|could you\s+)*"
    r"(?:what\s+(?:is|are|was|were)|explain|calculate|compute|solve|"
    r"work out|tell me)\s+", re.I)

# A bare sum: digits, operators, and the words we say them with. No letters
# beyond those words and no "=", so an equation to solve is not caught.
_ARITHMETIC = re.compile(
    r"(?:[\d\s.,+\-*/^()%]|\b(?:plus|minus|times|multiplied|divided|by|of|"
    r"percent|into|and|point)\b)+", re.I)


# Things the model got wrong by answering from nothing or reaching for the
# wrong tool (28 Sep 2026): the time abroad, the song playing, the Wi-Fi
# network and the IP address.
_INFO_LEAD = r"^\W*(?:(?:athena|jarvis|hey|ok|okay|please)\W+)*(?:(?:can|could)\s+you\s+)?(?:(?:tell|show)\s+me\s+)?"
_TIME_IN = re.compile(
    _INFO_LEAD + r"(?:what(?:'?s|\s+is)\s+the\s+time|what\s+time\s+is\s+it|(?:the\s+)?(?:current\s+|local\s+)?time)"
    r"\s+(?:right\s+now\s+|now\s+)?in\s+(?P<place>[a-z][a-z .'-]{1,30}?)(?:\s+(?:right\s+)?now)?\W*$", re.I)
_NOW_PLAYING = re.compile(
    _INFO_LEAD + r"(?:what(?:'?s|\s+is)\s+(?:this|the|that)\s+song(?:\s+(?:called|playing|name))?|"
    r"what\s+song\s+is\s+(?:this|playing|on|that)(?:\s+(?:right\s+)?now)?|what(?:'?s|\s+is)\s+playing(?:\s+(?:right\s+)?now)?|"
    r"which\s+song\s+is\s+(?:this|playing|on)|what\s+am\s+i\s+listening\s+to|name\s+(?:of\s+)?(?:this|the)\s+song|"
    r"who\s+(?:sings|sang|is\s+singing)\s+(?:this|this\s+song|the\s+song))\W*$", re.I)
_WIFI_NAME = re.compile(
    _INFO_LEAD + r"(?:(?:which|what)\s+(?:wi-?fi|network|wifi\s+network)\s+(?:am\s+i|are\s+we|is\s+(?:this|the)\s+(?:pc|laptop))\s+(?:connected|on)(?:\s+to)?|"
    r"what(?:'?s|\s+is)\s+(?:my|the)\s+(?:wi-?fi|network)(?:\s+name)?|am\s+i\s+connected\s+to\s+(?:the\s+)?(?:wi-?fi|internet))\W*$", re.I)
_IP = re.compile(
    _INFO_LEAD + r"(?:what(?:'?s|\s+is)\s+)?(?:my|the|this\s+(?:pc'?s|laptop'?s))\s+(?:(?:public|local|private)\s+)?ip(?:\s+address)?\W*$", re.I)


def _direct_info(text: str) -> tuple[str, dict] | None:
    t = text.strip()
    m = _TIME_IN.match(t)
    if m and not re.fullmatch(r"(?:the\s+)?(?:morning|evening|afternoon|night|here|india\s+time)", m.group("place"), re.I):
        return "world_time", {"place": m.group("place").strip()}
    if _NOW_PLAYING.match(t):
        return "now_playing", {}
    if _WIFI_NAME.match(t):
        return "pc_status", {"what": "wifi"}
    if _IP.match(t):
        return "pc_status", {"what": "ip"}
    return None


_BRIEF = re.compile(
    r"^\W*(?:(?:athena|jarvis)\W+)?(?:please\s+|can you\s+)*"
    # "what's" arrives as "whats" as often as not - Whisper drops the
    # apostrophe and so does anyone typing quickly.
    r"(?:"
    r"(?:what'?s?(?:\s+is|\s+does)?\s+(?:my|the)\s+(?:(?:full|whole|entire|complete|remaining|rest\s+of\s+(?:my|the))\s+)?(?:day|schedule|calendar|"
    r"timetable|agenda|plan)\b)"
    r"|(?:what'?s?(?:\s+is)?\s+(?:on|up|happening)\s+(?:today|tomorrow)\b)"
    # "what's on my calendar today" checked the date and said "no events".
    r"|(?:what'?s?(?:\s+is)?\s+(?:on|in)\s+(?:my|the)\s+(?:calendar|schedule|agenda|timetable)\b)"
    r"|(?:(?:check|show|open|read)\s+(?:me\s+)?(?:my|the)\s+(?:calendar|schedule|agenda)\b)"
    r"|(?:do\s+i\s+have\s+(?:any(?:thing)?\s+)?(?:classes|lectures|meetings|events|plans)\b)"
    r"|(?:what\s+(?:have i|do i have)\s+(?:on|got)\b)"
    r"|(?:(?:brief|breathe|breef|brieve)\s+me\b)|(?:my\s+brief\b)|(?:daily\s+brief\b)"
    r"|(?:what\s+(?:classes|lectures|meetings)\s+(?:do i have|are)\b)"
    r"|(?:am i\s+free\b)"
    r")", re.I)

_TOMORROW = re.compile(r"\btomorrow\b", re.I)


def _direct_brief(text: str) -> tuple[str, dict] | None:
    """Turn "what's my day" into the brief, rather than a guess at a tool.

    The model reaches for list_reminders or answers from nothing at all when
    asked this, because the question does not name a tool. The phrasing is
    regular enough to route outright.
    """
    if not _BRIEF.match(text.strip()):
        return None
    day = "tomorrow" if _TOMORROW.search(text) else "today"
    # Today's brief skips what is already over; "full", "whole day", "all my
    # classes" or "including earlier" asks for everything.
    full = bool(re.search(r"\b(full|whole|entire|all\s+(?:my\s+|of\s+(?:my|the)\s+|the\s+)?"
                          r"(?:classes|lectures|day|schedule)|including|earlier|already)\b",
                          text, re.I))
    return "daily_brief", {"day": day, "full": full}


# A question about the user's own data that no router above caught. Left to
# the model, it guessed: "your next MS Teams meeting is tomorrow at 14:00",
# repeated even after "but tomorrow is Sunday" (3 Oct 2026). These always go
# to the tool that has the real answer.
_G_ASK = re.compile(r"^\W*(?:(?:athena|jarvis|so|and|hey|ok|okay|but|then)\W+)*(?:what|when|which|where|how\s+(?:many|much|is|are|'?s)|"
                    r"do\s+i|did\s+i|am\s+i|is\s+there|are\s+there|any|anything|tell\s+me|show|check|list|have\s+i|"
                    r"can\s+you\s+(?:tell|check|show)|is\s+my|are\s+my|what'?s)\b", re.I)
_G_MINE = re.compile(r"\b(?:my|i|me|mine|i'?ve|i'?m)\b", re.I)
_GROUNDS = (
    (r"\battendance\b|\bbunk|\bskip\b", "my_attendance", None),
    (r"\bteams?\b.*\b(?:meeting|class|lecture|call)s?\b|\bmeetings?\b", "teams_meetings", "day"),
    (r"\b(?:class|classes|lecture|lectures|timetable|lab)\b", "upes_classes", "day"),
    (r"\b(?:deadline|deadlines|assignment|assignments|due|submission|quiz|quizzes|homework|lms)\b", "lms_deadlines", None),
    (r"\b(?:task|tasks|to-?dos?)\b", "list_tasks", None),
    (r"\b(?:reminder|reminders|alarm|alarms)\b", "list_reminders", None),
    (r"\b(?:schedule|agenda|calendar|plans?|busy|free)\b", "daily_brief", "brief"),
    (r"\b(?:battery|charge|charging)\b", "battery", None),
    (r"\b(?:mail|mails|email|emails|inbox)\b", "inbox_summary", None),
)


def _direct_grounded(text: str) -> tuple[str, dict] | None:
    if not (_G_ASK.match(text) and _G_MINE.search(text)):
        return None
    day = ("next" if re.search(r"\bnext\b|\bwhen\b", text, re.I) else
           "tomorrow" if re.search(r"\btomorrow\b", text, re.I) else "today")
    for pattern, name, arg in _GROUNDS:
        if re.search(pattern, text, re.I) and registry.get(name) is not None:
            if arg == "day":
                return name, {"day": day}
            if arg == "brief":
                return name, {"day": "tomorrow" if day == "tomorrow" else "today", "full": False}
            return name, {}
    return None


def _direct_explain(text: str) -> tuple[str, dict] | None:
    """Send a class question to the explainer rather than the spoken reply.

    The ordinary voice answer is tuned to be short, which is wrong for
    anything with a derivation in it - and the LaTeX the model writes gets
    read aloud as "backslash frac backslash".
    """
    q = text.strip()
    if len(q.split()) < 3 or _SYSTEM_TOPIC.search(q):
        return None

    # "what is 2 plus 2" is a question, not a class question. The explainer
    # hands it to study.model, which reasons at length and renders a page -
    # thirty seconds and a browser tab to be told the answer is 4. Sums with
    # a letter or an equals sign in them ("solve x^2 - 5x + 6 = 0") are real
    # work and still go through.
    if _ARITHMETIC.fullmatch(_STUDY_PREFIX.sub("", q).strip(" ?.")):
        return None

    if _STUDY_VERB.match(q):
        return "explain", {"question": q}

    m = _STUDY_WHAT.match(q)
    # "what are you doing" is talk, not a topic (it rendered a study page, 3 Oct 2026).
    if m and len(m.group("topic").split()) >= 2 and not _QUICK_FACT.match(m.group("topic")) \
            and not re.match(r"(?:you|your|yours|we|i|me|my|he|she|they|it|this|that|there|up)\b",
                             m.group("topic"), re.I):
        return "explain", {"question": q}
    return None


# "what is the capital of france" is a one-line fact, not class work: the
# explainer sent it to study.model and opened a formatted page to say Paris.
_QUICK_FACT = re.compile(
    r"^(?:the\s+)?(?:capital|population|currency|president|prime\s+minister|ceo|founder|owner|"
    r"height|age|distance|price|cost|score|weather|full\s+form|spelling|synonym|"
    r"opposite|plural|birthday|address|phone\s+number|name|national\s+\w+|"
    r"official\s+language)\b", re.I)
# Not "time", "speed", "area" or "length": "the time constant of an RC
# circuit" and "the area of a circle" are class questions.


# Whisper's stock endings, added to speech that never said them: "call maa
# on phone. And we'll see you in the next one." (25 Sep 2026).
_OUTRO = re.compile(r"[\s.,!?]+(?:and\s+)?(?:we'?ll\s+)?(?:see\s+you\s+(?:in\s+the\s+next\s+(?:one|video)|next\s+time|soon)|"
                    r"thanks?\s+(?:you\s+)?for\s+watching|please\s+subscribe|bye(?:\s+bye)?)[\s.!]*$", re.I)


# What Whisper makes up in the silence after you stop, as a sentence of its
# own: "play the music. Thank you." played a song called "Thank You For The
# Love" (26 Sep 2026); "brief me. Okay.", "...your choice. first." likewise.
_FILLER_TAIL = re.compile(
    r"(?<=\S)[.!?]+\s+(?:thanks?(?:\s+you)?(?:\s+(?:so\s+much|very\s+much))?|ok(?:ay)?|you\s+know|do\s+you\s+know|"
    r"first|yeah|yes|yep|right|alright|all\s+right|hmm+|um+|uh+|so|bye|you|oh|ah|well|i\s+mean|"
    r"good|great|nice|cool|sure|there\s+you\s+go|here\s+we\s+go)[\s.!?,]*$", re.I)


def clean_speech(text: str) -> str:
    t = _OUTRO.sub("", text.strip())
    for _ in range(2):                     # "... Okay. Thank you."
        t = _FILLER_TAIL.sub(".", t)
    return re.sub(r"[\s,.]+(?:and|so|but|then)\W*$", "", t, flags=re.I).strip()


def _direct_store(said: str) -> tuple[str, dict] | None:
    from .tools import ragazza
    return ragazza.route(said)


def route(text: str) -> tuple[str, dict] | None:
    """The tool call a command maps to without asking the model, or None.

    Shared by Agent.ask and by routines' `do:` steps, so a routine can say
    "open downloads" exactly as you would.
    """
    said = _fix_verb(clean_speech(text))
    return (_direct_phone_ctl(said) or _direct_teams(said) or _direct_power(said) or _direct_basic(said) or _direct_info(said) or _direct_memory(said)
            or _direct_phone_files(said) or _direct_find_document(said)
            or _direct_store(said)
            or _direct_routine(said) or _direct_it(said)
            or _direct_reminder(said) or _direct_rules(said) or _direct_track(said)
            or _direct_phone_me(said)
            or _direct_call(said) or _direct_places(said) or _direct_folder(said)
            or _direct_bluetooth(said)
            or _direct_app(said) or _direct_file(said)
            or _direct_message(said) or _direct_song(said)
            or _direct_brief(said) or _direct_grounded(said) or _direct_explain(said))


# Automations (jarvis/rules.py). A rule needs its trigger set off by a comma -
# "when I get home, play my playlist" - or the action first - "organise my
# downloads every 2 hours"; a bare "when is my next class" is a question.
_R_LEAD = r"^\W*(?:(?:athena|jarvis|hey|ok|okay|please)\W+)*(?:can\s+you\s+)?(?:please\s+)?"
_R_PAUSE = re.compile(_R_LEAD + r"(?:pause|stop|disable|turn\s+off)\s+(?:all\s+)?(?:the\s+|your\s+)?"
                      r"(?:automations?|autopilot|rules)(?:\s+for\s+(?P<n>\d+)\s+(?P<u>min|minute|hour)s?)?\W*$", re.I)
_R_RESUME = re.compile(_R_LEAD + r"(?:resume|restart|enable|turn\s+on|start)\s+(?:all\s+)?(?:the\s+|your\s+)?"
                       r"(?:automations?|autopilot|rules)\W*$", re.I)
_R_LIST = re.compile(_R_LEAD + r"(?:list|show|what\s+are)\s+(?:me\s+)?(?:my\s+|your\s+|all\s+)?(?:automations|rules)\W*$", re.I)
_R_DID = re.compile(_R_LEAD + r"what\s+(?:did|have)\s+you\s+(?:done|do)\s+(?:on\s+your\s+own|by\s+yourself|"
                    r"automatically)(?:\s+today)?\W*$", re.I)
_R_TRIGGER = r"(?:every\s*time|whenever|each\s+time|when|every\s+(?:day|morning|night|evening|\d+\s+\w+|hour|minute)|daily|hourly|at\s+\d)"
_R_WHEN_FIRST = re.compile(_R_LEAD + r"(?P<when>" + _R_TRIGGER + r"[^,]{2,60}?)\s*,\s*(?P<do>.{3,})$", re.I)
_R_DO_FIRST = re.compile(_R_LEAD + r"(?P<do>(?!remind)(?:play|open|close|connect|turn|start|lock|organi[sz]e|tidy|clean|"
                         r"check|run|say|send|switch)\b.{2,80}?)\s+(?P<when>(?:every\s*time|whenever|every\s+(?:day|morning|"
                         r"night|evening|\d+\s+(?:min|minute|hour|day)s?|hour)|daily|hourly)\b.*)$", re.I)


def _direct_rules(text: str) -> tuple[str, dict] | None:
    m = _R_PAUSE.match(text)
    if m:
        n = int(m.group("n") or 0) * (60 if (m.group("u") or "").startswith("hour") else 1)
        return "pause_automation", {"minutes": n}
    if _R_RESUME.match(text):
        return "resume_automation", {}
    if _R_LIST.match(text):
        return "list_rules", {}
    if _R_DID.match(text):
        return "what_did_you_do", {}
    if re.match(_R_LEAD + r"(?:run\s+(?:a\s+)?)?(?:health\s*check|check\s+yourself|self[\s-]?check|"
                r"how\s+are\s+you\s+running|any\s+problems\s+with\s+you)\W*$", text, re.I):
        return "health_check", {}
    if re.match(_R_LEAD + r"(?:retrain|re-train|train)\s+(?:athena|yourself)(?:\s+tonight)?\W*$", text, re.I):
        return "retrain_athena", {}
    if re.match(_R_LEAD + r"(?:check|read|summari[sz]e|go\s+through|what'?s\s+(?:new\s+)?in)\s+(?:my\s+)?(?:e-?mails?|inbox|mail)"
                r"(?:\s+and\s+draft\s+repl(?:y|ies))?\W*$|" + _R_LEAD + r"draft\s+(?:my\s+)?(?:e-?mail\s+)?repl(?:y|ies)\W*$",
                text, re.I):
        return "inbox_summary", {"draft_replies": True}
    if re.match(_R_LEAD + r"connect\s+(?:my\s+)?gmail\s+drafts?\W*$", text, re.I):
        return "connect_gmail_drafts", {}
    if re.match(_R_LEAD + r"(?:stop|turn\s+off|dismiss|silence)\s+(?:the\s+)?alarm\W*$|" + _R_LEAD + r"alarm\s+off\W*$", text, re.I):
        return "stop_alarm", {}
    m = re.match(_R_LEAD + r"snooze(?:\s+(?:it|the\s+alarm))?(?:\s+for\s+(?P<n>\d+)\s*min\w*)?\W*$", text, re.I)
    if m:
        return "snooze_alarm", {"minutes": int(m.group("n") or 10)}
    # UPES portal (upes.py): attendance, bunk maths, classes with rooms.
    m = re.match(_R_LEAD + r"(?:can|should|could)\s+i\s+(?:skip|bunk|miss|leave)\s+(?:the\s+|my\s+|today'?s\s+|tomorrow'?s\s+)?"
                 r"(?P<c>.+?)(?:\s+(?:class|lecture|lab))?(?:\s+(?:today|tomorrow|this\s+week))?\W*$", text, re.I)
    if m and m.group("c").lower() not in ("it", "that", "this", "class", "the class", "today", "tomorrow"):
        return "can_i_skip", {"course": m.group("c")}
    m = re.match(_R_LEAD + r"(?:what'?s|what\s+is|how'?s|how\s+is|show|check|tell\s+me)\s+(?:me\s+)?(?:my\s+)?attendance"
                 r"(?:\s+(?:in|for|of)\s+(?P<c>.+?))?\W*$|" + _R_LEAD + r"(?:my\s+)?attendance(?:\s+(?:in|for)\s+(?P<c2>.+?))?\W*$",
                 text, re.I)
    if m:
        return "my_attendance", {"course": (m.group("c") or m.group("c2") or "").strip()}
    m = re.match(_R_LEAD + r"(?:what|which)\s+classes\s+(?:do\s+i\s+have|have\s+i\s+got|are\s+there)\s*(?P<d>today|tomorrow)?\W*$|"
                 + _R_LEAD + r"(?:where|which\s+room)\s+is\s+my\s+(?:next\s+)?class\W*$", text, re.I)
    if m:
        return "upes_classes", {"day": (m.group("d") or "today").lower()}
    if re.match(_R_LEAD + r"(?:what'?s|what\s+is|anything|check|show)\s+(?:me\s+)?(?:due\s+)?(?:on\s+)?(?:my\s+)?(?:the\s+)?lms"
                r"(?:\s+deadlines?|\s+assignments?)?\W*$|" + _R_LEAD + r"(?:what'?s|what\s+is)\s+due\s+on\s+(?:the\s+)?lms\W*$|"
                + _R_LEAD + r"(?:lms|moodle)\s+(?:deadlines?|assignments?)\W*$", text, re.I):
        return "lms_deadlines", {}
    # LMS items open signed in (upes.open_lms) - "open the feedback forum for
    # leading conversation" went to open_url with a guessed address (3 Oct 2026).
    m = re.match(_R_LEAD + r"open\s+(?:up\s+)?(?:the\s+|my\s+)?(?P<w>.*?\b(?:forum|assignment|quiz|submission|lab\s+record|"
                 r"discussion|course|lms|moodle)\b.*?)(?:\s+(?:on|in|from)\s+(?:the\s+)?(?:lms|moodle))?\W*$", text, re.I)
    if m and not re.search(r"\b(?:file|folder|pdf|docx?|pptx?)\b", m.group("w"), re.I):
        w = m.group("w").strip()
        return "open_lms", {"what": "" if w.lower() in ("lms", "the lms", "moodle") else w}
    # Whisper hears UPES as "UPS" (3 Oct 2026); bare "open UPES" means the portal too.
    if re.match(_R_LEAD + r"open\s+(?:the\s+|my\s+)?(?:upes|ups|u\.?\s*p\.?\s*e\.?\s*s|university|uni|college)\s*"
                r"(?:portal\s*)?(?:login|log\s+in|sign[\s-]?in|website|site)?\W*$", text, re.I) \
            and not re.match(_R_LEAD + r"open\s+(?:the\s+|my\s+)?(?:university|uni|college)\W*$", text, re.I):
        return "open_upes_login", {}
    if re.match(_R_LEAD + r"(?:plan|schedule|organi[sz]e)\s+(?:my\s+|the\s+)?(?:assignments?|deadlines|homework)\W*$", text, re.I):
        return "plan_my_assignments", {}
    if re.match(_R_LEAD + r"(?:prepare|prep|get)\s+(?:me\s+)?(?:ready\s+)?for\s+tomorrow(?:'?s)?(?:\s+classes)?\W*$"
                r"|" + _R_LEAD + r"(?:make|write)\s+(?:me\s+)?primers?\s+for\s+tomorrow", text, re.I):
        return "prepare_for_tomorrow", {}
    m = re.match(_R_LEAD + r"(?:make|create|give)\s+(?:me\s+)?(?:a\s+)?(?:revision|study|exam)\s+(?:plan|timetable|schedule)"
                 r"\s+for\s+(?:my\s+)?(?P<subs>.+?)\s+(?:exams?\s+)?(?:on|by|before)\s+(?:the\s+)?(?P<date>[\w -]+?)\W*$", text, re.I)
    if m:
        return "exam_plan", {"exam_date": m.group("date"), "subjects": re.sub(r"\bexams?\b", "", m.group("subs")).strip()}
    if re.match(_R_LEAD + r"(?:watch|keep\s+an?\s+eye\s+on|monitor|check)\s+(?:my\s+)?(?:ragazza\s+|store\s+)?orders\b"
                r"|.*\b(?:order|orders)\b.*\bstuck\b", text, re.I):
        d = re.search(r"(\d+)\s*days?", text)
        return "add_watch", {"what": "stuck_orders", "every_hours": 6, "value": d.group(1) if d else "2"}
    if re.match(_R_LEAD + r"(?:warn|tell|alert)\s+me\s+(?:if|when)\s+(?:my\s+)?attendance\b", text, re.I):
        p = re.search(r"(\d+)\s*(?:%|percent)", text)
        return "add_watch", {"what": "attendance", "every_hours": 12, "value": p.group(1) if p else "75"}
    if re.match(_R_LEAD + r"(?:every\s+(?:evening|night)\s*,?\s*)?(?:tell|remind|warn)\s+me\s+(?:what'?s|what\s+is|about\s+"
                r"(?:anything|things|tasks))\s+due\s+(?:by\s+)?tomorrow(?:\s+every\s+(?:evening|night))?\W*$", text, re.I):
        return "add_rule", {"when": "every day at 8pm", "do": "watch:due_soon"}
    m = _R_WHEN_FIRST.match(text) or _R_DO_FIRST.match(text)
    if m:
        from .rules import parse_when
        if parse_when(m.group("when")):
            return "add_rule", {"when": m.group("when").strip(), "do": m.group("do").strip(" .")}
    return None


# Teams classes (tools/teams.py).
_TEAMS_LIST = re.compile(r"^\W*(?:(?:athena|jarvis|hey|ok|okay|please)\W+)*(?:can\s+you\s+)?(?:please\s+)?"
                         r"(?=.*\b(?:teams?|online)\b)(?=.*\b(?:class|classes|meeting|meetings|lecture|lectures|calendar)\b)"
                         r"(?:what|which|any|show|list|tell\s+me|check|do\s+i\s+have|when|so)\b", re.I)
_TEAMS_JOIN = re.compile(r"^\W*(?:(?:athena|jarvis|hey|ok|okay|please)\W+)*(?:can\s+you\s+)?(?:please\s+)?join\s+"
                         r"(?:my\s+|the\s+)?(?:(?P<next>next|current|ongoing)\s+)?(?:(?:online|teams)\s+)?"
                         r"(?P<what>class|meeting|lecture|call|.+?)(?:\s+(?:on|in)\s+teams)?\W*$", re.I)


# The phone over Wi-Fi (tools/android.py). First in the chain: "phone battery"
# must not be read as the laptop's, nor "open WhatsApp on my phone" as the PC app.
_P_LEAD = r"^\W*(?:(?:athena|jarvis|hey|ok|okay|please|can\s+you|could\s+you)\W+)*"
_P_MY = r"(?:my\s+|the\s+)?(?:phone|mobile|galaxy|samsung)"


def _direct_phone_ctl(text: str) -> tuple[str, dict] | None:
    t = text.strip()
    if re.match(_P_LEAD + r"(?:show|open|mirror|cast)\s+(?:me\s+)?" + _P_MY + r"(?:'?s)?(?:\s+screen)?(?:\s+on\s+(?:the\s+)?(?:pc|laptop|screen))?\W*$", t, re.I):
        return "show_phone", {}
    m = re.match(_P_LEAD + r"(?:open|launch|start)\s+(?P<app>.+?)\s+(?:on|in)\s+" + _P_MY + r"\W*$", t, re.I)
    if m:
        return "phone_open_app", {"name": m.group("app")}
    if re.match(_P_LEAD + r"(?:where'?s|where\s+is|find|ring)\s+" + _P_MY + r"\W*$", t, re.I) \
            or re.match(_P_LEAD + r"i\s+(?:can'?t|cannot)\s+find\s+" + _P_MY + r"\W*$", t, re.I):
        return "find_my_phone", {}
    if re.search(r"\b(?:battery|charge|charging)\b", t, re.I) and re.search(r"\b(?:phone|mobile)\b", t, re.I):
        return "phone_status", {}
    if re.match(_P_LEAD + r"lock\s+" + _P_MY + r"\W*$", t, re.I):
        return "lock_phone", {}
    if re.match(_P_LEAD + r"(?:take\s+(?:a\s+)?)?(?:screenshot|screen\s*shot)\s+(?:of|on)\s+" + _P_MY + r"\W*$|"
                + _P_LEAD + r"(?:take\s+(?:a\s+)?)?(?:phone|mobile)\s+screenshot\W*$", t, re.I):
        return "phone_screenshot", {}
    m = re.match(_P_LEAD + r"(?P<a>pause|play|resume|next|skip|previous|mute|volume\s+up|volume\s+down|turn\s+(?:it\s+)?up|turn\s+(?:it\s+)?down)"
                 r"(?:\s+(?:the\s+)?(?:music|song|track|video))?\s+(?:on|in)\s+" + _P_MY + r"\W*$", t, re.I)
    if m:
        a = m.group("a").lower()
        a = {"resume": "play", "skip": "next", "turn up": "volume up", "turn it up": "volume up",
             "turn down": "volume down", "turn it down": "volume down"}.get(a, a)
        return "phone_media", {"action": re.sub(r"\s+", " ", a)}
    return None


def _direct_teams(text: str) -> tuple[str, dict] | None:
    # "do I have any MS meetings?" came back as "MS Mutings" and the model
    # invented a "mute" tool (3 Oct 2026).
    # "when is my next MS team meeting" (team, singular) missed this and the
    # model made up "tomorrow at 14:00" (3 Oct 2026).
    day = ("next" if re.search(r"\bnext\b|\bwhen\b", text, re.I) else
           "tomorrow" if re.search(r"tomorrow", text, re.I) else "today")
    if re.search(r"\b(?:ms|microsoft|m\.s\.)\s+(?:teams?\s+)?(?:meetings?|mutings?|meating?s?|classes|calls?)\b", text, re.I):
        return "teams_meetings", {"day": day}
    m = _TEAMS_LIST.match(text)
    if m:
        return "teams_meetings", {"day": day}
    m = _TEAMS_JOIN.match(text)
    if m and (re.search(r"\bteams\b", text, re.I) or m.group("what").lower() in ("class", "meeting", "lecture")):
        what = m.group("what").strip()
        return "join_teams_meeting", {"which": "next" if what.lower() in ("class", "meeting", "lecture", "call")
                                      else what}
    return None


# Bluetooth. "connect to echo" reached the model, which had no connect action
# and announced a connection that never happened (3 Oct 2026).
_BT_LEAD = r"^\W*(?:(?:athena|jarvis|hey|ok|okay|please)\W+)*(?:can\s+you\s+|could\s+you\s+)?(?:please\s+)?"
_BT_CONNECT = re.compile(_BT_LEAD + r"(?:connect|pair)\s+(?:(?:to|with)\s+)?(?:my\s+|the\s+)?"
                         r"(?P<d>[\w'+.\- ]{2,40}?)(?:\s+(?:via|over|on|through|with)\s+bluetooth)?\W*$", re.I)
_BT_TOGGLE = re.compile(_BT_LEAD + r"(?:turn|switch)\s+(?:(?P<a>on|off)\s+(?:the\s+)?bluetooth|"
                        r"(?:the\s+)?bluetooth\s+(?P<b>on|off))\W*$|" + _BT_LEAD + r"bluetooth\s+(?P<c>on|off)\W*$", re.I)


def _direct_bluetooth(text: str) -> tuple[str, dict] | None:
    m = _BT_CONNECT.match(text)
    if m and not re.search(r"\b(?:wi-?fi|internet|vpn|network|hotspot|server|database|it|that|this)\b",
                           m.group("d"), re.I):
        return "bluetooth", {"action": "connect", "device": m.group("d").strip()}
    m = _BT_TOGGLE.match(text)
    if m:
        return "bluetooth", {"action": (m.group("a") or m.group("b") or m.group("c")).lower()}
    return None


# Memory controls. "remember that I ..." goes to the learned profile (it is
# about the user); "remember my wifi password is ..." is refused there by the
# secret filter, which is the point.
_MEM_SHOW = re.compile(
    r"^\W*(?:(?:athena|jarvis|hey|ok|okay|please)\W+)*(?:what\s+do\s+you\s+(?:remember|know)\s+about\s+me|"
    r"what\s+have\s+you\s+(?:learned|learnt)\s+about\s+me|show\s+(?:me\s+)?(?:my\s+profile|your\s+memory|"
    r"what\s+you\s+remember)|what\s+do\s+you\s+remember)\W*$", re.I)
_MEM_FORGET = re.compile(
    r"^\W*(?:(?:athena|jarvis|hey|ok|okay|please)\W+)*forget\s+(?:that\s+|about\s+|the\s+fact\s+that\s+)?(?P<x>.{3,})$", re.I)
_MEM_ADD = re.compile(
    r"^\W*(?:(?:athena|jarvis|hey|ok|okay|please)\W+)*(?:remember|note|keep\s+in\s+mind)\s+that\s+(?P<x>.{6,})$", re.I)


# "what did we decide about Q6", "when did I ask you about the timetable",
# "did we talk about ltspice", "find our conversation about the pdf".
_MEM_SEARCH = re.compile(
    r"^\W*(?:(?:athena|jarvis|hey|ok|okay|please)\W+)*(?:"
    r"what\s+did\s+(?:we|i|you)\s+(?:talk\s+about|discuss|decide|say|agree|conclude|find)"
    r"(?:\s+(?:about|on|regarding|for))?"
    r"|when\s+did\s+(?:i|we)\s+(?:ask(?:\s+you)?|talk|discuss|mention|tell\s+you)(?:\s+(?:about|for))?"
    r"|did\s+(?:we|i)\s+(?:ever\s+)?(?:talk|discuss|ask\s+you)(?:\s+about)?"
    r"|(?:find|search|look\s+up)\s+(?:our|my|the)?\s*(?:past\s+|old\s+|earlier\s+)?"
    r"(?:conversation|chat|discussion)s?\s+(?:about|on|where|when)"
    r"|remind\s+me\s+what\s+(?:we|you|i)\s+(?:said|decided|found|discussed)(?:\s+about)?"
    r")\s+(?P<q>.{3,})$", re.I)
_STYLE_CMD = re.compile(
    r"^\W*(?:(?:athena|jarvis|hey|ok|okay|please)\W+)*(?:learn|study|update|analy[sz]e)\s+"
    r"(?:my|how\s+i)\s+(?:writing\s+style|write|style)\W*$"
    r"|^\W*(?:(?:athena|jarvis)\W+)?how\s+do\s+i\s+write\W*$", re.I)


# "remember my locker number 42", "save my wifi name as Home5G", "remember
# that my locker number is 42", "my locker number is 42, remember that".
# Sent to the model, qwen answered "Noted, locker number 42" without calling
# anything, twice, and the user got "no tool ran" (28 Sep 2026). A labelled
# fact is regular enough to parse here and save outright.
_MEM_VERB = (r"^\W*(?:(?:athena|jarvis|hey|ok|okay|please)\W+)*(?:(?:can|could|would)\s+you\s+)?"
             r"(?:please\s+)?(?:remember|save|note(?:\s+down)?|store|keep\s+in\s+mind|write\s+down)"
             r"\s+(?:that\s+)?")
_LABEL = r"(?P<label>[a-z][\w'-]*(?:\s+[\w'-]+){0,5}?)"
_MEM_FACT_IS = re.compile(
    _MEM_VERB + r"(?:my|our|the)\s+" + _LABEL + r"\s*(?:\s(?:is|are|was|=|as)\s+|[:=]\s*)"
    r"(?P<value>\S.{0,150}?)\W*$", re.I)
# No "is": the value is whatever ends the sentence and has a digit in it -
# "locker number 42", "wifi name Home5G", "roll number 21BEC1023".
_MEM_FACT_BARE = re.compile(
    _MEM_VERB + r"(?:my|our|the)?\s*" + _LABEL
    + r"\s+(?P<value>[^\s]*\d[^\s]*(?:\s+[^\s]*\d[^\s]*)*)\W*$", re.I)
_MEM_FACT_AFTER = re.compile(
    r"^\W*(?:(?:athena|jarvis|hey|ok|okay)\W+)*(?:my|our)\s+" + _LABEL
    + r"\s+(?:is|are)\s+(?P<value>\S.{0,150}?)[\s,.;-]+(?:(?:please|so|and)\s+)?"
      r"(?:remember|save|note|store)(?:\s+(?:that|it|this))?\W*$", re.I)

# "what is my locker number", "what's my wifi name", "do you remember my
# roll number" - answered from saved facts. Only when one is actually saved
# under that name: otherwise "what is my X" stays with the other routers.
_MEM_ASK = re.compile(
    r"^\W*(?:(?:athena|jarvis|hey|ok|okay|please)\W+)*(?:"
    r"what(?:'?s|\s+is|\s+was|\s+are)|tell\s+me|do\s+you\s+(?:remember|know)|"
    r"remind\s+me(?:\s+of|\s+what)?|recall)\s+(?:my|our|the)\s+(?P<q>.{2,60}?)"
    r"(?:\s+(?:is|again|please))*\W*$", re.I)


# "what did we talk about?", "what did I ask you yesterday", "recap" - no
# topic, so a search by meaning had nothing to look for; it searched for
# "yesterday", or for nothing (28 Sep 2026). recent_conversations reads the
# day's history instead.
_MEM_RECAP = re.compile(
    r"^\W*(?:(?:athena|jarvis|hey|ok|okay|please|so)\W+)*(?:"
    r"what\s+(?:did|have|were)\s+(?:we|you|i)\s+(?:been\s+)?(?:talk(?:ing|ed)?|chat(?:ting|ted)?|"
    r"speak(?:ing)?|discuss(?:ing|ed)?)(?:\s+about)?"
    r"|what\s+did\s+i\s+ask(?:\s+you)?(?:\s+about)?"
    r"|(?:give\s+me\s+a\s+)?recap(?:\s+of\s+(?:our|the)\s+(?:conversation|chat))?"
    r")(?:\s+(?P<when>today|yesterday|earlier(?:\s+today)?|last\s+time|recently|before|so\s+far))?\W*$",
    re.I)


# "search my documents for signals and systems", "find the lab manual on my
# PC", "look for my DSP notes in my files".
_DOC_FIND = re.compile(
    r"^\W*(?:(?:athena|jarvis|hey|ok|okay|please|now|just)\W+)*(?:(?:can|could|would|will)\s+you\s+)?"
    r"(?:please\s+)?(?:search|find|look\s+for|look\s+up|locate|check)\s+"
    r"(?:(?:in|through)\s+)?(?:my|the)\s+(?:documents?|docs|files|pc|computer|laptop|downloads|"
    r"drives?|folders?|notes)\s+(?:for|about)\s+(?:the\s+|my\s+)?(?P<a>.+?)(?:\s+please)?\W*$"
    r"|^\W*(?:(?:athena|jarvis|hey|ok|okay|please|now|just)\W+)*(?:(?:can|could|would|will)\s+you\s+)?"
    r"(?:please\s+)?(?:search|find|look\s+for|locate)\s+(?:for\s+)?(?:the\s+|my\s+)?(?P<b>.+?)"
    r"\s+(?:in|on|from)\s+(?:my|the)\s+(?:documents?|docs|files|pc|computer|laptop|downloads|drives?|folders?)"
    r"(?:\s+please)?\W*$", re.I)


# "open all my signals and systems files", "open all the DSP pdfs".
_OPEN_ALL = re.compile(
    r"^\W*(?:(?:athena|jarvis|hey|ok|okay|please|now|just)\W+)*(?:(?:can|could|would|will)\s+you\s+)?"
    r"(?:please\s+)?(?:open|show|pull\s+up)\s+(?:up\s+)?(?:all|every|each)\s+(?:of\s+)?(?:the\s+|my\s+)?"
    r"(?P<q>.+?)\s*(?:files?|documents?|docs|pdfs?|notes|slides|ppts?|sheets?)\W*$", re.I)


# ---------------------------------------------------------------- phone files
# "send the lab manual to my phone", "share it with my phone", and - typed on
# the phone itself - just "send me the lab manual".
_NOT_A_FILE_TO_SEND = re.compile(
    r"^(?:a|an|the|my)?\s*(?:message|text|msg|whatsapp|email|mail|reminder|notification|"
    r"call|ring|alert|push|update|reply)\b", re.I)
_SEND_TO_PHONE = re.compile(
    r"^\W*(?:(?:athena|jarvis|hey|ok|okay|please|now|just)\W+)*(?:(?:can|could|would|will)\s+you\s+)?"
    r"(?:please\s+)?(?:send|share|transfer|move|copy|put|push|get)\s+(?:me\s+)?(?:over\s+)?"
    r"(?P<q>.+?)\s+(?:to|on|onto|with|over\s+to|on\s+to)\s+(?:my\s+|the\s+)?(?:phone|mobile|cell(?:\s*phone)?)"
    r"(?:\s+(?:please|now))*\W*$", re.I)
_SEND_ME = re.compile(
    r"^\W*(?:(?:athena|jarvis|hey|ok|okay|please|now|just)\W+)*(?:(?:can|could|would|will)\s+you\s+)?"
    r"(?:please\s+)?(?:send|give|share|pass)\s+me\s+(?P<q>.+?)(?:\s+(?:please|now))*\W*$", re.I)
# "send me ..." typed on the phone, without "to my phone": a file is "the X"
# or "my X"; "send me a joke", "send me some ideas" are asks for an answer.
_NOT_A_FILE_TO_SEND_ME = re.compile(
    r"^(?:a|an|some|any|more|another)\b|^(?:the\s+)?(?:jokes?|story|poem|quote|facts?|summary|list|"
    r"links?|recipe|news|weather|info(?:rmation)?|details|answer|songs?|playlist|ideas?|tips?|"
    r"suggestions?|something|anything)\b", re.I)
_FROM_PHONE = re.compile(
    r"^\W*(?:(?:athena|jarvis|hey|ok|okay|please|now|just)\W+)*(?:(?:can|could|would|will)\s+you\s+)?"
    r"(?:please\s+)?(?:open|show)\s+(?:me\s+)?(?:"
    r"(?:the\s+)?(?P<latest>last|latest|newest|recent)?\s*(?P<kind>files?|photos?|pictures?|documents?|pdfs?|things?)\s+"
    r"(?:(?:i|that\s+i|you)\s+)?(?:just\s+)?(?:sent|uploaded|shared|got|received)\s+(?:me\s+)?from\s+(?:my\s+|the\s+)?phone"
    r"|(?:the\s+)?(?:files?\s+)?from\s+(?:my\s+|the\s+)?phone(?:\s+folder)?"
    r"|what\s+(?:my\s+|the\s+)?phone\s+sent"
    r")\W*$", re.I)


def _direct_phone_files(text: str) -> tuple[str, dict] | None:
    t = text.strip()
    m = _FROM_PHONE.match(t)
    if m:
        kind = (m.group("kind") or "").lower()
        latest = m.group("latest") or (kind and not kind.endswith("s"))
        return "open_from_phone", {"which": "latest" if latest else "folder"}
    m = _SEND_TO_PHONE.match(t)
    if not m:
        from . import share
        m = _SEND_ME.match(t) if share.from_phone else None
        if m and _NOT_A_FILE_TO_SEND_ME.match(m.group("q").strip()):
            m = None
    if m:
        q = re.sub(r"^(?:the|my|a|an)\s+", "", m.group("q").strip(" .?"), flags=re.I)
        if q and not _NOT_A_FILE_TO_SEND.match(q):
            return "send_to_phone", {"file": q}
    return None


def _direct_find_document(text: str) -> tuple[str, dict] | None:
    m = _OPEN_ALL.match(text.strip())
    if m and m.group("q").strip():
        q = m.group("q").strip()
        kind = re.search(r"(pdfs?|docs?|documents?|notes|slides|ppts?|sheets?)\W*$", text.strip(), re.I)
        if kind and kind.group(1).lower().startswith(("pdf", "slide", "ppt", "sheet")):
            q += " " + kind.group(1).lower().rstrip("s")     # keep the type: "dsp pdf"
        return "find_document", {"query": q, "open_all": True}
    m = _DOC_FIND.match(text.strip())
    if not m:
        return None
    q = (m.group("a") or m.group("b") or "").strip(" .?")
    return ("find_document", {"query": q}) if q else None


def _direct_memory(text: str) -> tuple[str, dict] | None:
    t = text.strip()
    for pattern in (_MEM_FACT_AFTER, _MEM_FACT_IS, _MEM_FACT_BARE):
        m = pattern.match(t)
        if m:
            label = re.sub(r"\s+", " ", m.group("label")).strip(" ,.")
            value = m.group("value").strip(" ,.")
            if label and value:
                return "remember", {"what": value, "label": label}
    m = _MEM_RECAP.match(t)
    if m:
        return "recent_conversations", {"when": (m.group("when") or "").strip().lower()}
    m = _MEM_ASK.match(t)
    if m:
        try:
            from .memory import find_facts
            if find_facts(m.group("q"), threshold=0.65):
                return "recall", {"query": m.group("q").strip(" ?.")}
        except Exception:
            pass
    if _MEM_SHOW.match(t):
        return "what_you_remember", {}
    m = _MEM_SEARCH.match(t)
    if m:
        return "search_past_conversations", {"query": m.group("q").strip(" ?.")}
    if _STYLE_CMD.match(t):
        return "learn_my_writing_style", {}
    m = _MEM_FORGET.match(t)
    if m:
        return "forget_about", {"thing": m.group("x").strip(" .")}
    m = _MEM_ADD.match(t)
    if m:
        return "remember_about_me", {"note": m.group("x").strip()}
    return None


# Everyday one-liners that should never need the model: music keys, volume,
# screenshot, lock. "play music" used to reach the model - and, joined to
# "open Spotify", the planner - for what is one media key.
_BASIC_LEAD = r"^\W*(?:(?:athena|jarvis|hey|ok|okay|please|now|just|and|then)\W+)*(?:(?:can|could|would)\s+you\s+)?(?:please\s+)?"
_BASIC = [
    (re.compile(_BASIC_LEAD + r"(?:play|put\s+on|start|open)\s+(?:(?:my|the)\s+)?(?:spotify\s+)?playlist"
                r"(?:\s+(?:in|on|from)\s+(?:my\s+)?spotify)?(?:\s+that\s+you\s+(?:have|know|saved|remember).*)?\W*$", re.I),
     "play_my_playlist", {}),
    (re.compile(_BASIC_LEAD + r"(?:check|scan|look\s+for|find|any)\s+(?:my\s+|for\s+|new\s+)*"
                r"(?:deadlines?|assignments?|due\s+dates?)(?:\s+in\s+my\s+(?:mail|email|inbox))?\W*$", re.I),
     "check_deadlines", {}),
    # A song Athena picks from what you listen to. Before "play music",
    # which only presses play on whatever was paused.
    (re.compile(_BASIC_LEAD + r"(?:"
                r"(?:play\s+(?:me\s+)?)?(?:(?:a|some|any)\s+)?(?:music|songs?|tracks?|something|anything)?\s*"
                r"(?:of|from|by|at)?\s*your\s+(?:own\s+)?(?:choice|pick|liking|wish)"
                r"|play\s+(?:me\s+)?(?:something|anything|a\s+song|some(?:thing)?\s+(?:music|songs?)|a\s+track)\s+"
                r"(?:(?:that\s+)?i\s+(?:like|love|listen\s+to|would\s+like)|for\s+me|(?:that\s+)?you\s+(?:like|love|prefer|think\s+i'?d\s+like|choose)|good|nice|random)"
                r"|play\s+(?:me\s+)?(?:something|anything|a\s+(?:random\s+)?(?:song|track)|any\s+(?:song|track|music)|random\s+(?:music|songs?))"
                r"|play\s+(?:me\s+)?(?:my\s+)?(?:kind|type|style)\s+of\s+(?:music|songs?)|play\s+(?:me\s+)?my\s+(?:vibe|taste|style)"
                r"|surprise\s+me(?:\s+with\s+(?:a\s+song|(?:some\s+)?music))?"
                r"|(?:choose|pick)\s+(?:a\s+|some\s+)?(?:song|music|track)(?:\s+for\s+me)?"
                r")\W*$", re.I), "play_my_taste", {}),
    (re.compile(_BASIC_LEAD + r"(?:play|resume|start|put\s+on)\s+(?:some\s+|the\s+|my\s+)?(?:music|songs?|playback)"
                r"(?:\s+on\s+spotify)?\W*$", re.I), "music", {"action": "play"}),
    (re.compile(_BASIC_LEAD + r"(?:pause|stop)\s+(?:the\s+)?(?:music|song|playback|spotify)\W*$|"
                + _BASIC_LEAD + r"pause\W*$", re.I), "music", {"action": "pause"}),
    (re.compile(_BASIC_LEAD + r"(?:next|skip)(?:\s+(?:the\s+)?(?:song|track|this))?\W*$|"
                + _BASIC_LEAD + r"(?:play\s+the\s+)?next\s+(?:song|track)\W*$", re.I), "music", {"action": "next"}),
    (re.compile(_BASIC_LEAD + r"(?:previous|last|go\s+back\s+a)\s+(?:song|track)\W*$", re.I),
     "music", {"action": "previous"}),
    (re.compile(_BASIC_LEAD + r"take\s+a\s+screenshot\W*$|" + _BASIC_LEAD + r"screenshot\W*$", re.I),
     "take_screenshot", {}),
    (re.compile(_BASIC_LEAD + r"lock\s+(?:the\s+|my\s+)?(?:screen|pc|computer|laptop)\W*$", re.I),
     "lock_screen", {}),
]
# The Command Center's menu, and the same questions asked aloud: answered by
# their tool outright. "list my routines" and "what is my pc doing" went to
# the model and took seconds; from a menu click that reads as broken.
_BASIC += [
    # Gaming mode off: load the model back. Checked before "on" below.
    (re.compile(_BASIC_LEAD + r"(?:(?:turn|switch|shut)\s+off|stop|exit|end|disable|leave|quit|cancel)\s+(?:the\s+)?gam(?:e|er|ing)\s+mode\W*$|"
                + _BASIC_LEAD + r"gam(?:e|er|ing)\s+mode\s+(?:off|over|done|finished)\W*$|"
                + _BASIC_LEAD + r"(?:i'?m\s+)?done\s+(?:gaming|playing)\W*$|"
                + _BASIC_LEAD + r"(?:back\s+to\s+|switch\s+to\s+)?normal\s+mode\W*$|"
                + _BASIC_LEAD + r"(?:re)?load\s+(?:the\s+|your\s+)?(?:ai\s+)?models?(?:\s+back)?\W*$", re.I),
     "load_gpu", {}),
    # Frees the GPU for a game or an editor. Routed, so it works even when the
    # model itself is the thing filling the VRAM.
    (re.compile(_BASIC_LEAD + r"(?:(?:turn\s+on|start|enable|switch\s+to|go\s+(?:in)?to|enter)\s+)?gam(?:e|er|ing)\s+mode(?:\s+on)?\W*$|"
                + _BASIC_LEAD + r"(?:i'?m\s+(?:going\s+to\s+|gonna\s+)?(?:start\s+)?(?:gaming|playing\s+(?:a\s+)?games?)|time\s+to\s+game)(?:\s+now)?\W*$|"
                + _BASIC_LEAD + r"(?:free|clear|release|empty)\s+(?:up\s+)?(?:the\s+|my\s+|some\s+)?(?:gpu|vram|graphics\s+(?:card|memory)|video\s+memory)\W*$|"
                + _BASIC_LEAD + r"unload\s+(?:the\s+|all\s+(?:the\s+)?|your\s+)?(?:ai\s+)?models?\W*$", re.I),
     "free_gpu", {}),
    (re.compile(_BASIC_LEAD + r"(?:list|show|what\s+are)\s+(?:me\s+)?(?:my\s+|the\s+|your\s+)?"
                r"(?:routines|workflows)\W*$|" + _BASIC_LEAD + r"(?:my\s+)?(?:routines|workflows)\W*$", re.I),
     "list_routines", {}),
    (re.compile(_BASIC_LEAD + r"(?:list|show|what\s+are)\s+(?:me\s+)?(?:my\s+)?reminders\W*$|"
                + _BASIC_LEAD + r"(?:my\s+)?reminders\W*$", re.I), "list_reminders", {}),
    (re.compile(_BASIC_LEAD + r"(?:list|show|what\s+are|what'?s\s+on)\s+(?:me\s+)?(?:my\s+)?(?:tasks|to-?dos?|to-?do\s+list)"
                r"(?:\s+(?:for\s+)?today)?\W*$|" + _BASIC_LEAD + r"(?:my\s+)?(?:tasks|to-?do\s+list)\W*$|"
                + _BASIC_LEAD + r"what\s+do\s+i\s+(?:need|have)\s+to\s+do(?:\s+today)?\W*$", re.I), "list_tasks", {}),
    (re.compile(_BASIC_LEAD + r"(?:what\s+(?:tools|skills)\s+do\s+you\s+have|what\s+can\s+you\s+do|"
                r"(?:list|show)\s+(?:me\s+)?(?:your\s+)?(?:tools|skills)|tools\s+(?:and|&)\s+skills)\W*$", re.I),
     "list_skills", {}),
    (re.compile(_BASIC_LEAD + r"(?:what\s+is\s+my\s+(?:pc|computer|laptop)\s+doing|system\s+report|"
                r"(?:give\s+me\s+a\s+)?(?:pc|system)\s+(?:report|status))(?:\s+right\s+now)?\W*$", re.I),
     "system_report", {}),
]
# Screen awareness: questions about what is on the screen right now.
_SCREEN = re.compile(
    _BASIC_LEAD + r"(?:"
    r"what(?:'?s|\s+is)\s+(?:on|in)\s+(?:my|the|this)\s+screen|what\s+am\s+i\s+(?:looking\s+at|seeing|doing)|"
    r"(?:look|glance)\s+at\s+(?:my|the|this)\s+(?:(?:whole|entire|full)\s+)?screens?|can\s+you\s+see\s+(?:my|the)\s+screen|check\s+my\s+screen|"
    r"(?:read|summari[sz]e|explain|describe|translate)\s+(?:this|that|what'?s\s+on\s+(?:my|the)\s+screen|my\s+screen|"
    r"(?:this|the)\s+(?:page|screen|error|message|window|code|article|email|document|question|chart|graph|table))|"
    r"what\s+does\s+(?:this|that)\s+(?:say|mean)|help\s+me\s+with\s+(?:this|what'?s\s+on\s+(?:my|the)\s+screen)|"
    r"is\s+there\s+(?:an?\s+)?(?:error|problem|mistake)\s+on\s+(?:my|the)\s+screen"
    r")\b.*$", re.I)
_WHOLE = re.compile(r"\b(?:whole|entire|full|all)\s+(?:screen|screens|desktop|monitors?)\b", re.I)
# Battery, time and date: a tool answers each instantly; the model took
# seconds to decide to call it.
_BASIC += [
    (re.compile(_BASIC_LEAD + r"(?:(?:what(?:'?s|\s+is)|how(?:'?s|\s+is)|check)\s+(?:my\s+|the\s+)?)?"
                r"(?:battery|charge)(?:\s+(?:level|status|percentage|percent|life|left|at|now))*\W*$|"
                + _BASIC_LEAD + r"how\s+much\s+(?:battery|charge)(?:\s+(?:do\s+i\s+have|is\s+left|left|"
                r"have\s+i\s+got))?(?:\s+left)?\W*$|"
                + _BASIC_LEAD + r"(?:am\s+i|is\s+(?:my|the)\s+(?:laptop|pc|computer)|are\s+we)\s+"
                r"(?:charging|plugged\s+in|on\s+battery)\W*$", re.I),
     "battery", {}),
    (re.compile(_BASIC_LEAD + r"(?:what(?:'?s|\s+is)\s+the\s+time(?:\s+now)?|what\s+time\s+is\s+it(?:\s+now)?|"
                r"(?:tell\s+me\s+)?the\s+time(?:\s+please)?|time\s+(?:now|please)|"
                r"what'?s\s+the\s+time\s+right\s+now)\W*$", re.I),
     "get_datetime", {"what": "time"}),
    (re.compile(_BASIC_LEAD + r"(?:what(?:'?s|\s+is)\s+(?:the\s+date|today'?s\s+date)(?:\s+today)?|"
                r"what\s+(?:day|date)\s+is\s+it(?:\s+today)?|what\s+is\s+today|which\s+day\s+is\s+(?:it|today))\W*$", re.I),
     "get_datetime", {"what": "date"}),
]
# Disk, RAM, CPU and uptime: read from the system. Left to the model, "how
# much free space is on my C drive" came back as an invented figure.
_ASK = r"(?:(?:how\s+much|what(?:'?s|\s+is)(?:\s+(?:the|my))?|check(?:\s+(?:my|the))?|show(?:\s+me)?(?:\s+(?:my|the))?|tell\s+me(?:\s+(?:my|the))?)\s+)?"
_ON_PC = r"(?:\s+(?:on|in|of)\s+(?:my\s+|the\s+|this\s+)?(?:[a-z]\s*(?::|\s+drive)|laptop|pc|computer|disk|drive|system|ssd))?"
_BASIC += [
    (re.compile(_BASIC_LEAD + _ASK + r"(?:free\s+|available\s+|remaining\s+)?(?:disk\s+|storage\s+|drive\s+|hard\s+drive\s+)?"
                r"(?:space|storage)(?:\s+(?:is|do\s+i\s+have|have\s+i\s+got))?(?:\s+(?:left|free|available|remaining))?"
                + _ON_PC + r"(?:\s+(?:left|free|available|remaining))?\W*$|"
                + _BASIC_LEAD + r"(?:is\s+(?:my\s+)?(?:disk|drive|storage|[a-z]\s+drive)\s+(?:full|almost\s+full))\W*$", re.I),
     "pc_status", {"what": "disk"}),
    (re.compile(_BASIC_LEAD + _ASK + r"(?:ram|memory\s+usage|ram\s+usage|(?:ram|memory)\s+(?:is\s+)?(?:used|in\s+use|free|left|available))"
                + _ON_PC + r"(?:\s+(?:am\s+i\s+using|is\s+(?:used|free|left)|left|free|used))?\W*$|"
                + _BASIC_LEAD + r"how\s+much\s+(?:ram|memory)\s+(?:am\s+i\s+using|is\s+(?:being\s+)?used|is\s+(?:free|left)|do\s+i\s+have(?:\s+(?:left|free))?|left|free)"
                + _ON_PC + r"\W*$", re.I),
     "pc_status", {"what": "memory"}),
    (re.compile(_BASIC_LEAD + _ASK + r"(?:cpu|processor)\s*(?:usage|use|load|at)?" + _ON_PC + r"\W*$|"
                + _BASIC_LEAD + r"how\s+busy\s+is\s+(?:my\s+|the\s+)?(?:cpu|processor)\W*$", re.I),
     "pc_status", {"what": "cpu"}),
    (re.compile(_BASIC_LEAD + r"(?:(?:what(?:'?s|\s+is)\s+(?:my\s+|the\s+)?)?uptime|how\s+long\s+(?:has|have)\s+(?:my\s+|the\s+|this\s+)?"
                r"(?:pc|laptop|computer|system|i)\s+been\s+(?:on|running|up))\W*$", re.I),
     "pc_status", {"what": "uptime"}),
]
_BASIC.append((re.compile(_BASIC_LEAD + r"(?:hang\s+up|end\s+(?:the\s+)?call|cut\s+(?:the\s+)?call|disconnect(?:\s+the\s+call)?)"
                          r"(?:\s+now)?\W*$", re.I), "end_call", {}))
_VOLUME = re.compile(_BASIC_LEAD + r"(?:set\s+(?:the\s+)?)?volume\s+(?:to\s+)?(\d{1,3})(?:\s*%|\s+percent)?\W*$", re.I)


_ADD_TASK = re.compile(
    _BASIC_LEAD + r"(?:add|put|create|new)\s+(?:a\s+)?(?:task|to-?do)(?:\s+to\s+(?:my\s+)?(?:list|tasks))?\s*[:,-]?\s+(?P<t>.+?)"
    r"(?:\s+(?:for|by|due)\s+(?P<d>today|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday))?\W*$|"
    + _BASIC_LEAD + r"add\s+(?P<t2>.+?)\s+to\s+my\s+(?:tasks|to-?do(?:\s+list)?|list)"
    r"(?:\s+(?:for|by|due)\s+(?P<d2>today|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday))?\W*$", re.I)


def _direct_basic(text: str) -> tuple[str, dict] | None:
    t = text.strip()
    if _SCREEN.match(t):
        return "look_at_screen", {"question": t, "whole_screen": bool(_WHOLE.search(t))}
    m = _ADD_TASK.match(t)
    if m:
        return "add_task", {"text": m.group("t") or m.group("t2"),
                            "due": m.group("d") or m.group("d2") or ""}
    m = _VOLUME.match(t)
    if m:
        return "set_volume", {"level": min(100, int(m.group(1)))}
    for pattern, name, args in _BASIC:
        if pattern.match(t):
            return name, dict(args)
    return None


def split_simple(text: str, strict: bool = False) -> list[tuple[str, dict]] | None:
    """"open spotify and play music" -> two quick tool calls, if EVERY part
    is something the quick handlers know. Otherwise None, and it is the
    planner's. Two plain actions do not need a hosted model, its rate
    limits, or twenty seconds."""
    parts = [p for p in re.split(r"\s*(?:,\s*(?:and\s+)?(?:then\s+)?|\band\s+then\b|\bthen\b|\band\b)\s*",
                                 text.strip(), flags=re.I) if p and p.strip()]
    if len(parts) < 2 or len(parts) > 4:
        return None
    # strict: the sentence was not obviously several requests, so each part
    # must be a command of its own - "play simon and garfunkel" is one song.
    if strict and any(len(p.split()) < 2 for p in parts):
        return None
    calls = []
    for p in parts:
        hit = route(p)
        if hit is None or hit[0] in ("explain", "run_routine"):
            return None
        calls.append(hit)
    return calls


# Places: phone a business, book online, check hours. Contacts said plainly
# ("call mom") still reach _direct_call first and ring on WhatsApp; "on my
# phone" or "dial" means a normal call through Phone Link.
_PL = r"^\W*(?:(?:athena|jarvis|hey|ok|okay|please)\W+)*(?:(?:can|could|would)\s+you\s+)?(?:please\s+)?"
_CALL_PHONE = re.compile(_PL + r"(?:call|phone|ring|dial)\s+(?P<x>.+?)\s+(?:on|from|using)\s+(?:my\s+)?(?:phone|mobile|cell|sim)\W*$|"
                         + _PL + r"(?:dial|make\s+a\s+(?:phone\s+)?call\s+to)\s+(?P<x2>.+?)\W*$|"
                         + _PL + r"(?:call|phone|ring)\s+(?P<x3>(?:\+?\d[\d\s-]{6,}\d))\W*$", re.I)
_CALL_ANY = re.compile(_PL + r"(?:call|phone|ring)\s+(?:up\s+)?(?:the\s+)?(?P<x>.+?)\W*$", re.I)
_BOOK = re.compile(_PL + r"(?:book|reserve|get)\s+(?:me\s+|us\s+)?(?:a\s+|an\s+)?(?P<kind>table|appointment|slot|reservation|booking)"
                   r"\s+(?:at|in|with|for)\s+(?P<place>.+?)(?:\s+for\s+(?P<n>\d+|two|three|four|five|six|seven|eight)(?:\s+(?:people|persons|of\s+us|guests|pax))?)?"
                   r"(?:\s+(?P<when>(?:at|on|for|tonight|today|tomorrow|this|next).*?))?\W*$", re.I)
_PLACE_Q = re.compile(_PL + r"(?:"
                      r"is\s+(?P<a>.+?)\s+open(?:\s+(?:now|today|right\s+now|tonight))?"
                      r"|what\s+time\s+does\s+(?P<b>.+?)\s+(?:open|close)(?:\s+today)?"
                      r"|(?:what\s+(?:is|are)\s+)?(?:the\s+)?(?:opening\s+hours|timings?|phone\s+number|contact\s+number|address)\s+(?:of|for)\s+(?P<c>.+?)"
                      r"|(?P<d>.+?)(?:'s|s)?\s+(?:phone\s+number|contact\s+number|opening\s+hours|timings)"
                      r")\W*$", re.I)
_NUMW = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8}
# Things "call" is followed by that are not a place to phone: "call it a
# day", "call off the meeting", "call back later".
_NOT_PLACE = re.compile(r"^(?:me|my\s+phone|now|everyone)$|^(?:it|this|that|him|her|them|a|an|off|out|back|in|up|for|over)\b", re.I)


def _direct_places(text: str) -> tuple[str, dict] | None:
    t = text.strip()
    m = _BOOK.match(t)
    if m:
        n = m.group("n") or "2"
        people = int(n) if n.isdigit() else _NUMW.get(n.lower(), 2)
        return "book_reservation", {"place": m.group("place"), "when": (m.group("when") or "today").strip(),
                                    "people": people}
    m = _CALL_PHONE.match(t)
    if m:
        who = (m.group("x") or m.group("x2") or m.group("x3")).strip()
        if not _NOT_PLACE.match(who):
            return "call_number", {"who": who}
    m = _PLACE_Q.match(t)
    if m:
        place = next(g for g in (m.group("a"), m.group("b"), m.group("c"), m.group("d")) if g)
        if len(place.split()) <= 6 and not re.match(r"^(?:my|the\s+pc|this|it|that)\b", place, re.I):
            return "place_info", {"place": place, "question": t}
    m = _CALL_ANY.match(t)
    if m and not _NOT_PLACE.match(m.group("x").strip()):
        # Reached only when _direct_call did not know the name as a contact.
        return "call_number", {"who": m.group("x").strip()}
    return None


def _direct_routine(text: str) -> tuple[str, dict] | None:
    """"study mode" / "run study mode" -> run_routine, if one is saved."""
    try:
        from .tools import routines

        name = routines.find(text)
    except Exception:
        return None
    return ("run_routine", {"name": name}) if name else None


# "submit it" / "don't submit" after browser_click held back a final button.
_SUBMIT_YES = re.compile(
    r"^\W*(?:(?:athena|jarvis|ok|okay|yes|yeah|yep|sure|alright)\W+)*"
    r"(?:submit(?:\s+it)?|send\s+it|go\s+ahead|confirm(?:\s+it)?|do\s+it|press\s+it|(?:call|dial|ring)\s+(?:it|them)|"
    r"yes|yeah|yep|place\s+(?:the\s+)?order|pay)(?:\s+(?:now|please))*\W*$", re.I)
_SUBMIT_NO = re.compile(
    r"^\W*(?:(?:athena|jarvis|no|nope)\W+)*(?:no|nope|don'?t(?:\s+submit)?|cancel(?:\s+it)?|"
    r"stop|wait|hold\s+on|not\s+yet)(?:\s+(?:it|now|please))*\W*$", re.I)


# Typed commands arrive with typos the routers cannot see past: "oopen
# scheduler 2" (24 Sep 2026) missed every "open" pattern, reached the model,
# and became open_app("scheduler 2") - Task Scheduler - while "open
# scheduler 2" routes straight to scheduler (2).pdf. Only the leading verb is
# corrected; the rest of the sentence is left for the finder's own fuzziness.
_VERB_TYPO = re.compile(
    r"^(\W*(?:(?:athena|jarvis)\W+)?)(?:o+pe+n+|opne|opn|oepn|opem|oprn|ipen|pen|ope)(?=\s)", re.I)


# "Athena, close Spotify" came back from speech as "close, Spotify" (the
# comma kept every router from matching) and as "Claude, Spotify" (25 Sep
# 2026). "close" before an app name is also heard as "clothes" and "clause".
_CLOSE_HEARD = re.compile(
    r"^(\W*(?:(?:athena|jarvis)\W+)?)(?:claude|clothes|clause|cloze|klose|clos|closed)(?=[\s,.]+\w)", re.I)
_VERB_COMMA = re.compile(
    r"^(\W*(?:(?:athena|jarvis)\W+)?(?:open|close|quit|exit|launch|start|play|pause|kill))\s*[,.:;]+\s*", re.I)


def _fix_verb(text: str) -> str:
    text = _VERB_TYPO.sub(lambda m: m.group(1) + "open", text, count=1)
    text = _CLOSE_HEARD.sub(lambda m: m.group(1) + "close", text, count=1)
    return _VERB_COMMA.sub(lambda m: m.group(1) + " ", text, count=1)


# "open it", "show it in explorer" - the file last opened or shown.
_IT_CMD = re.compile(
    r"^\W*(?:(?:athena|jarvis|hey|ok|okay|please|now|just|and|then|so)\W+)*"
    r"(?:(?:can|could|would|will)\s+you\s+)?(?:please\s+)?"
    r"(?P<verb>open|show|reveal|launch|view)\s+(?:me\s+)?"
    r"(?:it|that|this|them|the\s+same|(?:that|this|the)\s+(?:file|one|document|pdf))"
    r"(?P<exp>\s+in\s+(?:the\s+)?(?:file\s+)?explorer)?"
    r"(?:\s+(?:up|again|now|please))*\W*$", re.I)


def _direct_it(text: str) -> tuple[str, dict] | None:
    """Turn "open it" into opening the file just found, opened or shown."""
    m = _IT_CMD.match(text.strip())
    if not m:
        return None
    from .tools import files

    if files.last_file is None:
        return None             # nothing to point at; let the model ask
    path = str(files.last_file)
    if m.group("exp") or m.group("verb").lower() == "reveal":
        return "reveal_path", {"path": path}
    return "open_file", {"path": path}


# Shutdown, restart, sleep and cancelling them. On 24 Sep 2026 "Shutdown the
# pc" reached _direct_app, which matched "pc" to the WinPcap Web Site
# shortcut and opened it; plain "Shutdown" went to the model, which replied
# "Shutting down the PC." without calling anything. So these are parsed here,
# ahead of every other router. "turn off" and "restart" need the PC named -
# "turn off the lights" and "restart spotify" are not about this machine -
# but "shutdown" and "reboot" on their own can only mean one thing.
_PC = r"(?:the\s+|my\s+|this\s+)?(?:pc|computer|laptop|system|machine|windows)"
_POWER_LEAD = r"^\W*(?:(?:athena|jarvis|hey|ok|okay|please|now|just)\W+)*(?:(?:can|could|would|will)\s+you\s+)?(?:please\s+)?"
_WHEN = (r"(?:\s+(?:in|after)\s+(?P<n>\d+|a|an|one|two|five|ten|fifteen|thirty)\s*"
         r"(?P<unit>sec(?:ond)?s?|min(?:ute)?s?|hours?|hrs?))?"
         r"(?:\s+(?P<now>now|right\s+now|immediately))?")
_TAIL = r"(?:\s+please)?\W*$"
_POWER = [
    ("cancel", re.compile(_POWER_LEAD + r"(?:cancel|abort|stop|don'?t)\s+(?:the\s+)?"
                          r"(?:shut\s*down|restart|reboot|power\s*off)" + _TAIL, re.I)),
    ("shutdown", re.compile(_POWER_LEAD + r"(?:shut\s*down|power\s*off|switch\s+off|turn\s+off)"
                            r"(?:\s+" + _PC + r")?" + _WHEN + _TAIL, re.I)),
    ("restart", re.compile(_POWER_LEAD + r"(?:(?:restart|reboot)\s+" + _PC + r"|reboot)"
                           + _WHEN + _TAIL, re.I)),
    ("sleep", re.compile(_POWER_LEAD + r"(?:(?:put\s+" + _PC + r"\s+to|send\s+" + _PC
                         + r"\s+to)\s+sleep|sleep\s+" + _PC + r"|" + _PC
                         + r"\s+(?:go\s+)?to\s+sleep)" + _TAIL, re.I)),
]
_WORD_NUM = {"a": 1, "an": 1, "one": 1, "two": 2, "five": 5, "ten": 10,
             "fifteen": 15, "thirty": 30}


def _direct_power(text: str) -> tuple[str, dict] | None:
    """Turn "shut down the pc", "restart in 5 minutes", "cancel shutdown"
    into power_action, before any other router can mistake them."""
    t = text.strip()
    for action, pattern in _POWER:
        m = pattern.match(t)
        if not m:
            continue
        # "turn off" / "switch off" with nothing named is ambiguous (the
        # music? the screen?), so only route it when the PC was named.
        if action == "shutdown" and re.search(r"\b(?:turn|switch)\s+off\b", t, re.I) \
                and not re.search(_PC, t, re.I):
            return None
        args: dict = {"action": action}
        if action in ("shutdown", "restart"):
            gd = m.groupdict()
            if gd.get("now"):
                args["delay_seconds"] = 5
            elif gd.get("n"):
                n = gd["n"].lower()
                n = int(n) if n.isdigit() else _WORD_NUM.get(n, 1)
                unit = gd["unit"].lower()
                mult = 3600 if unit.startswith("h") else 60 if unit.startswith("m") else 1
                args["delay_seconds"] = n * mult
        return "power_action", args
    return None


# "call me" / "test call": the user's own phone, not a contact. Anchored at
# both ends and limited to "me"/"my phone" so "call mom" and "call papa on
# whatsapp" still reach _direct_call - and it runs first, because a contact
# saved as "me" would otherwise turn "call me" into a WhatsApp call to
# yourself.
_PHONE_ME = re.compile(
    r"^\W*(?:(?:athena|jarvis|hey|ok|okay|please|now|just)\W+)*"
    r"(?:(?:can|could|would|will)\s+you\s+)?(?:please\s+)?"
    r"(?:"
    r"(?:(?:give\s+me|do|place|make|send\s+me|run)\s+a\s+)?test\s+(?:phone\s+)?call"
    r"|(?:call|ring|phone)\s+(?:me|my\s+(?:phone|mobile|cell))(?:\s+on\s+my\s+(?:phone|mobile|cell))?"
    r"|give\s+me\s+a\s+(?:call|ring)"
    r")"
    r"(?:\s+(?:now|right\s+now))?(?:\s+please)?\W*$",
    re.I,
)


def _direct_phone_me(text: str) -> tuple[str, dict] | None:
    """Turn "call me" / "test call" into a call to the user's own phone."""
    if not _PHONE_ME.match(text.strip()):
        return None
    from .phone import TEST_MESSAGE

    return "call_me", {"message": TEST_MESSAGE}


def _direct_call(text: str) -> tuple[str, dict] | None:
    """Turn 'call <contact>' into a whatsapp_call, when the name is known."""
    from .tools.messaging import parse_call_request

    parsed = parse_call_request(text)
    if parsed is None:
        return None
    return "whatsapp_call", {"to": parsed[0], "video": parsed[1]}


def _direct_message(text: str) -> tuple[str, dict] | None:
    """Turn an obvious WhatsApp request straight into a whatsapp_message call.

    Safe to route: the tool opens the chat with the text typed and never
    sends. A wrong guess costs a window, not a sent message.
    """
    from .tools.messaging import parse_request

    parsed = parse_request(text)
    if parsed is None:
        return None
    return "whatsapp_message", {"to": parsed[0], "text": parsed[1]}


def _direct_reminder(text: str) -> tuple[str, dict] | None:
    """Turn an obvious reminder request straight into an add_reminder call."""
    from .tools.scheduler import parse_request

    parsed = parse_request(text)
    if parsed is None:
        return None
    return "add_reminder", {"text": parsed[0], "when": parsed[1]}


# Some claims contain no verb at all: "Media playback is now next."
_ACTION_PHRASES = re.compile(
    "is now (next|previous|playing|paused|muted|on|off|open|closed)"
    "|moved to the (next|previous)"
    "|now playing|now paused",
    re.IGNORECASE,
)


def _claims_action(answer: str) -> bool:
    """True if the reply asserts a real-world change was made."""
    if not answer:
        return False
    if _NOT_A_CLAIM.search(answer):
        return False
    return bool(_ACTION_CLAIMS.search(answer) or _ACTION_PHRASES.search(answer))


def _truncate(text: str, limit: int = 4000) -> str:
    """Keep one tool result from eating the whole context window."""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [truncated, {len(text) - limit} more characters]"

"""The agent loop: prompt -> model -> tool calls -> results -> answer."""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Callable

from . import offer, planner, registry
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

Work like a partner, not a vending machine (13 Oct 2026):
- Keep track of what the user is working on in this conversation - the
  assignment, the bug, the plan - and build on it. "Do the next one" or "now
  make it shorter" refers to that work; don't make them repeat it.
- When a piece of work is done and there is an obvious next step, offer it in
  one short line ("Want me to send it to your phone?"). Don't offer anything
  when there's no real next step.
- Say what you think when it helps: if their approach has a problem, or there
  is a clearly better way, say so briefly and why. Be honest when you're unsure.
- If something is unclear, ask one short question instead of guessing.
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
Your punctuation sets your tone of voice: good news ends with "!" ("Done! It's
open.", "Nice, all three classes are done!"), a question ends with "?", and bad
news is a plain, gentle sentence. Don't put "!" on everything - only where you'd
genuinely sound pleased.
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
        if llm._cloud():
            llm = llm._local()          # keep the local fallback warm instead
        loaded = [m.model for m in llm.client.ps().models]
        if not load and not any(m.split(":")[0] == str(llm.model).split(":")[0]
                                and m.endswith(str(llm.model).split(":")[-1]) for m in loaded):
            return
        prompt = SYSTEM_PROMPT.format(name=cfg.get("assistant.name", "Athena"))
        if voice:
            prompt += VOICE_STYLE
        from . import modes
        system = Memory(prompt + modes.prompt()).build()[0]
        llm.chat([system, {"role": "user", "content": "hi"}],
                 tools=registry.schemas(""), max_tokens=1)
    except Exception as e:                      # warming is a nicety, never an error
        print(f"[warm: {e}]")


def _new_recipient(args: dict) -> bool:
    """A message or call going to a number that is not one of your contacts -
    or, for call_number, to anyone who is not (a business, a raw number)."""
    from .tools import messaging
    to = str(args.get("to") or args.get("who") or "").strip()
    if not to:
        return False
    digits = messaging._normalise_phone(to)
    if len(digits) >= 10:
        return not any(messaging._normalise_phone(c.get("phone", ""))[-10:] == digits[-10:]
                       for c in messaging._load().values())
    return messaging._find_contact(to) is None


class Agent:
    def __init__(self, on_step: Callable[[Step], None] | None = None,
                 confirm: Callable[[str, dict], bool] | None = None,
                 voice: bool = False):
        self.llm = LLM()
        registry.warm_semantic()           # tool descriptions embedded in the background, for picking by meaning
        prompt = SYSTEM_PROMPT.format(name=cfg.get("assistant.name", "Athena"))
        if voice:
            prompt += VOICE_STYLE
        self._base_prompt = prompt
        self.memory = Memory(prompt)
        # UI hooks: show progress, and ask before destructive actions
        self._last_answer = ""
        self.on_step = on_step or (lambda s: None)
        # No dialog to ask in (the phone/web chat, rules): offer it instead,
        # and "yes" as the next message does it (offer.py).
        self.confirm = confirm or self._offer_instead
        self._approved: set[str] = set()     # this request's yes and no answers
        self._refused: set[str] = set()
        self._untrusted_until = 0.0
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

    def _needs_confirm(self, tool: registry.Tool, args: dict | None = None) -> bool:
        """Risk tiers (9 Oct 2026 audit):

          critical  every time: safety.ask_first (PowerShell, deleting) and
                    safety.always_confirm_tags (the RAGAZZA shop - an
                    unconfirmed "set stock to 50" looped six times, 27 Sep 2026)
          high      a message or call to someone not in your contacts; and
                    anything in registry.OUTWARD while a web page, email, file
                    or another app's text is in the conversation - it may
                    carry instructions written for Athena (prompt injection)
          low       everything else just runs, as before (confirm_destructive off)
        """
        if tool.name in (cfg.get("safety.ask_first", []) or []) or                 set(tool.tags) & set(cfg.get("safety.always_confirm_tags", []) or []):
            return True
        if tool.name in registry.OUTWARD and time.time() < self._untrusted_until:
            return True
        if tool.name in ("whatsapp_message", "whatsapp_call", "call_number") and _new_recipient(args or {}):
            return True
        return bool(tool.destructive and cfg.get("safety.confirm_destructive", True))

    def allowed(self, tool: registry.Tool, args: dict) -> bool:
        """Ask if this call needs it - once per request: the router asking
        and then Tool.run asking again would be the same question twice."""
        getattr(self, "_ran_tools", set()).add(tool.name)
        key = tool.name + json.dumps(args, sort_keys=True, default=str)
        if key in self._approved or not self._needs_confirm(tool, args):
            return True
        if key in self._refused:
            return False
        # On the HUD's live feed: what is waiting for your OK, and what was refused.
        self.on_step(Step(tool=f"permission: {tool.name}", args=args, result="asked"))
        ok = bool(self.confirm(tool.name, args))
        (self._approved if ok else self._refused).add(key)
        if not ok:
            self.on_step(Step(tool=f"blocked: {tool.name}", args=args, result="not done"))
        return ok

    def saw_untrusted(self) -> None:
        """Text someone else wrote is now in the conversation; it stays in the
        history for a while, so outward actions ask for the next 10 minutes."""
        self._untrusted_until = time.time() + float(cfg.get("safety.untrusted_minutes", 10)) * 60

    def _offer_instead(self, name: str, args: dict) -> bool:
        from . import offer
        from .habits import _label
        offer.set_choices([offer.Choice(_label(name, args), name, args)])
        return False

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
        from . import offer
        from .habits import _label
        note = "Not done: waiting for the user's approval."
        self.on_step(Step(tool=name, args=args, result=note))
        self.memory.add_raw({"role": "tool", "name": name, "content": note})  # every tool call gets its result
        answer = (f"That needs your OK first: {_label(name, args)}. Say yes to go ahead." if offer.pending()
                  else "I haven't done that - it needs your OK first.")
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
        # Just her name (heard unsure, inside a follow-up window): "Athena" once
        # opened the Athena app - "already open, brought it to the front" (16 Oct 2026).
        if re.fullmatch(r"\W*(?:hey\s+|ok\s+|okay\s+)?(?:athena|jarvis)\W*", user_input or "", re.I):
            return "Yes?"
        started = time.time()
        self._approved, self._refused = set(), set()
        from . import modes
        self.memory.system_prompt = self._base_prompt + modes.prompt()    # study / coding / research ...
        registry._guard.agent = self       # Tool.run checks permissions with this request
        # Privacy (15 Oct 2026): messages, contacts, calls and email are handled
        # by the model on this PC, and marked so no later cloud request carries them.
        turn_from, saved_llm = len(self.memory.full), self.llm
        self._ran_tools: set[str] = set()
        from .memory import private_words
        mentions = set(re.findall(r"[a-z]{3,}", user_input.lower())) & private_words()
        # Messages, contacts, calls, email - or a person or thing from your
        # personal memories ("what's Riya's other name?").
        private = bool(_PRIVATE.search(user_input) or mentions) and cfg.get("privacy.local_for_messages", True)
        self._turn_from = turn_from
        self.memory.private_turn = private
        hosted = lambda m: bool(getattr(m, "_cloud", lambda: False)())     # noqa: E731
        if private and hosted(self.llm):
            self.llm = self.llm._local()
            while hosted(self.llm):                # past any hosted fallbacks, to the one on this PC
                self.llm = self.llm._local()
        from . import deadline
        deadline.start(float(cfg.get("llm.budget_voice_seconds" if self.speak else "llm.budget_typed_seconds",
                                     35 if self.speak else 75)))
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
        finally:
            registry._guard.agent = None
            deadline.clear()
            if private or (self._ran_tools & PRIVATE_TOOLS):
                self.memory.mark_private(turn_from)
            self.memory.private_turn = False
            self.llm = saved_llm
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

        # A quiz is running (quiz.py): what you say is your answer.
        from . import quiz
        marked = quiz.handle(user_input)
        if marked is not None:
            self.memory.add("user", user_input)
            self._last_answer = marked
            self.memory.add("assistant", marked)
            return marked

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
                    self._approved.add(c.tool + json.dumps(c.args, sort_keys=True, default=str))   # "yes" was the OK
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
        multi = (cfg.get("planner.enabled", True) and not self.memory.private_turn
                 and planner.is_multistep(user_input))
        # A message with no one to send it to: ask, never guess. "Send a high
        # message on WhatsApp through my phone" went to the model, which picked
        # a chat and sent "High message sent." (4 Oct 2026).
        pend = getattr(self, "_pending_msg", None)
        self._pending_msg = None
        if pend and offer.NO.match(user_input):
            answer = "Okay, I won't send it."
            self._last_answer = answer
            self.memory.add("assistant", answer)
            return answer
        if pend and time.time() - pend[1] < 90 and len(user_input.split()) <= 4:
            who = _mentioned_contact(user_input) or user_input.strip(" .?!,")
            follow = ("whatsapp_message", {"to": who, "text": pend[0]})
        else:
            m = _NO_RECIPIENT.match(user_input)
            if m and route(user_input) is None and not _mentioned_contact(user_input):
                body = m.group("b").strip()
                body = "hi" if body.lower() in ("high", "hai", "hay") else body
                self._pending_msg = (body, time.time())
                answer = f"Who should I send \"{body}\" to?"
                self._last_answer = answer
                self.memory.add("assistant", answer)
                return answer
            # "and tomorrow?" / "what about DSA?" / "open it" right after a command
            # carries on from it, instead of starting from nothing.
            follow = self._follow_up(user_input)
        if follow is not None:
            multi = False
        direct = follow or (None if multi else route(user_input))
        # In a project whose files cover the question, a look-up shortcut steps
        # aside and the model answers with the files in front of it (and the
        # tools still to hand): "when is my Unit 3 quiz" went to the LMS list
        # while the syllabus in the project had the date (14 Oct 2026).
        # A project with files is a study room: questions there are class work,
        # answered from the files in the chat (14 Oct 2026: "how many units in
        # my course plan" got the daily brief). Commands ("open spotify",
        # "remind me ...") still go to their shortcuts.
        from . import chats
        proj = chats.ACTIVE
        if proj and not follow and not self.memory.private_turn and chats.project_files(proj["id"]) and (
                (direct and direct[0] in _LOOKUPS and (direct[0] == "explain" or chats.covers(proj["id"], user_input)))
                or (not direct and _QUESTION.match(user_input))):
            from .tools import study
            self.on_step(Step(tool="project_files", args={"project": proj["name"]}, result="answering from the project's files"))
            try:
                answer = study.project_answer(user_input, self.memory.full[:-1], proj)
            except Exception as e:
                answer = ""
                print(f"[project] {e}")
                from . import deadline
                if deadline.expired():
                    answer = deadline.BUSY
            if answer:
                try:
                    from . import goals
                    ticked = goals.complete_pending(user_input)
                    answer += f"\n\n{ticked}" if ticked else ""
                except Exception:
                    pass
                self.memory.add("assistant", answer)
                self._last_answer = answer
                return answer
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
            if not self.allowed(tool, args):
                answer = self._declined(name, args)
            else:
                # Tools that take half a minute: say so first, or the silence
                # sounds like Athena never heard you.
                if name in _SLOW_TOOLS:
                    self._acknowledge(_SLOW_TOOLS[name])
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
        shown += registry.by_meaning(user_input, shown)     # a few nearest by meaning, after the fixed prefix
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
        # The last tool this turn failed: its "done" is never passed on (14 Oct
        # 2026: the phone was unreachable and the reply was "already open").
        failed = [""]
        for _ in range(max_steps):
            if self.cancelled:                  # Stop pressed in the chat (POST /ask/stop)
                return "(stopped)"
            from . import deadline
            if deadline.expired():              # every model slow or failing: say so, don't keep waiting
                self._trouble.append("out of time")
                last = next((str(m.get("content", "")) for m in reversed(self.memory.messages) if m.get("role") == "tool"), "")
                # "ERROR: no such tool 'list_meetings'. Available: ..." was read aloud (16 Oct 2026)
                if not used_tools or not last.strip() or re.match(r"\s*(?:ERROR|Error|error:|Traceback|[\[{])", last) \
                        or "no such tool" in last:
                    answer = deadline.BUSY
                else:
                    answer = "That took too long to finish - here's where it got to: " + \
                        re.split(r"(?<=[.!?])\s", last.strip(), maxsplit=1)[0][:200]
                self.memory.add("assistant", answer)
                self._last_answer = answer
                return answer
            if self.speak is None:
                msg = self.llm.chat(self.memory.build(), tools=tools)
                if self.cancelled:
                    return "(stopped)"
            else:
                blocked = [False]

                def out(sentence: str, used=used_tools, wide=widened) -> None:
                    # The same guards as below, applied before the sentence is
                    # said: an unbacked "Screen locked." is never spoken, nor
                    # an "I can't" that is about to be retried with every tool.
                    if self.cancelled:
                        return
                    if failed[0] and _claims_success(sentence):
                        blocked[0] = True
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
                if failed[0] and _claims_success(answer):
                    answer = "That didn't work - " + _why(failed[0])

                # The model sometimes writes the call out as text rather than
                # calling it. Run it instead of reading JSON aloud.
                written = _parse_text_tool_call(answer)
                if written is not None:
                    name, args = written
                    t = registry.get(name)
                    if not self.allowed(t, args):
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
                elif not self.allowed(t, args):
                    return self._declined(name, args)
                else:
                    result = t.run(**args)

                result = _truncate(result)
                failed[0] = result if result.startswith("ERROR") else ""
                done_calls[key] = result
                self.on_step(Step(tool=name, args=args, result=result))
                self.memory.add_raw({"role": "tool", "name": name, "content": result})
                # A cloud brain that looked up contacts, memories or messages
                # mid-request: the rest of this request runs on this PC, so the
                # result isn't sent back out (15 Oct 2026).
                if name in PRIVATE_TOOLS | {"recall"} and not self.memory.private_turn:
                    self.memory.private_turn = True
                    self.memory.mark_private(getattr(self, "_turn_from", len(self.memory.full)))
                    hosted = lambda m: bool(getattr(m, "_cloud", lambda: False)())     # noqa: E731
                    while hosted(self.llm):
                        self.llm = self.llm._local()

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
                or not cfg.get("planner.rescue", True) or self.memory.private_turn):
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


from .routes import (  # noqa: E402 - routing lives in routes.py since 16 Oct 2026
    PRIVATE_TOOLS, _CANT, _FILE_TOOLS, _FOLLOW, _LOOKUPS, _NO_RECIPIENT,
    _OFFERED, _OPEN_IT, _OPEN_WHATSAPP, _PRIVATE, _QUESTION, _SLOW_TOOLS,
    _SUBMIT_NO, _SUBMIT_YES, _UNLOCK, _claims_action, _claims_success, _is_greeting,
    _is_vague, _mentioned_contact, _parse_text_tool_call, _pick_line, _truncate, _why,
    clean_speech, route, split_simple,
)


def __getattr__(name: str):
    """agent.<name> for what moved to routes.py - tests and older imports use it."""
    from . import routes
    try:
        return getattr(routes, name)
    except AttributeError:
        raise AttributeError(f"module 'jarvis.agent' has no attribute '{name}'") from None

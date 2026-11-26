"""The agent loop: prompt -> model -> tool calls -> results -> answer.

Split on 31 Oct 2026: the prompt text and Step are in agent_defs.py, asking first
(Guardrails, which Agent inherits) in agent_safety.py, and the shortcuts in
routes*.py - all importable from here as before.
"""
from __future__ import annotations

import json
import re
import time
from typing import Callable

from . import offer, planner, registry
from .config import cfg
from .llm import LLM
from .memory import Memory
from .agent_defs import SYSTEM_PROMPT, Step, VOICE_STYLE  # noqa: F401 - split out 31 Oct 2026; Step is agent.Step to the UIs
from .agent_safety import Guardrails, _OPEN_ASK, _new_recipient, opens_asked  # noqa: F401 - split out 31 Oct 2026


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


class Agent(Guardrails):
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
        self._opens_left = opens_asked(user_input)
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
                self._opens_left = len(picked)         # "yes" to "want me to open it?" asked for these
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
        # "Save it as morning setup": the job just worked out, under your own name for it.
        named = _SAVE_SKILL.match(user_input or "")
        skill = getattr(self, "_last_skill", None)
        if named and skill and time.time() - skill[1] < 600:
            from .tools import routines
            answer = routines.save(named.group("n").strip(" .!?\"'"), skill[0], "from a job she worked out")
            answer = answer.removeprefix("ERROR:").strip()
            self._last_skill = None
            self.memory.add("user", user_input)
            self.memory.add("assistant", answer)
            self._last_answer = answer
            return answer
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
        done: list[tuple[str, dict, str]] = []

        def step(name, args, result):
            done.append((name, args, result))
            self.on_step(Step(tool=name, args=args, result=result))
            self.memory.add_raw({"role": "tool", "name": name, "content": _truncate(result)})
        try:
            answer = planner.run(request, on_step=step)
        except planner.Stuck:
            return None
        except Exception as e:
            print(f"[planner failed: {e}]")
            return None
        answer = (answer + " " + self._offer_skill(request, done)).strip()
        self._last_answer = answer
        self.memory.add("assistant", answer)
        return answer

    def _offer_skill(self, request: str, done: list) -> str:
        """A new multi-step job worked out: offer to keep it as a skill, run by name next
        time with no model at all (28 Oct 2026, Hermes Agent's learning loop)."""
        from .tools import routines
        plan = routines.skill_steps(done)
        if not plan or self.memory.private_turn or routines.find(request):
            return ""
        name = routines.unique_name(routines.suggest_name(request))
        self._last_skill = (plan, time.time())
        offer.set_choices([offer.Choice(f'the skill "{name}"', "save_skill", {"name": name, "steps": plan})])
        return f'Want me to keep that as a skill? Then just say "{name}" next time.'

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


# "save it as morning setup", "keep that as a skill called study start" - right after a job
_SAVE_SKILL = re.compile(r"^\W*(?:(?:athena|jarvis|ok|okay|yes|yeah)\W+)*(?:save|keep|store)\s+(?:that|it|this)"
                         r"(?:\s+job)?\s+as\s+(?:a\s+)?(?:(?:skill|routine)\s+)?(?:(?:called|named)\s+)?(?P<n>[\w' -]{2,40}?)\W*$",
                         re.I)

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

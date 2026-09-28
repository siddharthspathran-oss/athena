"""What a phone actually asks, and where each request should end up.

The phone goes through the same Agent as everything else, so these are really
tests of the routers - but the phone is where getting it wrong hurts most.
Nobody minds a needless browser tab on the PC they are sitting at; being made
to wait half a minute on a phone for "what is 2 plus 2" is another matter, and
that is what happened once study.model became a model that reasons before
answering.

    python tests/mobiletest.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis import render                                      # noqa: E402
from jarvis.agent import (_SUBMIT_NO, _SUBMIT_YES, _UNLOCK,  # noqa: E402
                          _direct_brief, _direct_explain, _direct_it,
                          _direct_power, _fix_verb)
from jarvis.tools.study import _lead_line, _speakable, _thinking  # noqa: E402

# Whether a question is worth the reasoning model's deliberation. Keyed on
# there being something to work out - an earlier version keyed on topic words
# including "probability", so every question on a probability course
# deliberated, and "state the definitions" took 266 seconds.
THINKING = [
    ("State the classical and empirical definitions of probability.", False),
    ("explain kirchhoff voltage law", False),
    ("compare BJT and MOSFET", False),
    ("describe the binomial distribution", False),
    ("what is a probability density function", False),
    ("solve x^2 - 5x + 6 = 0", True),
    ("calculate the mean of the distribution", True),
    ("determine the probability that a packet violates the guarantee", True),
    ("find the value of c", True),
    ("how many ways can 5 books be arranged", True),
    ("Fit the binomial distribution and calculate the expected frequencies", True),
]

# "what's my day" and friends. Whisper drops apostrophes, so both spellings
# have to work, and none of it may swallow a class question.
BRIEFING = [
    ("what is my day", "today"),
    ("whats my schedule", "today"),
    ("what is my timetable", "today"),
    ("brief me", "today"),
    ("what classes do i have", "today"),
    ("am i free", "today"),
    ("whats my day tomorrow", "tomorrow"),
    ("what is on tomorrow", "tomorrow"),
    ("whats my calendar tomorrow", "tomorrow"),
    ("explain kirchhoff voltage law", None),
    ("what is my battery level", None),
    ("what is my pc doing", None),
    ("what is a probability density function", None),
]

# (question, the tool it should route to, or None for "just answer it")
ROUTING = [
    # Quick questions: answered directly, no page, no reasoning model.
    ("what is 2 plus 2", None),
    ("what is 15 percent of 200", None),
    ("what is 100 divided by 4", None),
    ("athena what is 7 times 8", None),
    ("calculate 12 plus 30", None),

    # Real class questions: worth the explainer and the page.
    ("explain kirchhoff voltage law", "explain"),
    ("what is the difference between BJT and MOSFET", "explain"),
    ("derive the quadratic formula", "explain"),
    ("solve x^2 - 5x + 6 = 0", "explain"),
    ("what is a probability density function", "explain"),
    ("compute the integral of x squared dx", "explain"),

    # Things on this PC: never the explainer.
    ("what is my battery level", None),
    ("what is the time", None),
    ("what is playing on spotify", None),
    ("what is my wifi password", None),

    # Asking what the machine itself is up to. "what is my pc doing" used to
    # reach the explainer and come back with an essay on what computers do.
    ("what is my pc doing", None),
    ("what is my computer doing", None),
    ("what is my laptop doing right now", None),
    ("what tasks are running", None),
    ("what processes are running on my pc", None),
    ("what apps are open right now", None),
    ("what is running in the background", None),
    ("what is my pc using the most", None),

    # ...but the same words in a class question still go to the explainer.
    ("explain the poisson process", "explain"),
    ("what is cache memory", "explain"),
    ("explain task scheduling in an rtos", "explain"),
    ("what is a distributed system", "explain"),
]

# Power commands: (said, action or None, delay or None). On 24 Sep 2026
# "Shutdown the pc" opened the WinPcap Web Site and "Shutdown" got a spoken
# "Shutting down the PC." with nothing run.
POWER = [
    ("Shutdown the pc", "shutdown", None),
    ("Shutdown", "shutdown", None),
    ("Athena shutdown", "shutdown", None),
    ("athena shut down the computer", "shutdown", None),
    ("shut down my laptop in 5 minutes", "shutdown", 300),
    ("shutdown now", "shutdown", 5),
    ("turn off the pc", "shutdown", None),
    ("restart the pc", "restart", None),
    ("reboot", "restart", None),
    ("put the pc to sleep", "sleep", None),
    ("cancel shutdown", "cancel", None),
    ("turn off the music", None, None),
    ("turn off", None, None),
    ("restart spotify", None, None),
    ("what is shutdown in windows", None, None),
    ("sleep", None, None),
]

# Typo'd verbs: "oopen scheduler 2" slipped past every router on 24 Sep 2026.
VERB_TYPOS = [
    ("oopen scheduler 2", "open scheduler 2"),
    ("opne scheduler 2", "open scheduler 2"),
    ("athena oopen spotify", "athena open spotify"),
    ("opening hours", "opening hours"),
    ("open it", "open it"),
]

# "open it" after a file was shown: (said, tool or None). "open it" once
# became open_app("it") and launched Git CMD.
OPEN_IT = [
    ("open it", "open_file"),
    ("athena open that file", "open_file"),
    ("open it again", "open_file"),
    ("show it in explorer", "reveal_path"),
    ("open itunes", None),
    ("open this pc", None),
]

# Asking Athena to unlock Windows gets a plain "can't" instead of the model's
# "no tool ran". (said, is it an unlock request)
UNLOCK = [
    ("Athena, unlock.", True),
    ("unlock my laptop", True),
    ("log me in", True),
    ("unlock the door", False),
    ("unlock achievements in steam", False),
    ("lock my screen", False),
]

# Browser: fields Athena must never type into, and what counts as the user
# saying "submit it" / "don't". (item or phrase, expected)
SECRET_FIELDS = [
    ({"type": "password", "label": "Password"}, True),
    ({"type": "text", "label": "Enter OTP"}, True),
    ({"type": "text", "label": "Card number"}, True),
    ({"type": "tel", "label": "CVV"}, True),
    ({"type": "text", "label": "Name", "autocomplete": "cc-name"}, True),
    ({"type": "text", "label": "Full name"}, False),
    ({"type": "email", "label": "E-mail address"}, False),
]
SUBMIT_WORDS = [
    ("call it", "yes"), ("dial it", "yes"),
    ("submit it", "yes"), ("yes", "yes"), ("ok send it", "yes"), ("go ahead", "yes"),
    ("don't submit", "no"), ("cancel", "no"), ("not yet", "no"),
    ("submit my assignment tomorrow", None), ("open spotify", None),
]
# Which requests go to the planner (several actions) rather than a router or
# the local model. (said, multi-step?)
MULTISTEP = [
    ("find the scheduler pdf and send it to Rahul on whatsapp", True),
    ("open spotify and play believer", True),
    ("take a screenshot then open downloads", True),
    ("check my battery and tell me what classes I have tomorrow", True),
    ("open the calculator and then use its buttons to work out 12 times 9", True),
    ("open downloads", False),
    ("what is my battery", False),
    ("explain the difference between bjt and mosfet", False),
    ("play believer by imagine dragons", False),
    ("remind me to call mom at 5", False),
]
# Memory: what the profile refuses to keep, and the controls' routing.
PROFILE_REFUSES = [
    ("my wifi password is hunter22", True),
    ("my card is 4111 1111 1111 1111", True),
    ("my OTP is 482913", True),
    ("Neha's email is neha@example.com", True),
    ("Siddharth", True),
    ("I prefer answers with every step shown", False),
    ("Rahul is my classmate in MATH2065", False),
]
MEMORY_ROUTES = [
    ("what do you remember about me", "what_you_remember"),
    ("athena what do you know about me", "what_you_remember"),
    ("forget that I like lofi", "forget_about"),
    ("remember that Rahul is my classmate", "remember_about_me"),
    ("remind me to call mom at 5", None),
    ("what did we decide about the butterworth filter", "search_past_conversations"),
    ("when did I ask you about experiment 6.1", "search_past_conversations"),
    ("did we ever talk about whatsapp calls", "search_past_conversations"),
    ("remind me what we decided about shutdown", "search_past_conversations"),
    ("learn my writing style", "learn_my_writing_style"),
    ("what did you say", None),
]
# Which of the user's messages count as samples of how they write.
# (message, counts as writing?)
WRITING_SAMPLES = [
    ("open spotify and play believer by imagine dragons", False),
    ("athena what classes do I have tomorrow morning", False),
    ("You said that was done, but you did not call any tool, so nothing happened.", False),
    ("bro I'll be late today, start without me and save me a seat", True),
    ("Sir, I wanted to ask if the assignment deadline could be extended", True),
]
# Desktop app buttons that wait for "submit it". (label, held?)
APP_FINAL = [
    ("Send", True), ("Delete", True), ("Uninstall", True), ("Pay now", True),
    ("Empty Recycle Bin", True), ("Sign out", True),
    ("Don't save", False), ("Seven", False), ("Equals", False), ("Open", False),
    ("Settings", False),
]
# Everyday commands that must never reach the model or the planner.
# (said, tool or None)
BASIC = [
    ("play music", "music"), ("pause the music", "music"), ("next song", "music"),
    ("set volume to 30", "set_volume"), ("take a screenshot", "take_screenshot"),
    ("lock the screen", "lock_screen"), ("play believer", "play_song"),
    # How "close Spotify" actually came back from speech on 25 Sep 2026.
    ("Athena, close, Spotify.", "close_app"), ("Claude, Spotify.", "close_app"),
    ("close, Spotify.", "close_app"), ("open claude", "open_app"),
    ("Claude is great", None),
    # The Command Center's menu items, answered without the model.
    ("list my routines", "list_routines"), ("list my reminders", "list_reminders"),
    ("what tools do you have", "list_skills"), ("what is my pc doing", "system_report"),
    ("what are my tasks", "list_tasks"), ("what do i need to do today", "list_tasks"),
    # Screen awareness
    ("what's on my screen", "look_at_screen"), ("what am I looking at", "look_at_screen"),
    ("explain this error", "look_at_screen"), ("summarise this page", "look_at_screen"),
    ("athena read what's on my screen", "look_at_screen"), ("help me with this", "look_at_screen"),
    ("look at my whole screen", "look_at_screen"),
    # Places: phone, book, look up
    ("hang up", "end_call"), ("athena end the call", "end_call"),
    # How "call maa / mom on my phone" really came back from speech (25 Sep 2026)
    ("Athena, call mom on my phone.  and", "call_number"),
    ("Athena, call maa on phone.  And we'll see you in the next one.", "call_number"),
    ("call dominos", "call_number"), ("dial 0135 2760973", "call_number"),
    ("call papa on my phone", "call_number"), ("call papa", "whatsapp_call"),
    ("call it a day", None), ("call off the meeting", None),
    ("book a table at Barbeque Nation for 4 at 8 pm tonight", "book_reservation"),
    ("is dominos open now", "place_info"), ("what time does doon hospital close", "place_info"),
    ("explain kirchhoff voltage law", "explain"), ("read my email", None),
    ("add a task finish the DSD lab record", "add_task"), ("add submit assignment to my tasks for friday", "add_task"),
    ("what's my full schedule today", "daily_brief"), ("what do you remember about me", "what_you_remember"),
]
# Simple combos split into quick calls; real multi-step tasks stay with the
# planner. (said, number of quick calls or None)
SIMPLE_SPLIT = [
    ("open Spotify and play music", 2),
    ("open spotify and then play believer", 2),
    ("take a screenshot and open downloads", 2),
    ("find the scheduler pdf and send it to Rahul", None),
    ("check my battery and tell me what classes I have tomorrow", None),
]
# Today's brief covers what is left of the day; "full"/"whole" asks for all.
# (said, full?)
BRIEF_FULL = [
    ("what is my schedule", False), ("brief me", False),
    ("what's my full schedule today", True), ("whats my whole day", True),
    ("what classes do I have tomorrow", False),
    ("Athena, breathe me.", False),
]
ROUTINES = [
    ("study mode", "study mode"), ("run study mode", "study mode"),
    ("athena good night", "good night"), ("open spotify", None),
]

# (markdown in, what must appear in the rendered HTML)
RENDERING = [
    (r"The result of $ 2 + 2 $ is $ 4 $.", r"\(2 + 2\)"),
    (r"$$ 2 + 2 = 4 $$", r"$$ 2 + 2 = 4 $$"),          # display maths untouched
    (r"Already correct: \( a^2 \)", r"\( a^2 \)"),      # other style untouched
    (r"It costs $5 and change.", "$5 and change"),      # lone dollar untouched
    (r"Use `$PATH` in the shell.", "$PATH"),            # code untouched
]


# The one line the phone shows in the chat bubble, and the one line spoken
# aloud. MathJax never sees it, so the LaTeX has to come out here.
SPEAKABLE = [
    (r"the resistance: $ V = I \cdot R $.", "the resistance: V = I times R."),
    (r"\( c \) is \( \frac{1}{3} \)", "c is 1 over 3"),
    (r"$$ E = mc^2 $$", "E = mc to the power 2"),
    (r"**bold** stays", "bold stays"),
    ("plain english with no maths", "plain english with no maths"),
]

# The spoken line for a calculated answer comes from its closing "Final
# answer:" line: the opening sentence no longer states the result, because a
# model asked to state it first guessed (question 5 opened with 1024/1025,
# then worked its way to 0.6993).
LEAD = [
    ("We find c from the sum of the pmf.\n\nworking\n\n**Final answer:** "
     r"\( c = \frac{1}{3} \)", "Final answer: c = 1 over 3"),
    ("Ohm's law relates voltage and current.\n\n## Details", "Ohm's law relates"),
]


def main() -> int:
    failures = 0

    for question, want in ROUTING:
        got = _direct_explain(question)
        name = got[0] if got else None
        ok = name == want
        failures += not ok
        print(f"{'ok  ' if ok else 'FAIL'} {question:48} -> "
              f"{name or 'answered directly'}"
              f"{'' if ok else f'   (wanted {want or 'answered directly'})'}")

    for said, want, delay in POWER:
        got = _direct_power(said)
        action = got[1]["action"] if got else None
        wait = got[1].get("delay_seconds") if got else None
        ok = action == want and wait == delay
        failures += not ok
        print(f"{'ok  ' if ok else 'FAIL'} power  {said:46} -> "
              f"{action or 'not a power command'}{f' in {wait}s' if wait else ''}")

    from jarvis.tools import browser, routines
    for item, want in SECRET_FIELDS:
        got = browser._is_secret(item)
        ok = got == want
        failures += not ok
        print(f"{'ok  ' if ok else 'FAIL'} secret {item['label']:46} -> "
              f"{'never typed' if got else 'may be filled'}")
    for said, want in SUBMIT_WORDS:
        got = ("yes" if _SUBMIT_YES.match(said) else "no" if _SUBMIT_NO.match(said) else None)
        ok = got == want
        failures += not ok
        print(f"{'ok  ' if ok else 'FAIL'} submit {said:46} -> {got}")
    import tempfile
    from jarvis import profile
    from jarvis.agent import _direct_memory
    real = profile.PROFILE
    profile.PROFILE = Path(tempfile.mkdtemp()) / "profile.md"   # never the real one
    for text, refused in PROFILE_REFUSES:
        got = profile.add(text) is not None
        ok = got == refused
        failures += not ok
        print(f"{'ok  ' if ok else 'FAIL'} memory {text[:46]:46} -> {'refused' if got else 'kept'}")
    profile.PROFILE = real
    for said, want in MEMORY_ROUTES:
        got = _direct_memory(said)
        ok = (got[0] if got else None) == want
        failures += not ok
        print(f"{'ok  ' if ok else 'FAIL'} memctl {said:46} -> {got[0] if got else None}")

    from jarvis import recall
    for text, want in WRITING_SAMPLES:
        got = (len(text.split()) >= 6 and not recall._COMMAND.match(text)
               and not recall.is_nudge(text))
        ok = got == want
        failures += not ok
        print(f"{'ok  ' if ok else 'FAIL'} style  {text[:46]:46} -> "
              f"{'writing sample' if got else 'not writing'}")

    from jarvis.agent import route, split_simple
    for said, want in BASIC:
        got = route(said)
        ok = (got[0] if got else None) == want
        failures += not ok
        print(f"{'ok  ' if ok else 'FAIL'} basic  {said:46} -> {got[0] if got else None}")
    # Every tool a router can hand back must exist - a misplaced @tool left
    # "brief me" pointing at nothing, and it crashed (25 Sep 2026).
    import re as _re
    from jarvis import registry, tools  # noqa: F401
    src = open(Path(__file__).resolve().parent.parent / "jarvis" / "agent.py", encoding="utf-8").read()
    named = set(_re.findall(r'return\s+\(?"([a-z_]+)",\s*\{', src)) | set(
        _re.findall(r'\),\s*"([a-z_]+)",\s*\{', src))
    missing = sorted(n for n in named if registry.get(n) is None)
    ok = not missing and len(named) > 10
    failures += not ok
    print(f"{'ok  ' if ok else 'FAIL'} tools  {len(named)} router tool names all registered"
          + (f" - missing: {missing}" if missing else ""))
    ROUTER_CHECKS = 1

    for said, want in BRIEF_FULL:
        got = route(said)
        ok = bool(got) and got[0] == "daily_brief" and got[1].get("full") == want
        failures += not ok
        print(f"{'ok  ' if ok else 'FAIL'} day    {said:46} -> {got}")
    for said, want in SIMPLE_SPLIT:
        got = split_simple(said)
        n = len(got) if got else None
        ok = n == want
        failures += not ok
        print(f"{'ok  ' if ok else 'FAIL'} split  {said[:46]:46} -> {n or 'planner'}")

    from jarvis.tools import places as _pl
    for heard, name in [("mom on my phone. and", "mom"), ("maa on phone. And we'll see you in the next one.", "maa"),
                        ("Domino's Rajpur Road", "Domino's Rajpur Road")]:
        got = _pl._clean_who(heard)
        ok = got == name
        failures += not ok
        print(f"{'ok  ' if ok else 'FAIL'} callee {heard[:46]:46} -> {got}")
    for url, bad in [("https://www.skokkaindia.com/call-girls/dehradun/", True),
                     ("https://www.indiacustomercare.com/dehradun-municipal-corporation-contact-no", True),
                     ("https://www.dominos.co.in/store-location/dehradun", False)]:
        got = bool(_pl._BAD_SOURCES.search(url))
        ok = got == bad
        failures += not ok
        print(f"{'ok  ' if ok else 'FAIL'} source {url[:46]:46} -> {'never used' if got else 'allowed'}")
    ROUTER_CHECKS = 7

    from jarvis.tools import apps
    for label, want in APP_FINAL:
        got = bool(apps._FINAL.search(label))
        ok = got == want
        failures += not ok
        print(f"{'ok  ' if ok else 'FAIL'} appbtn {label:46} -> {'waits for you' if got else 'pressed'}")

    from jarvis import planner
    for said, want in MULTISTEP:
        got = planner.is_multistep(said)
        ok = got == want
        failures += not ok
        print(f"{'ok  ' if ok else 'FAIL'} multi  {said[:46]:46} -> "
              f"{'planner' if got else 'single step'}")
    for said, want in ROUTINES:
        got = routines.find(said)
        ok = got == want
        failures += not ok
        print(f"{'ok  ' if ok else 'FAIL'} routine {said:45} -> {got}")

    for said, want in UNLOCK:
        got = bool(_UNLOCK.match(said.strip()))
        ok = got == want
        failures += not ok
        print(f"{'ok  ' if ok else 'FAIL'} unlock {said:46} -> "
              f"{'cannot-unlock reply' if got else 'not an unlock request'}")

    for said, want in VERB_TYPOS:
        got = _fix_verb(said)
        ok = got == want
        failures += not ok
        print(f"{'ok  ' if ok else 'FAIL'} typo   {said:46} -> {got}")

    from jarvis.tools import files
    files.last_file = Path(r"C:\example\Experiment 6.1.docx")
    for said, want in OPEN_IT:
        got = _direct_it(said)
        ok = (got[0] if got else None) == want
        failures += not ok
        print(f"{'ok  ' if ok else 'FAIL'} it     {said:46} -> {got[0] if got else 'not about the last file'}")
    files.last_file = None

    for markdown, must_contain in RENDERING:
        html = render.to_html_body(markdown)
        ok = must_contain in html
        failures += not ok
        print(f"{'ok  ' if ok else 'FAIL'} render {markdown[:42]:48} -> "
              f"{'contains ' + must_contain if ok else 'MISSING ' + must_contain}")
        if not ok:
            print(f"       got: {html[:160]}")

    for question, want_day in BRIEFING:
        got = _direct_brief(question)
        day = got[1]["day"] if got else None
        ok = day == want_day
        failures += not ok
        print(f"{'ok  ' if ok else 'FAIL'} brief  {question:46} -> "
              f"{day or 'not a brief'}")

    for question, want in THINKING:
        got = _thinking(question)
        ok = got == want
        failures += not ok
        print(f"{'ok  ' if ok else 'FAIL'} think  {question[:46]:48} -> "
              f"{'deliberate' if got else 'answer straight out'}")

    for markdown, want in SPEAKABLE:
        got = _speakable(markdown)
        ok = want in got
        failures += not ok
        print(f"{'ok  ' if ok else 'FAIL'} speak  {markdown[:42]:48} -> {got[:60]}")

    for markdown, want in LEAD:
        got = _lead_line(markdown, prefer_final=True)
        ok = got.startswith(want)
        failures += not ok
        print(f"{'ok  ' if ok else 'FAIL'} lead   {markdown[:42]!r:48} -> {got[:60]}")

    total = (len(ROUTING) + len(RENDERING) + len(SPEAKABLE) + len(LEAD)
             + len(BRIEFING) + len(THINKING) + len(POWER)
             + len(VERB_TYPOS) + len(OPEN_IT) + len(UNLOCK)
             + len(SECRET_FIELDS) + len(SUBMIT_WORDS) + len(ROUTINES)
             + len(MULTISTEP) + len(PROFILE_REFUSES) + len(MEMORY_ROUTES)
             + len(WRITING_SAMPLES) + len(APP_FINAL) + len(BASIC) + len(SIMPLE_SPLIT) + 6
             + len(BRIEF_FULL) + 1)
    print(f"\n{total} cases, {failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

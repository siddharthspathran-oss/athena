"""Checks the command routers, which tools the model is shown, the memory
tools and how a tool call is run.

The routers in agent.py answer the common commands without the model, and
each fix to one of them can quietly break another. This table is the
guard: every phrase here must keep routing exactly as written.

    python tests/routetest.py
"""
import ctypes
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if not hasattr(ctypes, "WinDLL"):
    # Not Windows: stand in for the Win32 modules so the routers, which do
    # not need them for these phrases, can still be checked.
    from unittest import mock

    ctypes.WinDLL = lambda *a, **k: mock.MagicMock()
    ctypes.windll = mock.MagicMock()
    ctypes.WINFUNCTYPE = ctypes.CFUNCTYPE
    for name in ("win32gui", "win32con", "win32api", "win32process", "win32clipboard",
                 "pycaw", "pycaw.pycaw", "comtypes", "comtypes.client", "winsound",
                 "keyboard", "send2trash", "playwright", "playwright.sync_api", "ddgs",
                 "pythoncom", "winreg", "msvcrt"):
        try:
            __import__(name)
        except Exception:
            sys.modules[name] = mock.MagicMock()

import jarvis.tools  # noqa: E402,F401  (registers every tool)
from jarvis import memory, profile, registry  # noqa: E402
from jarvis.agent import route, split_simple  # noqa: E402

# phrase -> (tool, args), or None when it must be left to the model.
# Only phrases whose routing does not depend on this PC's apps, files or
# contacts, so the table holds on any machine.
ROUTES = {
    # missions: Claude Code does a big job in the background
    # Claude builds open a live console on the Deck (Hedwig-style, 23 Oct 2026); research and other workers stay missions
    "have claude build me a one page website for a cafe":
        ("deck_spawn", {"kind": "claude", "count": 1, "folder": "", "task": "build me a one page website for a cafe"}),
    "athena, ask cloud to make me a snake game in python":
        ("deck_spawn", {"kind": "claude", "count": 1, "folder": "", "task": "make me a snake game in python"}),
    "jarvis start a clod code for my friend's peptide shop website":
        ("deck_spawn", {"kind": "claude", "count": 1, "folder": "", "task": "build my friend's peptide shop website"}),
    "build a todo app with claude code": ("deck_spawn", {"kind": "claude", "count": 1, "folder": "", "task": "build a todo app"}),
    "open claude code and make a snake game": ("deck_spawn", {"kind": "claude", "count": 1, "folder": "", "task": "make a snake game"}),
    "have claude research the best laptops": ("mission_start", {"task": "research the best laptops", "worker": "claude"}),
    # flows on the Deck (deck/flows.py), run by name; "workflow" stays a routine
    "athena, run the weekly summary flow": ("deck_flow_run", {"name": "weekly summary"}),
    "run flow called release notes": ("deck_flow_run", {"name": "release notes"}),
    "have gemini research the best esp32 microphones": ("mission_start", {"task": "research the best esp32 microphones", "worker": "gemini"}),
    "ask argon to write a study plan for signals": ("mission_start", {"task": "write a study plan for signals", "worker": "argon"}),
    "have gemini argon build a quiz on fourier series": ("mission_start", {"task": "build a quiz on fourier series", "worker": "argon"}),
    "start a mission to write a study timetable": ("mission_chain", {"goal": "write a study timetable"}),
    "athena, start a mission to find a cafe near me with no website and build them one":
        ("mission_chain", {"goal": "find a cafe near me with no website and build them one"}),
    "mission: compare three esp32 boards and make a buying guide":
        ("mission_chain", {"goal": "compare three esp32 boards and make a buying guide"}),
    "ask claude what time it is": None,
    "how's the mission going": ("mission_status", {}),
    "every 30 minutes, check whether the orb site works on phones until it does":
        ("loop_start", {"task": "check whether the orb site works on phones", "until": "it does", "every_minutes": 30.0}),
    "check the orb landing page on phones every hour until the cards fit":
        ("loop_start", {"task": "check the orb landing page on phones", "until": "the cards fit", "every_minutes": 60.0}),
    "every half an hour check my LMS for new marks till they are posted":
        ("loop_start", {"task": "check my LMS for new marks", "until": "they are posted", "every_minutes": 30.0}),
    "stop the orb loop": ("loop_stop", {"which": "orb"}),
    "how are my loops": ("loop_status", {}),
    "does anything need me": ("needs_me", {}),
    "open the deck": ("deck_open", {"folder": ""}),
    "open the deck on the orb landing": ("deck_open", {"folder": "orb landing"}),
    "open two claude consoles on the orb site": ("deck_spawn", {"kind": "claude", "count": 2, "folder": "orb site", "task": ""}),
    "open two codex consoles on the orb site": ("deck_spawn", {"kind": "codex", "count": 2, "folder": "orb site", "task": ""}),
    "hand off console 1 to codex: finish the docs": ("deck_handoff", {"frm": "console 1", "to": "codex", "task": "finish the docs"}),
    "hand console 2 over to a new claude and have it write the tests":
        ("deck_handoff", {"frm": "console 2", "to": "claude", "task": "write the tests"}),
    "hand off console 3 to console 1": ("deck_handoff", {"frm": "console 3", "to": "console 1", "task": ""}),
    "loop console 2 every 10 minutes: run the tests until they all pass":
        ("deck_loop", {"to": "console 2", "text": "run the tests", "every": 10, "goal": "they all pass"}),
    "every 5 minutes tell the codex console to check the build": ("deck_loop", {"to": "the codex console", "text": "check the build", "every": 5, "goal": ""}),
    "stop the loop on console 2": ("deck_loop_stop", {"to": "console 2"}),
    "open a chat gpt console": ("deck_spawn", {"kind": "chatgpt", "count": 1, "folder": "", "task": ""}),
    "spin up a gemini agent on athena sites to research esp32 boards":
        ("deck_spawn", {"kind": "gemini", "count": 1, "folder": "athena sites", "task": "research esp32 boards"}),
    "what are my consoles doing": ("deck_status", {}),
    "tell console 2 to run the tests": ("deck_send", {"text": "run the tests", "to": "console 2"}),
    "tell the gemini console yes": ("deck_send", {"text": "yes", "to": "the gemini console"}),
    "what does console 1 say": ("deck_read", {"to": "console 1"}),
    "show me console 2": ("deck_focus", {"to": "console 2"}),
    "who needs me": ("needs_me", {}),
    "what is the status of my missions": ("mission_status", {}),
    "stop the mission": ("mission_stop", {}),
    "athena, is the emission done?": ("mission_status", {}),
    "what is going on": None,
    "athena, publish it": ("mission_publish", {"which": ""}),
    "put the cafe site online": ("mission_publish", {"which": "cafe"}),
    "make the website live": ("mission_publish", {"which": ""}),
    "open the email draft": ("mission_draft", {}),
    "athena, review my flashcards": ("review_flashcards", {"topic": ""}),
    "athena, do you have any meetings today?": ("teams_meetings", {"day": "today"}),
    "every monday at 7am, start a mission to scan new AI tools and write me a rollup":
        ("add_rule", {"when": "every monday at 7am", "do": "start a mission to scan new AI tools and write me a rollup"}),
    "every sunday at 10pm start a mission to read my LMS files and update my study plan":
        ("add_rule", {"when": "every sunday at 10pm", "do": "start a mission to read my LMS files and update my study plan"}),
    "revise what i got wrong on fourier series": ("review_flashcards", {"topic": "fourier series"}),
    "what is happening": None,
    "is the machines done": ("mission_status", {}),
    "are my missions finished": ("mission_status", {}),
    # power
    "shut down the pc": ("power_action", {"action": "shutdown"}),
    "cancel shutdown": ("power_action", {"action": "cancel"}),
    "put the laptop to sleep": ("power_action", {"action": "sleep"}),
    "turn off the lights": None,
    # media
    "play music": ("music", {"action": "play"}),
    "pause the music": ("music", {"action": "pause"}),
    "next song": ("music", {"action": "next"}),
    "skip": ("music", {"action": "next"}),
    "previous track": ("music", {"action": "previous"}),
    "play music. Thank you.": ("music", {"action": "play"}),
    "play the first song": ("play_track", {"position": 1}),
    "play track 3": ("play_track", {"position": 3}),
    "play a seven song": ("play_track", {"position": 7}),
    "play believer by imagine dragons": ("play_song", {"query": "believer by imagine dragons"}),
    "play something": ("play_my_taste", {}),
    "surprise me": ("play_my_taste", {}),
    # 28 Sep 2026: this one searched Spotify for "something that you like"
    "play something that you like": ("play_my_taste", {}),
    "play any song": ("play_my_taste", {}),
    "play my kind of music": ("play_my_taste", {}),
    "play songs that i like": ("play_my_taste", {}),
    "put on some music": ("music", {"action": "play"}),
    "play something by arijit singh": ("play_song", {"query": "arijit singh"}),
    "play some songs by taylor swift": ("play_song", {"query": "taylor swift"}),
    # PC status is read, never guessed (the model invented "124.56 GB free")
    "how much free space is on my c drive": ("pc_status", {"what": "disk"}),
    "is my disk full": ("pc_status", {"what": "disk"}),
    "how much ram am i using": ("pc_status", {"what": "memory"}),
    "cpu usage": ("pc_status", {"what": "cpu"}),
    "how long has my pc been on": ("pc_status", {"what": "uptime"}),
    # time of day decides am/pm ("at 5 in the evening" was set for 5 AM)
    "remind me to call maa at 7 in the evening": ("add_reminder", {"text": "call maa", "when": "at 7  in the evening"}),
    "remind me tomorrow morning to submit the assignment": ("add_reminder", {"text": "submit the assignment", "when": "tomorrow morning"}),
    # asked, not acted on: "what song is playing" pressed Next twice
    "what song is playing": ("now_playing", {}),
    "what time is it in london": ("world_time", {"place": "london"}),
    "which wifi am i connected to": ("pc_status", {"what": "wifi"}),
    "what is my ip address": ("pc_status", {"what": "ip"}),
    "whats on my calendar today": ("daily_brief", {"day": "today", "full": False}),
    "what is an ip address": ("explain", {"question": "what is an ip address"}),
    # "Game mode on." went to the model, which claimed it without a tool
    "Game mode on.": ("free_gpu", {}),
    "gamer mode": ("free_gpu", {}),
    "turn off game mode": ("load_gpu", {}),
    "done gaming": ("load_gpu", {}),
    "free up space on my c drive": None,
    "what is a cpu": None,
    "tell me about space": None,
    "play my playlist": ("play_my_playlist", {}),
    "set volume to 40": ("set_volume", {"level": 40}),
    "volume 120": ("set_volume", {"level": 100}),
    # quick system answers
    "take a screenshot": ("take_screenshot", {}),
    "lock my screen": ("lock_screen", {}),
    "what is my battery": ("battery", {}),
    "how much battery do i have left": ("battery", {}),
    "am i charging": ("battery", {}),
    "battery saver on": None,
    "what time is it": ("get_datetime", {"what": "time"}),
    "whats the time": ("get_datetime", {"what": "time"}),
    "what day is it today": ("get_datetime", {"what": "date"}),
    "what is my pc doing": ("system_report", {}),
    "what can you do": ("list_skills", {}),
    "what's on my screen": ("look_at_screen", {"question": "what's on my screen", "whole_screen": False}),
    "gaming mode": ("free_gpu", {}),
    "turn off gaming mode": ("load_gpu", {}),
    "hang up": ("end_call", {}),
    # schedule
    "what's my day": ("daily_brief", {"day": "today", "full": False}),
    "brief me": ("daily_brief", {"day": "today", "full": False}),
    "what's on tomorrow": ("daily_brief", {"day": "tomorrow", "full": False}),
    "what's my full schedule today": ("daily_brief", {"day": "today", "full": True}),
    "remind me to call mom at 5": ("add_reminder", {"text": "call mom", "when": "at 5"}),
    "remind me in 10 minutes to stand up": ("add_reminder", {"text": "stand up", "when": "in 10 minutes"}),
    "add task finish the lab report by friday": ("add_task", {"text": "finish the lab report", "due": "friday"}),
    "list my reminders": ("list_reminders", {}),
    "my tasks": ("list_tasks", {}),
    "list my routines": ("list_routines", {}),
    "study mode": ("run_routine", {"name": "study mode"}),
    # class work vs quick facts
    "explain the difference between bjt and mosfet":
        ("explain", {"question": "explain the difference between bjt and mosfet"}),
    "what is the poisson process": ("explain", {"question": "what is the poisson process"}),
    "what is the capital of france": None,
    "what is the population of india": None,
    "what is 2 plus 2": None,
    # memory
    "what do you remember about me": ("what_you_remember", {}),
    "remember that Rahul is my classmate": ("remember_about_me", {"note": "Rahul is my classmate"}),
    "forget that I like lofi": ("forget_about", {"thing": "I like lofi"}),
    "remember my locker number 42": ("remember", {"what": "42", "label": "locker number"}),
    "Athena, remember my locker number is 42": ("remember", {"what": "42", "label": "locker number"}),
    "remember that my locker number is 42": ("remember", {"what": "42", "label": "locker number"}),
    "my locker number is 42, remember that": ("remember", {"what": "42", "label": "locker number"}),
    "save my wifi name as Home5G": ("remember", {"what": "Home5G", "label": "wifi name"}),
    "note down my roll number 21BEC1023": ("remember", {"what": "21BEC1023", "label": "roll number"}),
    "remember that I like lofi": ("remember_about_me", {"note": "I like lofi"}),
    "what did we decide about the butterworth filter":
        ("search_past_conversations", {"query": "the butterworth filter"}),
    "learn my writing style": ("learn_my_writing_style", {}),
    "Athena, what did you talk about?": ("recent_conversations", {"when": ""}),
    "what did we talk about yesterday": ("recent_conversations", {"when": "yesterday"}),
    "what did I ask you today": ("recent_conversations", {"when": "today"}),
    "recap": ("recent_conversations", {"when": ""}),
    "what did we talk about the butterworth filter":
        ("search_past_conversations", {"query": "the butterworth filter"}),
    "Athena, search my documents for signal and system": ("find_document", {"query": "signal and system"}),
    "find the lab manual on my pc": ("find_document", {"query": "lab manual"}),
    "look for my dsp notes in my files": ("find_document", {"query": "dsp notes"}),
    "search for flights to goa": None,
    "open all my signals and systems files": ("find_document", {"query": "signals and systems", "open_all": True}),
    "open all the dsp pdfs": ("find_document", {"query": "dsp pdf", "open_all": True}),
    "open all apps": None,
    # files and folders
    "open downloads": ("open_folder", {"name": "downloads"}),
    "oopen downloads": ("open_folder", {"name": "downloads"}),
    "open my documents folder": ("open_folder", {"name": "documents"}),
    "open file explorer": ("open_folder", {"name": "explorer"}),
    "where is my resume.pdf": ("reveal_path", {"path": "resume.pdf"}),
    "open the scheduler pdf": ("open_file", {"path": "the scheduler pdf"}),
    # places and calls
    "call me": ("call_me", {"message": "Athena here. This is a test call. Alerts are working."}),
    "call me on my phone": ("call_me", {"message": "Athena here. This is a test call. Alerts are working."}),
    # files between the PC and the phone
    "send the signals lab manual to my phone": ("send_to_phone", {"file": "signals lab manual"}),
    "can you share my resume.pdf with my phone": ("send_to_phone", {"file": "resume.pdf"}),
    "send it to my phone": ("send_to_phone", {"file": "it"}),
    "send a message to my phone": None,
    "send me the lab manual": None,          # said to the PC: "me" is not "my phone"
    # WhatsApp phrasings that went wrong on 3 Oct 2026
    "send me a joke": None,
    "message me hello": ("whatsapp_message", {"to": "me", "text": "hello"}),
    "message maa I am coming home": ("whatsapp_message", {"to": "maa", "text": "I am coming home"}),
    "send a high message to me on whatsapp": ("whatsapp_message", {"to": "me", "text": "hi"}),
    # also 3 Oct 2026: Bluetooth connect, the last song
    "connect to echo": ("bluetooth", {"action": "connect", "device": "echo"}),
    "pair with galaxy buds": ("bluetooth", {"action": "connect", "device": "galaxy buds"}),
    "turn on bluetooth": ("bluetooth", {"action": "on"}),
    "connect to wifi": None,
    "play the last song of my playlist": ("play_track", {"position": -1}),
    "what classes do i have on teams today": ("teams_meetings", {"day": "today"}),
    "any teams meetings tomorrow": ("teams_meetings", {"day": "tomorrow"}),
    "join my class": ("join_teams_meeting", {"which": "next"}),
    "join marketing on teams": ("join_teams_meeting", {"which": "marketing"}),
    # automation (3 Oct 2026)
    "every time i get home, connect to echo and play my playlist":
        ("add_rule", {"when": "every time i get home", "do": "connect to echo and play my playlist"}),
    "organise my downloads every 2 hours": ("add_rule", {"when": "every 2 hours", "do": "organise my downloads"}),
    "when is my next class": ("upes_classes", {"day": "next"}),   # not a "when ..., do ..." rule
    "pause all automation": ("pause_automation", {"minutes": 0}),
    "resume automation": ("resume_automation", {}),
    "what did you do on your own": ("what_did_you_do", {}),
    "health check": ("health_check", {}),
    "check my email": ("inbox_summary", {"draft_replies": True}),
    "plan my assignments": ("plan_my_assignments", {}),
    "keep an eye on my orders": ("add_watch", {"what": "stuck_orders", "every_hours": 6, "value": "2"}),
    "can i skip probability tomorrow": ("can_i_skip", {"course": "probability"}),
    "whats my attendance": ("my_attendance", {"course": ""}),
    "what classes do i have tomorrow": ("upes_classes", {"day": "tomorrow"}),
    "can i skip it": None,
    "whats due on lms": ("lms_deadlines", {}),
    "check lms": ("lms_deadlines", {}),
    "Athena, open the UPS login.": ("open_upes_login", {}),
    "Athena, what are you doing?": None,
    "send a whatsapp message to sleepyhead panda saying hi": ("whatsapp_message", {"to": "neha", "text": "hi"}),
    # "tell me ..." is a question, wherever it sits (both were sent as WhatsApps, 3 Oct 2026)
    "No, please tell me if I have any alarms for today.": ("list_reminders", {}),
    "similarly tell me what is a flip flop": None,
    "do I have any timer set?": ("list_reminders", {}),
    # asked of the contact book, not the explainer ("the Neha River", 4 Oct 2026)
    "Athena, what is the alternate name of Neha?": ("contact_info", {"name": "neha"}),
    "Athena, what is Neha also known as?": ("contact_info", {"name": "neha"}),
    "who is sleepyhead panda": ("contact_info", {"name": "sleepyhead panda"}),
    "what is a flip flop": ("explain", {"question": "what is a flip flop"}),
    "message sleepy head panda i am coming": ("whatsapp_message", {"to": "neha", "text": "i am coming"}),
    "Athena, how many devices do you have": ("phone_devices", {}),
    "gesture mode on": ("gesture_mode", {"action": "on"}),
    "Tower now of gesture mode.": ("gesture_mode", {"action": "on"}),
    "Athena, close gesture mode": ("gesture_mode", {"action": "off"}),
    "is gesture mode on?": ("gesture_mode", {"action": "status"}),
    "Athena, Now.": None,
    "turn off gesture mode": ("gesture_mode", {"action": "off"}),
    "search saga builds on youtube on my phone": ("phone_youtube", {"query": "saga builds", "everywhere": False}),
    "search saga builds in all three youtubes": ("phone_youtube", {"query": "saga builds", "everywhere": True}),
    "search saga builds on youtube on all devices": ("phone_youtube", {"query": "saga builds", "everywhere": True}),
    "open youtube on all phones": ("phone_open_everywhere", {"name": "youtube"}),
    # the phone over Wi-Fi (4 Oct 2026)
    "Athena, show my phone": ("show_phone", {}),
    "open whatsapp on my phone": ("phone_open_app", {"name": "whatsapp"}),
    "where's my phone": ("find_my_phone", {}),
    "what's my phone battery": ("phone_status", {}),
    "lock my phone": ("lock_phone", {}),
    "take a screenshot of my phone": ("phone_screenshot", {}),
    "pause the music on my phone": ("phone_media", {"action": "pause"}),
    "skip the song on my phone": ("phone_media", {"action": "next"}),
    "open whatsapp": ("open_app", {"name": "whatsapp"}),
    # questions about your own data never reach the model to be guessed (3 Oct 2026)
    "what is my next class": ("upes_classes", {"day": "next"}),
    "do i have anything due": ("lms_deadlines", {}),
    "how many tasks do i have": ("list_tasks", {}),
    "is my battery low": ("battery", {}),
    "any emails for me": ("inbox_summary", {}),
    "what is the capital of france": None,
    "Athena, when is my next MS team meeting scheduled?": ("teams_meetings", {"day": "next"}),
    "So Athena, when do I have my next MS team meeting?": ("teams_meetings", {"day": "next"}),
    "Athena, when is the next data of my MS team meeting?": ("teams_meetings", {"day": "next"}),
    "do i have any teams classes tomorrow": ("teams_meetings", {"day": "tomorrow"}),
    "what is your name": None,
    "open UPS": ("open_upes_login", {}),
    "open my upes portal": ("open_upes_login", {}),
    "open feedback forum for leading conversation": ("open_lms", {"what": "feedback forum for leading conversation"}),
    "Athena, open forum for...": ("open_lms", {"what": "forum for"}),
    "open the file I sent from my phone": ("open_from_phone", {"which": "latest"}),
    "show what my phone sent": ("open_from_phone", {"which": "folder"}),
    "is dominos open now": ("place_info", {"place": "dominos", "question": "is dominos open now"}),
    "book a table at barbeque nation for 4 tomorrow at 8":
        ("book_reservation", {"place": "barbeque nation", "when": "tomorrow at 8", "people": 4}),
    # conversation
    "tell me a joke": None,
}

# Typed in the phone's chat, where "send me X" means "to this phone".
PHONE_ROUTES = {
    "send me the lab manual": ("send_to_phone", {"file": "lab manual"}),
    "please send me my resume.pdf": ("send_to_phone", {"file": "resume.pdf"}),
    "send me a joke": None,
    "send me some ideas for dinner": None,
    "send me the summary": None,
    "send me a reminder at 5": ("add_reminder", {"text": "Reminder", "when": "at 5"}),
}

# split_simple(text, strict=True): the sentence did not look multi-step,
# so every part must be a command of its own.
STRICT_SPLITS = {
    "what is my battery and list my reminders": [("battery", {}), ("list_reminders", {})],
    "play simon and garfunkel": None,
    "play tom and jerry": None,
}

SPLITS = {
    "open downloads and play music": [("open_folder", {"name": "downloads"}), ("music", {"action": "play"})],
    "take a screenshot then lock my screen": [("take_screenshot", {}), ("lock_screen", {})],
    "open downloads and tell me a joke": None,
}

# request -> tools that must be shown to the model / must not be.
SELECTION = [
    ("what is the capital of france", ["web_search", "open_app", "remember", "recall"], ["whatsapp_message", "run_powershell"]),
    ("send papa a whatsapp saying running late", ["whatsapp_message", "list_contacts"], ["run_powershell"]),
    ("turn on bluetooth", ["bluetooth"], ["whatsapp_call"]),
    ("delete the old pdf in downloads", ["delete_file", "find_files"], []),
    ("play something by arijit", ["music"], ["play_song", "play_track", "play_my_taste"]),
    ("what do you know about me, forget it", ["recall", "forget"], ["remember_about_me", "forget_about", "what_you_remember"]),
    ("show my store orders", ["store_orders"], []),
    ("i need the physics notes on my mobile", ["send_to_phone"], []),
    ("did my phone send anything", ["open_from_phone"], []),
    ("what can you do", [], ["store_orders", "free_gpu"]),
    ("how are you", [], ["look_at_screen"]),
    ("can you see this error", ["look_at_screen"], []),
]


def check_routes() -> list[str]:
    bad = []
    from jarvis import share
    share.from_phone = True                  # typed in the phone's chat
    try:
        for phrase, want in PHONE_ROUTES.items():
            got = route(phrase)
            if got != want:
                bad.append(f"from the phone: route({phrase!r}) = {got}, want {want}")
    finally:
        share.from_phone = False
    for phrase, want in ROUTES.items():
        got = route(phrase)
        if got != want:
            bad.append(f"route({phrase!r}) = {got}, want {want}")
    for phrase, want in STRICT_SPLITS.items():
        got = split_simple(phrase, strict=True)
        if got != want:
            bad.append(f"split_simple({phrase!r}, strict) = {got}, want {want}")
    for phrase, want in SPLITS.items():
        got = split_simple(phrase)
        if got != want:
            bad.append(f"split_simple({phrase!r}) = {got}, want {want}")
    return bad


def check_selection() -> list[str]:
    # As in general mode: a mode the user left on (data/mode.json, "study" on 23 Oct)
    # adds its own tools ahead of the request's and moved the fixed prefix.
    from jarvis import modes
    real, modes.groups = modes.groups, lambda: []
    try:
        return _check_selection()
    finally:
        modes.groups = real


def _check_selection() -> list[str]:
    bad = []
    everything = registry.select("", everything=True)
    core = registry.select("")
    if len(core) >= len(everything):
        bad.append("the core tool set is no smaller than the full list")
    for request, shown, hidden in SELECTION:
        names = registry.select(request)
        if names[:len(core)] != core:
            bad.append(f"{request!r}: the core is not the fixed prefix (breaks Ollama's cache)")
        bad += [f"{request!r}: {n} not shown" for n in shown if n not in names]
        bad += [f"{request!r}: {n} shown" for n in hidden if n in names]
    for name in registry.CORE_TOOLS + sorted(registry._grouped()):
        if name not in registry.REGISTRY:
            bad.append(f"TOOL_GROUPS names {name}, which is not a registered tool")
    return bad


def check_memory() -> list[str]:
    from jarvis.tools import shell

    bad = []
    tmp = Path(tempfile.mkdtemp())
    saved = memory.MEM_FILE, profile.PROFILE
    memory.MEM_FILE, profile.PROFILE = tmp / "memory.json", tmp / "profile.md"
    memory._facts_cache.update(mtime=None, data={})
    try:
        shell.remember("42", "locker number")
        shell.remember("14 March", "birthday")
        shell.remember("15 March", "my birthday")
        if memory.load_facts() != {"locker number": "42", "birthday": "15 March"}:
            bad.append(f"relabelled fact not updated in place: {memory.load_facts()}")
        shell.remember("I prefer answers with every step shown")
        for q, want in (("locker", "42"), ("bday", "15 March"), ("my birthday", "15 March"),
                        ("every step", "every step shown")):
            if want not in shell.recall(q):
                bad.append(f"recall({q!r}) missed {want!r}: {shell.recall(q)!r}")
        if not shell.remember("42").startswith("ERROR"):
            bad.append("a bare value with no label was accepted and can never be found")
        # Saved by another process: the prompt must see it without a restart.
        m = memory.Memory("system")
        time.sleep(0.05)
        memory.save_json(memory.MEM_FILE, {**memory.load_facts(), "wifi name": "Home5G"})
        memory._facts_cache["mtime"] = None
        if "Home5G" not in m.build()[0]["content"]:
            bad.append("a fact saved elsewhere did not reach the prompt")
        # Asked back from a saved fact without the model; a question about
        # something never saved is left alone.
        if route("what is my locker number") != ("recall", {"query": "locker number"}):
            bad.append(f"'what is my locker number' not answered from memory: "
                       f"{route('what is my locker number')}")
        if route("what is my name") == ("recall", {"query": "name"}):
            bad.append("'what is my name' answered with the wifi name")
        shell.remember("B-12", "mom locker number")
        if not shell.forget("name").startswith(("Which one?", "I don't")):
            bad.append("forget('name') deleted a fact it only loosely matched")
        if not shell.forget("locker").startswith("Which one?"):
            bad.append("an ambiguous forget deleted something instead of asking")
        shell.forget("locker number")
        if "locker number" in memory.load_facts() or "mom locker number" not in memory.load_facts():
            bad.append(f"forget removed the wrong fact: {memory.load_facts()}")
        shell.forget("every step shown")
        if profile.load():
            bad.append("forget did not clear the matching profile line")
    finally:
        memory.MEM_FILE, profile.PROFILE = saved
        memory._facts_cache.update(mtime=None, data={})
    return bad


def check_tool_run() -> list[str]:
    bad = []

    @registry.tool(timeout=0.3)
    def _t_slow() -> str:
        """Sleeps past its limit."""
        time.sleep(1.5)
        return "late"

    @registry.tool
    def _t_bug(x: str) -> str:
        """Raises a TypeError from inside."""
        return len(x, 3)

    @registry.tool
    def _t_ok(x: str, flag: bool = False) -> str:
        """Echoes."""
        return f"{x}-{flag}"

    try:
        t0 = time.time()
        r = registry.REGISTRY["_t_slow"].run()
        if not r.startswith("ERROR") or time.time() - t0 > 1.0:
            bad.append(f"a hung tool was waited on: {r!r}")
        r = registry.REGISTRY["_t_bug"].run(x="a")
        if "wrong arguments" in r:
            bad.append("a bug inside a tool was reported as wrong arguments")
        r = registry.REGISTRY["_t_ok"].run(x="a", flag="true", reason="extra")
        if r != "a-True":
            bad.append(f"an extra argument was not dropped: {r!r}")
        if "wrong arguments" not in registry.REGISTRY["_t_ok"].run(flag=True):
            bad.append("a missing argument was not reported")
    finally:
        for n in ("_t_slow", "_t_bug", "_t_ok"):
            registry.REGISTRY.pop(n, None)
    return bad


def check_offers() -> list[str]:
    """Answers to "which one?" / "want me to open it?", and the guard that
    stops Whisper's primer being taken for a command."""
    from types import SimpleNamespace as NS

    from jarvis import offer
    from jarvis.ui import voice

    bad = []
    offer.set_choices([offer.Choice(n, "open_file", {"path": n})
                       for n in ("SS Unit 1.pdf", "SS Unit 2 notes.pdf", "Signals lab manual.docx")])
    want = {"yes": ["SS Unit 1.pdf"], "yeah open it": ["SS Unit 1.pdf"], "no thanks": "no",
            "all of them": 3, "the others": 3, "two": ["SS Unit 2 notes.pdf"],
            "open the third": ["Signals lab manual.docx"], "last": ["Signals lab manual.docx"],
            "lab manual": ["Signals lab manual.docx"], "five": None, "open spotify": None}
    for said, expect in want.items():
        got = offer.resolve(said)
        got = got if isinstance(got, (str, type(None))) else (
            len(got) if isinstance(expect, int) else [c.label for c in got])
        if got != expect:
            bad.append(f"offer.resolve({said!r}) = {got}, want {expect}")
    for said in ("yes", "two", "all", "no"):
        if not offer.REPLY.match(said):
            bad.append(f"the voice loop would drop the reply {said!r} as too short")
    offer.clear()

    seg = lambda nsp, lp: [NS(no_speech_prob=nsp, avg_logprob=lp)]  # noqa: E731
    primer = voice._BASE_BIAS
    for text, segs, secs, expect in (
            ("Athena, open Spotify and play music.", seg(0.6, -0.3), 1.2, True),
            ("Athena, open Spotify and play music.", seg(0.05, -0.2), 1.0, True),
            ("Athena, open Spotify and play music.", seg(0.05, -0.2), 3.2, False),
            ("Athena, open Chrome and search for goa", seg(0.6, -1.0), 1.0, False)):
        if voice._echoes_prompt(text, segs, secs, primer) != expect:
            bad.append(f"primer guard on {text!r} ({secs}s): want {expect}")
    # The primer used while Athena talks must hold stop words only: a command
    # in it comes back as a command on noise.
    import re as _re
    m = _re.search(r'_BARGE_PROMPT = "([^"]*)"', Path(voice.__file__).read_text(encoding="utf-8"))
    if not m or _re.search(r"\b(?:open|play|close|call|send)\b", m.group(1), _re.I):
        bad.append(f"the barge-in primer holds a command: {m.group(1) if m else 'not found'!r}")
    return bad


def check_planner() -> list[str]:
    """Lookups the planner batches run side by side; saved facts reach it,
    secrets do not; it is sent the relevant tools, not all of them."""
    import json as _json

    from jarvis import planner

    bad = []
    slow = ("_p_one", "_p_two", "_p_three")

    def lookup(name):
        def fn() -> str:
            """Slow lookup."""
            time.sleep(0.6)
            return f"{name} ok"
        fn.__name__ = name
        return fn

    for n in slow:
        registry.tool(timeout=5)(lookup(n))
    planner.PARALLEL_SAFE.update(slow)
    seen = {}
    script = [_json.dumps({"tools": [{"tool": n, "args": {}} for n in slow]}),
              _json.dumps({"done": "All three."})]

    def fake_chat(model, messages, **k):
        seen.setdefault("system", messages[0]["content"])
        return script.pop(0)

    tmp = Path(tempfile.mkdtemp())
    saved = planner.cloud.chat, memory.MEM_FILE
    planner.cloud.chat, memory.MEM_FILE = fake_chat, tmp / "memory.json"
    memory._facts_cache.update(mtime=None, data={})
    try:
        memory.save_facts({"roll number": "21BEC1023", "wifi password": "hunter22"})
        t0 = time.time()
        out = planner.run("check the first thing and then the second and the third")
        took = time.time() - t0
        if out != "All three." or took > 1.4:
            bad.append(f"planner batch: {out!r} in {took:.1f}s (three 0.6s lookups, want them together)")
        system = seen.get("system", "")
        if "21BEC1023" not in system:
            bad.append("the planner was not given the saved facts")
        if "hunter22" in system:
            bad.append("a saved password was sent to the hosted planner")
        listed = system.split("Tools:")[1].count("\n- ") if "Tools:" in system else 0
        if not 0 < listed < len(registry.REGISTRY):
            bad.append(f"the planner was sent {listed} tools, want a narrowed list")
    finally:
        planner.cloud.chat, memory.MEM_FILE = saved
        memory._facts_cache.update(mtime=None, data={})
        for n in slow:
            registry.REGISTRY.pop(n, None)
            planner.PARALLEL_SAFE.discard(n)
    return bad


if __name__ == "__main__":
    failures = (check_routes() + check_selection() + check_memory() + check_tool_run()
                + check_planner() + check_offers())
    for f in failures:
        print("FAIL", f)
    total = len(ROUTES) + len(PHONE_ROUTES) + len(SPLITS) + len(STRICT_SPLITS) + len(SELECTION) + 5
    print(f"{total} groups of cases, {len(failures)} failure(s)")
    sys.exit(1 if failures else 0)

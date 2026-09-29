"""Tool registry.

Decorate any function with @tool and it becomes callable by the model.
The JSON schema Ollama needs is generated automatically from the
function's type hints and docstring, so adding a new skill is one
function and zero boilerplate.

    @tool
    def set_volume(level: int) -> str:
        '''Set the system volume.

        level: volume from 0 to 100
        '''
        ...
"""
from __future__ import annotations

import inspect
import re
import threading
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable, get_args, get_origin, get_type_hints

_PY_TO_JSON = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
}


@dataclass
class Tool:
    name: str
    description: str
    schema: dict[str, Any]
    fn: Callable[..., Any]
    # Destructive tools get a confirmation prompt when safety.confirm_destructive is on
    destructive: bool = False
    tags: list[str] = field(default_factory=list)
    # Seconds before the caller stops waiting (tools.timeout_seconds when
    # None). 0 means no limit, for work that is long by nature.
    timeout: float | None = None

    def _coerce(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        """Force arguments to the types the schema declares.

        Small models routinely send "true" instead of true and "5" instead of
        5. Left alone, send="false" is a non-empty string and therefore truthy,
        which silently turns a "do not send" into a send.
        """
        props = self.schema["function"]["parameters"]["properties"]
        out: dict[str, Any] = {}
        for key, val in kwargs.items():
            want = props.get(key, {}).get("type")
            if want == "boolean" and isinstance(val, str):
                out[key] = val.strip().lower() in ("true", "yes", "1", "y", "on")
            elif want == "integer" and isinstance(val, str):
                try:
                    out[key] = int(float(val.strip()))
                except ValueError:
                    out[key] = val
            elif want == "number" and isinstance(val, str):
                try:
                    out[key] = float(val.strip())
                except ValueError:
                    out[key] = val
            elif want == "string" and isinstance(val, (int, float, bool)):
                out[key] = str(val)
            else:
                out[key] = val
        return out

    def _check_args(self, kwargs: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
        """Fit the model's arguments to the function before it runs.

        Any TypeError used to be reported as "wrong arguments" - including
        one raised by a bug deep inside the tool, so the model rewrote a
        call that was never the problem. Checking the arguments first keeps
        the two apart. Arguments the function does not take are dropped
        rather than refused: a small model adding "reason" to a call should
        not cost a round trip.
        """
        sig = inspect.signature(self.fn)
        params = sig.parameters
        if not any(p.kind is p.VAR_KEYWORD for p in params.values()):
            kwargs = {k: v for k, v in kwargs.items() if k in params}
        try:
            sig.bind(**kwargs)
        except TypeError as e:
            want = ", ".join(n + ("" if p.default is inspect.Parameter.empty else "?")
                             for n, p in params.items())
            return kwargs, f"ERROR: wrong arguments for {self.name}({want}): {e}"
        return kwargs, None

    def _call(self, kwargs: dict[str, Any]) -> str:
        try:
            result = self.fn(**kwargs)
            if result is None:
                return "Done."
            return str(result)
        except Exception as e:
            # The traceback is for whoever reads the console. Handed to the
            # model it is a few hundred tokens of file paths it cannot act on,
            # and it sometimes read them out.
            traceback.print_exc(limit=3)
            return f"ERROR in {self.name}: {type(e).__name__}: {e}"

    def run(self, **kwargs: Any) -> str:
        kwargs, bad = self._check_args(self._coerce(kwargs))
        if bad:
            return bad
        limit = self.timeout
        if limit is None:
            from .config import cfg
            limit = float(cfg.get("tools.timeout_seconds", 45) or 0)
        if not limit:
            return self._call(kwargs)
        # A tool that hangs - a window that never appears, a site that never
        # answers - used to hold the whole request, and every command behind
        # it, with nothing said. It runs on its own thread so the caller can
        # stop waiting. A thread cannot be killed, so it may still finish:
        # the model is told so, and must not simply try again.
        box: dict[str, str] = {}
        worker = threading.Thread(target=lambda: box.update(r=self._call(kwargs)),
                                  daemon=True, name=f"tool-{self.name}")
        worker.start()
        worker.join(limit)
        if worker.is_alive():
            print(f"[tool {self.name} still running after {limit:g}s - no longer waiting]")
            return (f"ERROR: {self.name} did not finish within {limit:g} seconds. It may "
                    f"still complete in the background - tell the user, and do not call it again.")
        return box.get("r", "Done.")


REGISTRY: dict[str, Tool] = {}


def _json_type(annotation: Any) -> dict[str, Any]:
    """Map a Python type hint onto a JSON-schema fragment."""
    if annotation is inspect.Parameter.empty:
        return {"type": "string"}
    origin = get_origin(annotation)
    if origin in (list, set, tuple):
        args = get_args(annotation)
        item = _json_type(args[0]) if args else {"type": "string"}
        return {"type": "array", "items": item}
    if origin is dict:
        return {"type": "object"}
    # Optional[X] / X | None -> unwrap to X
    if origin is not None:
        args = [a for a in get_args(annotation) if a is not type(None)]
        if len(args) == 1:
            return _json_type(args[0])
    return {"type": _PY_TO_JSON.get(annotation, "string")}


def _parse_doc(doc: str) -> tuple[str, dict[str, str]]:
    """Split a docstring into (summary, {param: description}).

    Parameter lines look like:  name: what it means
    """
    if not doc:
        return "", {}
    lines = [ln.strip() for ln in doc.strip().splitlines()]
    summary_parts: list[str] = []
    params: dict[str, str] = {}
    for ln in lines:
        if not ln:
            if summary_parts:
                # blank line after summary -> params may follow
                continue
            continue
        if ":" in ln and not ln.endswith(":"):
            head, tail = ln.split(":", 1)
            head = head.strip()
            # a param line is a bare identifier before the colon
            if head.isidentifier() and summary_parts:
                params[head] = tail.strip()
                continue
        if not params:
            summary_parts.append(ln)
    return " ".join(summary_parts), params


def tool(_fn: Callable | None = None, *, destructive: bool = False, tags: list[str] | None = None,
         brief: str | None = None, timeout: float | None = None):
    """Register a function as a model-callable tool.

    brief replaces the docstring's summary in what the model is shown, for
    tools whose docstring is mostly notes for whoever maintains the code.
    timeout overrides tools.timeout_seconds for this tool; 0 = no limit.
    """

    def wrap(fn: Callable) -> Callable:
        sig = inspect.signature(fn)
        summary, param_docs = _parse_doc(fn.__doc__ or "")
        if brief:
            summary = brief

        # Every tool module uses `from __future__ import annotations`, so the
        # raw annotations are strings ("bool", not bool). Resolving them is
        # what makes the schema honest about types - otherwise every argument
        # is advertised as a string and the model dutifully sends "true".
        try:
            hints = get_type_hints(fn)
        except Exception:
            hints = {}

        properties: dict[str, Any] = {}
        required: list[str] = []
        for pname, p in sig.parameters.items():
            if pname in ("self", "cls"):
                continue
            frag = _json_type(hints.get(pname, p.annotation))
            frag["description"] = param_docs.get(pname, pname)
            properties[pname] = frag
            if p.default is inspect.Parameter.empty:
                required.append(pname)

        schema = {
            "type": "function",
            "function": {
                "name": fn.__name__,
                "description": summary or fn.__name__,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            },
        }

        REGISTRY[fn.__name__] = Tool(
            name=fn.__name__,
            description=summary,
            schema=schema,
            fn=fn,
            destructive=destructive,
            tags=tags or [],
            timeout=timeout,
        )
        return fn

    return wrap(_fn) if _fn else wrap


_SHOP_WORDS = re.compile(r"\b(store|shop|ragazza|orders?|stock|products?|prices?|sales|revenue|"
                         r"customers?|ship(?:ped|ping)?|tracking|inventory|mrp)\b", re.I)


# ---------------------------------------------------------------- selection
# Every tool at once is 92 definitions, ~35k characters - more than the whole
# 8k context window qwen2.5 is given (28 Sep 2026). Ollama then cut the prompt
# to fit, so the model lost part of the tool list and the conversation, read
# ~9k tokens again on every cache miss, and picked badly among near-identical
# names. So the model sees a fixed core plus the groups a request mentions.
#
# Order is fixed on purpose: Ollama reuses its KV cache for an identical
# prefix, and the system prompt and tool list come first. The core always
# renders the same, so it stays cached whatever groups follow it.
#
# The most common commands never reach the model at all (agent.route), so
# the core is what an unrouted request is likely to need. A tool not named
# anywhere below is treated as core, so a newly added tool is never hidden.
CORE_TOOLS = [
    "open_app", "close_app", "open_file", "open_folder", "find_files",
    "web_search", "read_webpage", "open_url", "get_weather",
    "remember", "recall", "add_reminder", "music", "set_volume",
    "battery", "system_info", "take_screenshot", "look_at_screen", "explain",
    "list_skills",
]

_W = r"(?:\w*)"         # any ending: "remind" covers reminder, reminders, reminded
TOOL_GROUPS: list[tuple[str, re.Pattern, list[str]]] = [
    ("files", re.compile(
        r"\b(?:files?|folders?|director(?:y|ies)|pdfs?|docx?|documents?|downloads?|desktop|"
        r"delet|mov|renam|cop(?:y|ies)|writ|wrote|sav|size|organi[sz]|sort|tidy|clean|"
        r"path|drives?|zip|images?|photos?|pictures?|videos?|excel|word|csv|txt|notes?|"
        r"read|contents?|list|show|where|explorer|phone|mobile)" + _W, re.I),
     ["find_document", "send_to_phone", "open_from_phone", "list_folder", "read_file", "write_file", "move_file", "delete_file",
      "organize_folder", "folder_size", "reveal_path"]),
    ("media", re.compile(
        r"\b(?:songs?|tracks?|music|spotify|play|playlist|album|artist|pause|skip|"
        r"volume|loud|quiet|mute|listen)" + _W, re.I),
     ["play_track", "play_song", "play_my_taste", "play_my_playlist", "get_volume"]),
    ("schedule", re.compile(
        r"\b(?:remind|alarm|timer|tasks?|to-?dos?|deadline|assignment|due|schedul|calendar|"
        r"timetable|class|lecture|meeting|brief|today|tomorrow|tonight|week|agenda|"
        r"time|date|day|free|busy|cancel)" + _W, re.I),
     ["list_reminders", "cancel_reminder", "add_task", "complete_task", "list_tasks",
      "check_deadlines", "daily_brief", "get_datetime", "show_timetable", "set_timetable",
      "add_calendar_feed", "list_calendar_feeds", "connect_google"]),
    ("messaging", re.compile(
        r"\b(?:whatsapp|messag|text|msg|call|ring|dial|phone|mobile|contacts?|e-?mail|mail|"
        r"inbox|send|sent|reply|number|tell|hang)" + _W, re.I),
     ["whatsapp_message", "whatsapp_call", "compose_email", "list_contacts", "add_contact",
      "remove_contact", "call_me", "call_number", "end_call"]),
    ("apps", re.compile(
        r"\b(?:windows?|minimi[sz]|maximi[sz]|focus|switch|resiz|snap|running|apps?|"
        r"programs?|installed|type|typing|click|button|field|press|clipboard|paste|"
        r"lock|shut|restart|reboot|sleep|power|log\s*off|report|doing|cpu|ram|memory|"
        r"gpu|slow|task\s*manager)" + _W, re.I),
     ["list_running_apps", "list_installed_apps", "list_windows", "focus_window",
      "resize_window", "type_text", "clipboard", "lock_screen", "power_action",
      "app_read", "app_click", "app_type", "system_report"]),
    ("browser", re.compile(
        r"\b(?:browser|website|web\s*site|site|page|form|fill|log\s*in|login|sign\s*in|"
        r"sign\s*up|tab|url|link|chrome|comet|edge|opera|download|google|youtube|"
        r"online|buy|order|checkout)" + _W, re.I),
     ["browser_open", "browser_read", "browser_fill", "browser_click", "download_file"]),
    ("places", re.compile(
        r"\b(?:book|reserv|table|appointment|restaurant|cafe|hotel|clinic|hospital|doctor|"
        r"salon|shop|store|hours|open\s+now|opening|closing|address|near)" + _W, re.I),
     ["place_info", "book_reservation", "call_number"]),
    ("memory", re.compile(
        r"\b(?:remember|forget|forgot|know\s+about\s+me|recall|conversation|talk|discuss|"
        r"decid|said|style|writing|profile|learn)" + _W, re.I),
     ["forget", "search_past_conversations", "recent_conversations", "learn_my_writing_style",
      # route_only: reached from their phrases, never shown to the model
      "what_you_remember", "remember_about_me", "forget_about"]),
    ("knowledge", re.compile(
        r"\b(?:index|documents?|my\s+notes|knowledge|according|lease|contract|"
        r"search\s+my|in\s+my\s+(?:files|docs|documents|notes))" + _W, re.I),
     ["search_documents", "index_documents", "index_status", "clear_index"]),
    ("settings", re.compile(
        r"\b(?:wi-?fi|bluetooth|settings?|network|internet|headphones?|earbuds|airpods|"
        r"speaker|display|brightness|sound|connect|pair)" + _W, re.I),
     ["wifi", "bluetooth", "list_bluetooth_devices", "open_windows_settings"]),
    ("shell", re.compile(
        r"\b(?:powershell|command|terminal|cmd|script|process|kill|install|uninstall|"
        r"ip\s+address|ping|registry|service|environment)" + _W, re.I),
     ["run_powershell"]),
    ("routines", re.compile(r"\b(?:routines?|workflows?|mode)\b", re.I),
     ["run_routine", "list_routines"]),
]


def _grouped() -> set[str]:
    return {n for _, _, names in TOOL_GROUPS for n in names}


def _visible(t: Tool, shop: bool) -> bool:
    """Tools tagged "route_only" run only from their exact phrases
    (agent.route), never on the model's initiative: asked to turn gaming mode
    *off*, the model called free_gpu five times, unloading itself between
    every step.

    Tools tagged "store" reach real customers, so the model only sees them
    when the request is about the shop: asked "what can you do?", the local
    model once made up a "mark order RGZ1004 shipped" call (27 Sep 2026).
    """
    return "route_only" not in t.tags and ("store" not in t.tags or shop)


def select(request: str = "", everything: bool = False) -> list[str]:
    """Names of the tools the model is shown for this request, in a fixed order."""
    from .config import cfg

    shop = bool(_SHOP_WORDS.search(request or ""))
    if everything or not cfg.get("llm.tool_selection", True):
        return [n for n, t in REGISTRY.items() if _visible(t, shop)]

    grouped = _grouped()
    names = [n for n in CORE_TOOLS if n in REGISTRY]
    # Anything registered but never placed in a group counts as core.
    names += [n for n in REGISTRY if n not in grouped and n not in names]
    for _, pattern, members in TOOL_GROUPS:
        if pattern.search(request or ""):
            names += [n for n in members if n in REGISTRY and n not in names]
    return [n for n in names if _visible(REGISTRY[n], shop)]


_STOP = {"the", "and", "for", "with", "that", "this", "what", "can", "you", "please", "my",
         "your", "from", "into", "about", "have", "does", "will", "would", "could", "use",
         "user", "users", "tool", "when", "not", "any", "all", "are", "was", "its", "it's"}


def _terms(text: str) -> set[str]:
    return {w[:6] for w in re.findall(r"[a-z]{3,}", (text or "").lower()) if w not in _STOP}


def related(request: str, have: list[str], k: int = 8) -> list[str]:
    """Up to k more tools whose name or description shares words with the
    request, for the retry after "I can't".

    That retry used to send every tool - 85 of them, ~32k characters - which
    overflowed the context window exactly as before, and the second answer
    came back as muddled as the first (28 Sep 2026, "remember my locker
    number 42"). A few likely extras fit.
    """
    words = _terms(request)
    if not words:
        return []
    shop = bool(_SHOP_WORDS.search(request or ""))
    scored = []
    for n, t in REGISTRY.items():
        if n in have or not _visible(t, shop):
            continue
        hit = len(words & _terms(n.replace("_", " ") + " " + t.description))
        if hit:
            scored.append((-hit, n))
    return [n for _, n in sorted(scored)[:k]]


def schemas(request: str = "", everything: bool = False) -> list[dict[str, Any]]:
    """Tool schemas for this request, in the shape Ollama's `tools=` expects.

    everything=True returns every tool the request may see, for the retry
    when the model says it cannot do something with the narrower set.
    """
    return [REGISTRY[n].schema for n in select(request, everything)]


def get(name: str) -> Tool | None:
    return REGISTRY.get(name)


def describe_all() -> str:
    """Human-readable catalogue, used by the `help` command."""
    by_tag: dict[str, list[Tool]] = {}
    for t in REGISTRY.values():
        key = t.tags[0] if t.tags else "misc"
        by_tag.setdefault(key, []).append(t)
    out = []
    for tag in sorted(by_tag):
        out.append(f"\n[{tag}]")
        for t in sorted(by_tag[tag], key=lambda x: x.name):
            mark = " (!)" if t.destructive else ""
            out.append(f"  {t.name}{mark} - {t.description}")
    return "\n".join(out)

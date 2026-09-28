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

    def run(self, **kwargs: Any) -> str:
        try:
            kwargs = self._coerce(kwargs)
            result = self.fn(**kwargs)
            if result is None:
                return "Done."
            return str(result)
        except TypeError as e:
            return f"ERROR: wrong arguments for {self.name}: {e}"
        except Exception as e:
            return f"ERROR in {self.name}: {e}\n{traceback.format_exc(limit=2)}"


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


def tool(_fn: Callable | None = None, *, destructive: bool = False, tags: list[str] | None = None):
    """Register a function as a model-callable tool."""

    def wrap(fn: Callable) -> Callable:
        sig = inspect.signature(fn)
        summary, param_docs = _parse_doc(fn.__doc__ or "")

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
        )
        return fn

    return wrap(_fn) if _fn else wrap


_SHOP_WORDS = re.compile(r"\b(store|shop|ragazza|orders?|stock|products?|prices?|sales|revenue|"
                         r"customers?|ship(?:ped|ping)?|tracking|inventory|mrp)\b", re.I)


def schemas(request: str = "") -> list[dict[str, Any]]:
    """All tool schemas, in the shape Ollama's `tools=` parameter expects.

    Tools tagged "route_only" run only from their exact phrases (agent.route),
    never on the model's initiative: asked to turn gaming mode *off*, the model
    called free_gpu five times, unloading itself between every step.

    Tools tagged "store" reach real customers, so the model only sees them when
    the request is about the shop: asked "what can you do?", the local model
    once made up a "mark order RGZ1004 shipped" call (27 Sep 2026).
    """
    shop = bool(_SHOP_WORDS.search(request or ""))
    return [t.schema for t in REGISTRY.values()
            if "route_only" not in t.tags and ("store" not in t.tags or shop)]


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

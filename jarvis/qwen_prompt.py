"""Qwen2.5's chat template, rendered here exactly as Hugging Face renders it.

Why it exists: athena:3b was fine-tuned on prompts made by the Hugging Face
template. Ollama's chat endpoint renders its own version - the tool list as
compact Go JSON, 2,600 characters shorter on a typical command - and on
3 Oct 2026 the fine-tuned model answered 5 of 27 commands with nothing at all
through it, while the very same model given the training-format prompt wrote
a perfect <tool_call> every time. So for models listed in llm.raw_models,
llm.py sends this prompt to /api/generate in raw mode and reads the tool
calls out of the reply itself.

Checked against transformers' apply_chat_template on the training data
(finetune/check_template.py): identical, character for character.
"""
from __future__ import annotations

import json
import re

_TOOLS_HEAD = ("\n\n# Tools\n\nYou may call one or more functions to assist with the user query.\n\n"
               "You are provided with function signatures within <tools></tools> XML tags:\n<tools>")
_TOOLS_TAIL = ("\n</tools>\n\nFor each function call, return a json object with function name and "
               "arguments within <tool_call></tool_call> XML tags:\n<tool_call>\n"
               "{\"name\": <function-name>, \"arguments\": <args-json-object>}\n</tool_call>")
_DEFAULT_SYSTEM = "You are Qwen, created by Alibaba Cloud. You are a helpful assistant."


def _j(x) -> str:
    return json.dumps(x, ensure_ascii=False)


def _call(c) -> tuple[str, object]:
    f = c["function"] if isinstance(c, dict) else c.function
    name = f["name"] if isinstance(f, dict) else f.name
    args = f.get("arguments") if isinstance(f, dict) else f.arguments
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            pass
    return name, args if args is not None else {}


def render(messages: list[dict], tools: list[dict] | None) -> str:
    out = ""
    first = messages[0] if messages else {}
    if tools:
        out += "<|im_start|>system\n" + (first["content"] if first.get("role") == "system" else _DEFAULT_SYSTEM)
        out += _TOOLS_HEAD + "".join("\n" + _j(t) for t in tools) + _TOOLS_TAIL + "<|im_end|>\n"
    else:
        out += "<|im_start|>system\n" + (first["content"] if first.get("role") == "system" else _DEFAULT_SYSTEM) + "<|im_end|>\n"
    for i, m in enumerate(messages):
        role, content = m.get("role"), m.get("content") or ""
        calls = m.get("tool_calls")
        if (role == "user") or (role == "system" and i != 0) or (role == "assistant" and not calls):
            out += f"<|im_start|>{role}\n{content}<|im_end|>\n"
        elif role == "assistant":
            out += "<|im_start|>assistant"
            if content:
                out += "\n" + content
            for c in calls:
                name, args = _call(c)
                out += "\n<tool_call>\n{\"name\": \"" + name + "\", \"arguments\": " + _j(args) + "}\n</tool_call>"
            out += "<|im_end|>\n"
        elif role == "tool":
            prev = messages[i - 1].get("role") if i else None
            nxt = messages[i + 1].get("role") if i + 1 < len(messages) else None
            if prev != "tool":
                out += "<|im_start|>user"
            out += "\n<tool_response>\n" + content + "\n</tool_response>"
            if nxt != "tool":
                out += "<|im_end|>\n"
    return out + "<|im_start|>assistant\n"


_CALL = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.S)


def parse(text: str) -> tuple[str, list[dict]]:
    """(the reply's prose, [{"function": {"name", "arguments"}}]) from raw output."""
    calls = []
    for m in _CALL.finditer(text):
        try:
            d = json.loads(m.group(1))
        except ValueError:
            continue
        if isinstance(d, dict) and d.get("name"):
            args = d.get("arguments") or {}
            calls.append({"function": {"name": d["name"], "arguments": args if isinstance(args, dict) else {}}})
    prose = _CALL.sub("", text).replace("<tool_call>", "").strip()
    return prose, calls

"""Thin wrapper over the local Ollama server."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Iterator

import ollama

from .config import cfg


def _ollama_exe() -> str | None:
    """Find ollama.exe on PATH or in the default per-user install location."""
    found = shutil.which("ollama")
    if found:
        return found
    default = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe"
    return str(default) if default.exists() else None


def _nvidia_ready() -> bool:
    """True if an NVIDIA GPU is present and its driver is answering."""
    smi = shutil.which("nvidia-smi")
    if not smi:
        return False
    try:
        r = subprocess.run([smi, "--query-gpu=name", "--format=csv,noheader"],
                           capture_output=True, text=True, timeout=6,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return r.returncode == 0 and bool(r.stdout.strip())
    except Exception:
        return False


def _wait_for_gpu_driver(limit: float = 90.0) -> None:
    """Hold on until the NVIDIA driver answers, or give up after `limit`.

    A machine without an NVIDIA GPU returns at once - there is nothing to
    wait for, and the CPU is the right answer there anyway.
    """
    if not shutil.which("nvidia-smi"):
        return
    deadline = time.time() + limit
    while time.time() < deadline:
        if _nvidia_ready():
            time.sleep(3)          # answering is not quite the same as idle
            return
        time.sleep(3)


def _clean_env() -> dict:
    """The environment for a server Athena starts, minus Athena's own CUDA.

    The voice process puts pip's cuBLAS and cuDNN on PATH for Whisper. An
    Ollama server inheriting that PATH would find those before the ones it
    ships with, and a version mismatch there is another way to lose the GPU.
    """
    env = dict(os.environ)
    parts = env.get("PATH", "").split(os.pathsep)
    env["PATH"] = os.pathsep.join(p for p in parts
                                  if "site-packages" not in p.lower()
                                  or "nvidia" not in p.lower())
    return env


def heal_gpu(llm: "LLM") -> str:
    """Restart Ollama if it is running on the CPU while a GPU sits idle.

    Ollama never looks for the GPU again once started, so a server that came
    up CPU-only stays that way until restarted. Returns what was done.
    """
    if llm.processor() != "CPU" or not _nvidia_ready():
        return ""
    exe = _ollama_exe()
    if not exe:
        return ""
    try:
        import psutil

        for p in psutil.process_iter(["name", "cmdline"]):
            name = (p.info["name"] or "").lower()
            cmd = " ".join(p.info["cmdline"] or []).lower()
            if name == "llama-server.exe" or (name == "ollama.exe" and " serve" in cmd):
                p.kill()
    except Exception:
        return ""
    time.sleep(3)
    if not llm.ensure_server():
        # Every reply now fails until someone restarts Ollama by hand, and
        # the person who can do that may not be at the PC.
        from . import alerts
        alerts.submit("gpu", f"gpu:{time.strftime('%Y-%m-%dT%H')}",
                      "Ollama fell back to the CPU and did not come back after "
                      "a restart. Athena cannot answer until it is restarted.")
        return "Ollama was on the CPU; tried to restart it, but it did not come back."
    try:
        llm.chat([{"role": "user", "content": "hi"}])     # reload onto the GPU
    except Exception:
        pass
    return f"Ollama had fallen back to the CPU; restarted it - now {llm.processor()}."


class LLM:
    def __init__(self, model: str | None = None, host: str | None = None):
        self.model = model or cfg.get("llm.model", "qwen2.5:7b")
        self.host = host or cfg.get("llm.host", "http://127.0.0.1:11434")
        # A hard ceiling on one call: a runaway reply held Athena for 2+ minutes
        # and every command queued behind it (27 Sep 2026).
        self.client = ollama.Client(host=self.host, timeout=float(cfg.get("llm.timeout_seconds", 180)))

    # ---------- health ----------

    def _reachable(self) -> bool:
        try:
            self.client.list()
            return True
        except Exception:
            return False

    def ensure_server(self, wait_seconds: int = 40) -> bool:
        """Start the Ollama server if nothing is listening. True once reachable.

        Ollama looks for the GPU exactly once, when the server starts, with a
        five-second limit - and settles on the CPU for as long as that server
        runs if the GPU does not answer in time. Athena starts at login and
        used to launch the server seconds after boot, before the NVIDIA driver
        was ready: detection timed out, every reply ran on the CPU at a
        fraction of the speed, and nothing said why. So it now waits for the
        driver first.
        """
        if self._reachable():
            return True
        exe = _ollama_exe()
        if not exe:
            return False
        _wait_for_gpu_driver()
        try:
            subprocess.Popen(
                [exe, "serve"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                env=_clean_env(),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception:
            return False
        deadline = time.time() + wait_seconds
        while time.time() < deadline:
            if self._reachable():
                return True
            time.sleep(1.5)
        return False

    def available(self) -> tuple[bool, str]:
        """(is_ollama_up, message). Starts the server if it is not running."""
        if not self.ensure_server():
            return False, (f"Ollama is not reachable at {self.host} and could not be started. "
                           "Install it from ollama.com, or run: ollama serve")
        try:
            models = [m.model for m in self.client.list().models]
        except Exception as e:
            return False, f"Ollama is not reachable at {self.host} ({e})."
        if not any(m.split(":")[0] == self.model.split(":")[0] for m in models):
            return False, f"Model '{self.model}' is not pulled. Run: ollama pull {self.model}"
        return True, "ok"

    def processor(self) -> str:
        """Where the loaded model is running: 'GPU', 'CPU', 'mixed' or 'not loaded'.

        Worth checking - a model silently falling back to CPU is the difference
        between a half-second reply and three minutes.
        """
        try:
            running = self.client.ps().models
        except Exception:
            return "unknown"
        for m in running:
            if m.model.split(":")[0] != self.model.split(":")[0]:
                continue
            total = getattr(m, "size", 0) or 0
            gpu = getattr(m, "size_vram", 0) or 0
            if total <= 0:
                return "unknown"
            share = gpu / total
            if share > 0.95:
                return "GPU"
            if share < 0.05:
                return "CPU"
            return f"mixed ({share:.0%} GPU)"
        return "not loaded"

    # ---------- generation ----------

    def chat(self, messages: list[dict], tools: list[dict] | None = None,
             temperature: float | None = None,
             context: int | None = None,
             think: bool | None = None,
             max_tokens: int | None = None) -> Any:
        """One non-streaming turn. Returns the raw Ollama message object.

        temperature and context override the defaults for this call. Class work
        wants both different from conversation: 0.3 is a sensible amount of
        variety in a spoken reply and pure downside in arithmetic, where it is
        part of why two attempts at the same question produce two different
        wrong answers. A model that reasons at length before answering also
        needs more room than the 8k a command needs.

        think turns a reasoning model's deliberation on or off, and max_tokens
        puts a ceiling on one reply. Both exist because a thinking model can
        run away: qwen3:8b was once 9,000 tokens into reasoning about a single
        question, seventeen minutes in, with the answer not yet begun. The
        deliberation is what makes it right on a maths worksheet, so it is
        worth having - but not without a limit, and not on a question that
        does not need it.
        """
        options: dict[str, Any] = {
            "temperature": (cfg.get("llm.temperature", 0.3)
                            if temperature is None else temperature),
            "num_ctx": (cfg.get("llm.context_window", 8192)
                        if context is None else context),
        }
        # Spoken replies are short; without a cap one bad generation ran on
        # for minutes. Study passes its own, larger limit.
        options["num_predict"] = int(max_tokens or cfg.get("llm.max_tokens", 600))

        extra: dict[str, Any] = {}
        if think is not None:
            extra["think"] = think

        try:
            resp = self.client.chat(
                model=self.model,
                messages=messages,
                tools=tools or None,
                # Keeping the model resident avoids a reload plus a full
                # re-read of ~4k tokens of tool definitions on the first
                # command after a pause.
                keep_alive=cfg.get("llm.keep_alive", "30m"),
                options=options,
                **extra,
            )
        except Exception:
            if not extra:
                raise
            # Not every model accepts "think"; asking a model that does not
            # is not a reason to fail the question.
            resp = self.client.chat(
                model=self.model, messages=messages, tools=tools or None,
                keep_alive=cfg.get("llm.keep_alive", "30m"), options=options,
            )
        return resp.message

    def chat_stream(self, messages: list[dict], tools: list[dict] | None,
                    on_sentence, stop=lambda: False) -> Any:
        """chat(), streamed: each finished sentence of the reply goes to
        on_sentence as soon as the model has written it, so the voice can
        start talking while the rest is still being generated. Returns a
        message with the full content and any tool calls, like chat().

        A reply that opens like JSON is a tool call written out as text and
        is never handed on; nor is anything once a real tool call shows up.
        """
        from types import SimpleNamespace
        content, calls, buf = [], [], ""
        quiet = False
        for chunk in self.client.chat(
            model=self.model, messages=messages, tools=tools or None, stream=True,
            keep_alive=cfg.get("llm.keep_alive", "30m"),
            options={"temperature": cfg.get("llm.temperature", 0.3),
                     "num_ctx": cfg.get("llm.context_window", 8192),
                     "num_predict": int(cfg.get("llm.max_tokens", 600))},
        ):
            if stop():
                break                       # interrupted: stop generating now
            m = chunk.message
            if getattr(m, "tool_calls", None):
                calls += list(m.tool_calls)
                quiet = True
            piece = m.content or ""
            if not piece:
                continue
            content.append(piece)
            if quiet:
                continue
            buf += piece
            if buf.lstrip()[:1] in ("{", "[", "`"):
                quiet = True
                continue
            # Hand on complete sentences; keep the unfinished tail.
            while True:
                cut = re.search(r"[.!?](?=\s)|\n", buf[12:])
                if not cut:
                    break
                end = 12 + cut.end()
                on_sentence(buf[:end].strip())
                buf = buf[end:]
        if buf.strip() and not quiet and not stop():
            on_sentence(buf.strip())
        return SimpleNamespace(content="".join(content), tool_calls=calls or None)

    def stream(self, messages: list[dict]) -> Iterator[str]:
        """Token stream for plain chat (no tools) - used by the CLI for snappy output."""
        for chunk in self.client.chat(
            model=self.model,
            messages=messages,
            stream=True,
            keep_alive=cfg.get("llm.keep_alive", "30m"),
            options={
                "temperature": cfg.get("llm.temperature", 0.3),
                "num_ctx": cfg.get("llm.context_window", 8192),
            },
        ):
            piece = chunk.message.content
            if piece:
                yield piece

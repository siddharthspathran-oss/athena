"""Global hotkey popup: press the combo anywhere, type a command, it acts.

Runs quietly in the background. The popup appears centred, takes one
instruction, shows the answer, and disappears on Escape.
"""
from __future__ import annotations

import queue
import threading
import tkinter as tk

from ..agent import Agent, Step
from ..config import cfg
from ..tools import scheduler

BG = "#12141a"
FG = "#e6e6e6"
ACCENT = "#5ac8fa"
DIM = "#6b7280"


class Popup:
    """A single always-on-top command bar, hidden until the hotkey fires."""

    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=ACCENT)

        self.events: queue.Queue = queue.Queue()
        self.busy = False

        frame = tk.Frame(self.root, bg=BG)
        frame.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)

        self.entry = tk.Entry(frame, bg=BG, fg=FG, font=("Segoe UI", 15),
                              insertbackground=ACCENT, borderwidth=0)
        self.entry.pack(fill=tk.X, padx=16, pady=(14, 10), ipady=6)
        self.entry.bind("<Return>", lambda _e: self._send())
        self.entry.bind("<Escape>", lambda _e: self.hide())

        self.output = tk.Label(frame, text="", bg=BG, fg=DIM, font=("Segoe UI", 10),
                               wraplength=620, justify=tk.LEFT, anchor="w")
        self.output.pack(fill=tk.X, padx=16, pady=(0, 14))

        self.agent = Agent(on_step=self._on_step)
        scheduler.set_notifier(lambda text: self.events.put(("reminder", text)))
        scheduler.start_scheduler("hotkey")
        self.root.after(80, self._drain)

    # ---------- window ----------

    def show(self) -> None:
        w, h = 680, 130
        x = (self.root.winfo_screenwidth() - w) // 2
        y = int(self.root.winfo_screenheight() * 0.28)
        self.root.geometry(f"{w}x{h}+{x}+{y}")
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        self.entry.focus_force()
        self.entry.select_range(0, tk.END)

    def hide(self) -> None:
        self.root.withdraw()
        self.entry.delete(0, tk.END)
        self.output.config(text="")

    # ---------- work ----------

    def _on_step(self, s: Step) -> None:
        self.events.put(("status", f"{s.tool}..."))

    def _send(self) -> None:
        text = self.entry.get().strip()
        if not text or self.busy:
            return
        self.busy = True
        self.entry.delete(0, tk.END)
        self.output.config(text="thinking...", fg=DIM)
        threading.Thread(target=self._work, args=(text,), daemon=True).start()

    def _work(self, text: str) -> None:
        try:
            self.events.put(("answer", self.agent.ask(text)))
        except Exception as e:
            self.events.put(("answer", f"Something broke: {e}"))

    def _drain(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "status":
                    self.output.config(text=payload, fg=DIM)
                elif kind == "answer":
                    self.output.config(text=payload[:600], fg=FG)
                    self.busy = False
                elif kind == "reminder":
                    self.show()
                    self.output.config(text=f"Reminder: {payload}", fg=ACCENT)
                    self.root.bell()
        except queue.Empty:
            pass
        self.root.after(80, self._drain)


def run() -> None:
    try:
        import keyboard
    except ImportError:
        print("ERROR: the hotkey interface needs the keyboard package.")
        print("Run: pip install keyboard")
        return

    combo = cfg.get("hotkey.combo", "ctrl+alt+space")
    popup = Popup()

    ok, msg = popup.agent.llm.available()
    if not ok:
        print(msg)
        return

    # keyboard callbacks fire on their own thread - hop to the UI thread
    keyboard.add_hotkey(combo, lambda: popup.root.after(0, popup.show))

    print(f"Athena is listening. Press {combo} anywhere to open the command bar.")
    print("Press Ctrl+C in this window to stop.")
    try:
        popup.root.mainloop()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run()

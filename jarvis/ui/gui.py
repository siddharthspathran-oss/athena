"""Desktop chat window (tkinter, no extra dependencies).

The agent runs on a worker thread so the window never freezes while a
model is thinking or a tool is running.
"""
from __future__ import annotations

import queue
import re
import time
import threading
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext

from .. import chats, registry, render, vision
from ..tools.knowledge import _extract as extract_text
from ..tools import study
from ..tools.study import answer_page
from ..agent import Agent, Step
from ..config import cfg
from ..tools import scheduler

# Tk cannot draw a rounded bubble, but a Text tag can carry a background, a
# margin and space above and below - which is most of what a chat bubble is.
# Matched to the phone interface so the two do not look like different
# programs. Tk has no gradients, blur or shadow, so the depth that CSS gets
# from glow has to come from flat colours a step apart: the window is nearly
# black, the panels lift slightly out of it, and the accent carries the rest.
BG = "#05080c"          # window
PANEL = "#0a1016"       # header and input strip
CARD = "#121e26"        # assistant bubble
# Your bubble is deliberately quiet. A saturated accent behind every line
# you type fights the answer for attention, and the answer is the thing worth
# reading - which is why ChatGPT and Claude both use a near-neutral here and
# spend their colour on small accents instead.
USER_BG = "#1c2b33"     # your bubble
USER_FG = "#eaf4f6"
FG = "#dff2f4"
DIM = "#5f7f8c"
ACCENT = "#2fe6c0"
ACCENT2 = "#19b6ff"
EDGE = "#16323a"
ERR = "#ff7b7b"

UI = ("Segoe UI", 10)
BODY = ("Segoe UI", 11)
SMALL = ("Segoe UI", 9)
MONO = ("Cascadia Mono", 9)

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}


class AthenaWindow:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title(f"{cfg.get('assistant.name', 'Athena')} - local assistant")
        self.root.geometry("820x620")
        self.root.configure(bg=BG)
        self.root.minsize(560, 400)

        self.events: queue.Queue = queue.Queue()
        self.busy = False
        self._confirm_answer: bool | None = None
        self._link_id = 0          # each answer's link needs its own tag
        self._labels: list = []    # bubble labels, re-wrapped on resize
        self.attachment = None     # file waiting to be sent
        self.chip = None

        self._build()
        self.agent = Agent(on_step=self._queue_step, confirm=self._confirm)

        ok, msg = self.agent.llm.available()
        if ok:
            self.sub.config(text=f"{self.agent.llm.model}  ·  "
                                 f"{len(registry.REGISTRY)} tools")
            self._bubble(cfg.get("assistant.greeting", "Ready."), "bot")
        else:
            self._ring_fault()
            self.sub.config(text="model unavailable")
            self._bubble(msg, "err")

        # Pick up where the last session left off rather than starting blank.
        recent = chats.listing()
        if recent:
            self._open_chat(recent[0]["id"])
        else:
            self.agent.memory.start_chat()
        self._refresh_sidebar()

        scheduler.set_notifier(lambda text: self.events.put(("reminder", text)))
        scheduler.start_scheduler("gui")
        self.root.after(80, self._drain)

    # ---------- layout ----------

    def _build(self) -> None:
        # Order matters: the chat expands to fill whatever is left, so the
        # input strip has to claim its space first. Packed the other way
        # round the chat swallowed the whole window and the text box and
        # Send button never appeared at all.
        self._header()

        wrap = tk.Frame(self.root, bg=BG)
        wrap.pack(fill=tk.BOTH, expand=True)

        # Saved conversations down the left, the current one filling the rest.
        self._sidebar(wrap)

        # The conversation and its input strip share a column, so the text box
        # stops where the chat does instead of running under the sidebar.
        main = tk.Frame(wrap, bg=BG)
        main.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._input(main)

        self.chat = scrolledtext.ScrolledText(
            main, wrap=tk.WORD, bg=BG, fg=FG, font=BODY,
            insertbackground=FG, borderwidth=0, highlightthickness=0,
            padx=18, pady=14, spacing1=2, spacing3=3, state=tk.DISABLED,
        )
        self.chat.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.chat.bind("<Configure>", self._rewrap)
        self._bubbles()

    def _sidebar(self, parent) -> None:
        """The list of saved conversations, and the button that starts one."""
        self.side = tk.Frame(parent, bg=PANEL, width=258)
        self.side.pack(side=tk.LEFT, fill=tk.Y)
        self.side.pack_propagate(False)

        top = tk.Frame(self.side, bg=PANEL)
        top.pack(fill=tk.X, padx=10, pady=(12, 8))
        tk.Button(top, text="+  New chat", command=self._new_chat,
                  bg=CARD, fg=ACCENT, font=("Segoe UI Semibold", 10),
                  relief=tk.FLAT, borderwidth=0, padx=10, pady=8,
                  activebackground=EDGE, activeforeground=ACCENT,
                  cursor="hand2", anchor="w").pack(fill=tk.X)

        tk.Label(self.side, text="RECENT", bg=PANEL, fg=DIM,
                 font=("Segoe UI", 8, "bold"), anchor="w").pack(
                     fill=tk.X, padx=14, pady=(6, 4))

        holder = tk.Frame(self.side, bg=PANEL)
        holder.pack(fill=tk.BOTH, expand=True)
        self._side_canvas = tk.Canvas(holder, bg=PANEL, highlightthickness=0)
        self._side_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.side_list = tk.Frame(self._side_canvas, bg=PANEL)
        self._side_window = self._side_canvas.create_window(
            (0, 0), window=self.side_list, anchor="nw")
        self.side_list.bind(
            "<Configure>",
            lambda e: self._side_canvas.configure(
                scrollregion=self._side_canvas.bbox("all")))
        self._side_canvas.bind(
            "<Configure>",
            lambda e: self._side_canvas.itemconfigure(self._side_window,
                                                      width=e.width))
        self._side_canvas.bind_all(
            "<MouseWheel>",
            lambda e: (self._side_canvas.yview_scroll(-e.delta // 120, "units")
                       if self._over_sidebar(e) else None))

        tk.Frame(parent, bg=EDGE, width=1).pack(side=tk.LEFT, fill=tk.Y)

    def _over_sidebar(self, event) -> bool:
        """Only scroll the chat list when the pointer is actually over it."""
        try:
            x = self.side.winfo_rootx()
            return x <= event.x_root <= x + self.side.winfo_width()
        except tk.TclError:
            return False

    def _header(self) -> None:
        bar = tk.Frame(self.root, bg=PANEL, height=52)
        bar.pack(fill=tk.X)
        bar.pack_propagate(False)

        # Two arcs turning opposite ways, same as the phone. Tk cannot glow,
        # so the impression comes from motion and a bright core instead.
        self.dot = tk.Canvas(bar, width=30, height=30, bg=PANEL,
                             highlightthickness=0)
        self.dot.pack(side=tk.LEFT, padx=(18, 11))
        self._arc_outer = self.dot.create_arc(
            3, 3, 27, 27, start=0, extent=110, style=tk.ARC,
            outline=ACCENT, width=2)
        self._arc_inner = self.dot.create_arc(
            9, 9, 21, 21, start=180, extent=110, style=tk.ARC,
            outline=ACCENT2, width=2)
        self._core = self.dot.create_oval(12, 12, 18, 18, fill=ACCENT, outline="")
        self._spin = 0
        self._animate()

        tk.Label(bar, text=cfg.get("assistant.name", "Athena").upper(),
                 bg=PANEL, fg=FG,
                 font=("Segoe UI Semibold", 11)).pack(side=tk.LEFT)
        self.sub = tk.Label(bar, text="", bg=PANEL, fg=DIM, font=SMALL)
        self.sub.pack(side=tk.RIGHT, padx=18)
        tk.Frame(self.root, bg=EDGE, height=1).pack(fill=tk.X)

    # ---------- saved conversations ----------

    def _refresh_sidebar(self) -> None:
        """Redraw the conversation list, highlighting the open one."""
        for child in self.side_list.winfo_children():
            child.destroy()
        for c in chats.listing():
            live = c["id"] == self.agent.memory.chat_id
            row = tk.Frame(self.side_list, bg=CARD if live else PANEL)
            row.pack(fill=tk.X, padx=8, pady=1)

            label = tk.Label(row, text=c["title"], bg=row["bg"],
                             fg=FG if live else DIM, font=SMALL, anchor="w",
                             justify=tk.LEFT, wraplength=150, cursor="hand2")
            label.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 2), pady=6)
            when = tk.Label(row, text=chats.when(c["updated"]), bg=row["bg"],
                            fg=DIM, font=("Segoe UI", 8))
            when.pack(side=tk.RIGHT, padx=(2, 4))
            drop = tk.Label(row, text="✕", bg=row["bg"], fg=PANEL,
                            font=("Segoe UI", 8), cursor="hand2")
            drop.pack(side=tk.RIGHT, padx=(0, 4))

            def enter(_e, r=row, d=drop):
                if r["bg"] == PANEL:
                    for w in (r, *r.winfo_children()):
                        w.configure(bg=EDGE)
                d.configure(fg=DIM)

            def leave(_e, r=row, d=drop, was=row["bg"]):
                for w in (r, *r.winfo_children()):
                    w.configure(bg=was)
                d.configure(fg=was)

            for w in (row, label, when):
                w.bind("<Enter>", enter)
                w.bind("<Leave>", leave)
                w.bind("<Button-1>", lambda _e, i=c["id"]: self._open_chat(i))
            drop.bind("<Enter>", enter)
            drop.bind("<Leave>", leave)
            drop.bind("<Button-1>",
                      lambda _e, i=c["id"], t=c["title"]: self._drop_chat(i, t))

    def _new_chat(self) -> None:
        if self.busy:
            return
        self.agent.memory.start_chat()
        self._clear_chat()
        self._bubble(cfg.get("assistant.greeting", "Ready."), "bot")
        self._refresh_sidebar()
        self.entry.focus_set()

    def _open_chat(self, chat_id: str) -> None:
        """Reopen a saved conversation and put it back on screen."""
        if self.busy or chat_id == self.agent.memory.chat_id:
            return
        self.agent.memory.start_chat(chat_id)
        self._clear_chat()
        for m in self.agent.memory.full:
            role, text = m.get("role"), m.get("content")
            if not isinstance(text, str) or not text.strip():
                continue
            if role == "user":
                self._bubble(text, "user")
            elif role == "assistant":
                self._bubble(text, "bot")
        self._refresh_sidebar()
        self.entry.focus_set()

    def _drop_chat(self, chat_id: str, title: str) -> None:
        if self.busy:
            return
        if not messagebox.askyesno("Delete chat",
                                   f"Delete “{title}”?\nThis cannot be undone."):
            return
        chats.delete(chat_id)
        if chat_id == self.agent.memory.chat_id:
            self._new_chat()
        else:
            self._refresh_sidebar()

    def _clear_chat(self) -> None:
        self.chat.config(state=tk.NORMAL)
        self.chat.delete("1.0", tk.END)
        self.chat.config(state=tk.DISABLED)
        self._labels.clear()

    def _ring_fault(self) -> None:
        """Turn the ring red. Used when the model cannot be reached."""
        self._faulted = True
        for item in (self._arc_outer, self._arc_inner):
            self.dot.itemconfigure(item, outline=ERR)
        self.dot.itemconfigure(self._core, fill=ERR)

    def _animate(self) -> None:
        """Turn the ring. Faster while Athena is working, so the window itself
        says whether anything is happening."""
        if getattr(self, "_faulted", False):
            return                      # a red ring should sit still
        try:
            step = 9 if getattr(self, "busy", False) else 3
            self._spin = (self._spin + step) % 360
            self.dot.itemconfigure(self._arc_outer, start=self._spin)
            self.dot.itemconfigure(self._arc_inner, start=-self._spin * 1.6 % 360)
            # The core breathes, on a slower cycle than the arcs turn.
            phase = abs(((self._spin // 3) % 60) - 30) / 30.0
            shade = int(0x60 + phase * 0x9f)
            self.dot.itemconfigure(
                self._core, fill=f"#{shade:02x}{0xe6:02x}{0xc0:02x}")
        except tk.TclError:
            return                      # window closed mid-frame
        self.root.after(45, self._animate)

    def _bubbles(self) -> None:
        """Message styles. Margins do the work that alignment cannot."""
        c = self.chat.tag_config
        c("user", background=USER_BG, foreground="#eaf6fb", font=BODY,
          lmargin1=150, lmargin2=150, rmargin=10,
          spacing1=8, spacing3=8, borderwidth=8, relief=tk.FLAT)
        c("bot", background=CARD, foreground=FG, font=BODY,
          lmargin1=10, lmargin2=10, rmargin=150,
          spacing1=8, spacing3=8, borderwidth=8, relief=tk.FLAT)
        c("dim", foreground=DIM, font=MONO, lmargin1=14, lmargin2=28, spacing3=2)
        c("accent", foreground=ACCENT, font=BODY, lmargin1=10)
        c("err", background="#2b1a1c", foreground=ERR, font=BODY,
          lmargin1=10, lmargin2=10, rmargin=150,
          spacing1=8, spacing3=8, borderwidth=8, relief=tk.FLAT)
        c("stamp", foreground=DIM, font=SMALL, lmargin1=10, spacing3=1)
        c("rightward", justify=tk.RIGHT)

    def _input(self, parent) -> None:
        # Packed bottom-up: the status line sits below the input row, and the
        # hairline separates both from the conversation above.
        self.status = tk.Label(parent, text="", bg=PANEL, fg=DIM,
                               font=SMALL, anchor="w")
        self.status.pack(side=tk.BOTTOM, fill=tk.X, padx=20, pady=(0, 4))
        self._model_bar(parent)

        bar = tk.Frame(parent, bg=PANEL)
        bar.pack(side=tk.BOTTOM, fill=tk.X)
        self.chip_row = tk.Frame(parent, bg=PANEL)
        self.chip_row.pack(side=tk.BOTTOM, fill=tk.X)
        tk.Frame(parent, bg=EDGE, height=1).pack(side=tk.BOTTOM, fill=tk.X)

        # One box holding everything, the way ChatGPT and Claude do it, rather
        # than a row of separate controls. It is centred on the same column as
        # the conversation so the two line up.
        self._input_row = tk.Frame(bar, bg=PANEL)
        self._input_row.pack(fill=tk.X, padx=18, pady=(10, 12))
        bar.bind("<Configure>", self._recentre_input)

        box = tk.Frame(self._input_row, bg=CARD, highlightthickness=1,
                       highlightbackground=EDGE, highlightcolor=ACCENT)
        box.pack(fill=tk.X)

        for glyph, tip, fn in (("＋", "Attach a file", self._attach),
                               ("◉", "Take a photo", self._photo)):
            b = tk.Button(box, text=glyph, command=fn, bg=CARD, fg=DIM,
                          font=("Segoe UI", 13), borderwidth=0,
                          highlightthickness=0, padx=7, pady=2,
                          activebackground=CARD, activeforeground=ACCENT,
                          cursor="hand2")
            b.pack(side=tk.LEFT, padx=(8 if glyph == "＋" else 0, 0), pady=6)
            b.bind("<Enter>", lambda _e, w=b: w.config(fg=ACCENT))
            b.bind("<Leave>", lambda _e, w=b: w.config(fg=DIM))
            self._tip(b, tip)

        self.send_btn = tk.Button(box, text="↑", command=self._send,
                                  bg=ACCENT, fg="#02201c",
                                  font=("Segoe UI Semibold", 12),
                                  borderwidth=0, highlightthickness=0,
                                  padx=11, pady=1, activebackground="#5ff0d2",
                                  activeforeground="#02201c", cursor="hand2")
        self.send_btn.pack(side=tk.RIGHT, padx=(6, 8), pady=7)

        self.entry = tk.Entry(box, bg=CARD, fg=FG, font=BODY,
                              insertbackground=ACCENT, borderwidth=0,
                              highlightthickness=0)
        self.entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 4), pady=12)
        self.entry.bind("<Return>", lambda _e: self._send())
        self.entry.focus_set()

    def _model_bar(self, parent) -> None:
        """Which model answers class work, chosen here rather than in YAML.

        Only study questions use it - voice, apps and messages always run on
        the local everyday model, so nothing picked here can send a spoken
        command anywhere.
        """
        bar = tk.Frame(parent, bg=PANEL)
        bar.pack(side=tk.BOTTOM, fill=tk.X, padx=18, pady=(0, 6))

        tk.Label(bar, text="class work", bg=PANEL, fg=DIM,
                 font=("Segoe UI", 8)).pack(side=tk.LEFT, padx=(0, 8))

        options = study.choices()
        self._model_label = tk.StringVar(
            value=next((o["label"] for o in options if o["live"]),
                       options[0]["label"]))
        menu = tk.OptionMenu(bar, self._model_label, *[o["label"] for o in options],
                             command=self._pick_model)
        menu.config(bg=CARD, fg=FG, font=SMALL, relief=tk.FLAT, borderwidth=0,
                    highlightthickness=0, activebackground=EDGE,
                    activeforeground=ACCENT, cursor="hand2", anchor="w",
                    padx=8, pady=2)
        menu["menu"].config(bg=CARD, fg=FG, font=SMALL, borderwidth=0,
                            activebackground=EDGE, activeforeground=ACCENT)
        # Grey out anything whose key is missing, rather than letting it be
        # chosen and fail later.
        for i, o in enumerate(options):
            if not o["ready"]:
                menu["menu"].entryconfig(i, foreground=DIM,
                                         label=o["label"] + "   - no key")
        menu.pack(side=tk.LEFT)

    def _pick_model(self, label: str) -> None:
        model = next((o["model"] for o in study.choices()
                      if o["label"] == label), "")
        if model:
            self.status.config(text=study.set_model(model))

    def _recentre_input(self, _event=None) -> None:
        """Hold the text box on the same centred column as the conversation."""
        try:
            pad = 18 + self._gutter()
            self._input_row.pack_configure(padx=pad)
        except tk.TclError:
            pass

    def _tip(self, widget, text: str) -> None:
        """A plain hover label - Tk has no tooltip of its own."""
        state = {}

        def show(_e=None):
            if state.get("win"):
                return
            x = widget.winfo_rootx()
            y = widget.winfo_rooty() - 28
            win = tk.Toplevel(widget)
            win.wm_overrideredirect(True)
            win.wm_geometry(f"+{x}+{y}")
            tk.Label(win, text=text, bg=CARD, fg=FG, font=SMALL,
                     padx=8, pady=4).pack()
            state["win"] = win

        def hide(_e=None):
            win = state.pop("win", None)
            if win:
                win.destroy()

        widget.bind("<Enter>", show, add="+")
        widget.bind("<Leave>", hide, add="+")

    # ---------- attachments ----------

    def _show_chip(self, label: str) -> None:
        """The strip above the input showing what is attached."""
        # Removes the old chip only. Calling _clear_chip here wiped
        # self.attachment immediately after it was set, so every attached
        # file was dropped before it could be sent.
        self._remove_chip()
        self.chip = tk.Frame(self.chip_row, bg=CARD)
        self.chip.pack(side=tk.LEFT, padx=(16, 0), pady=(8, 0))
        tk.Label(self.chip, text=label, bg=CARD, fg=FG, font=SMALL,
                 padx=10, pady=5).pack(side=tk.LEFT)
        x = tk.Label(self.chip, text="✕", bg=CARD, fg=DIM, font=SMALL,
                     padx=8, pady=5, cursor="hand2")
        x.pack(side=tk.LEFT)
        x.bind("<Button-1>", lambda _e: self._clear_chip())

    def _remove_chip(self) -> None:
        """Take the chip off screen, leaving the attachment alone."""
        if getattr(self, "chip", None):
            self.chip.destroy()
        self.chip = None

    def _clear_chip(self) -> None:
        """Forget the attachment entirely - the ✕, or after sending."""
        self._remove_chip()
        self.attachment = None

    def _attach(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.root, title="Attach a file",
            filetypes=[("Everything Athena can read",
                        "*.png *.jpg *.jpeg *.webp *.bmp *.gif "
                        "*.pdf *.docx *.txt *.md *.csv *.py *.json *.yaml"),
                       ("Images", "*.png *.jpg *.jpeg *.webp *.bmp *.gif"),
                       ("Documents", "*.pdf *.docx *.txt *.md *.csv"),
                       ("All files", "*.*")])
        if not path:
            return
        p = Path(path)
        self.attachment = p
        kind = "image" if p.suffix.lower() in IMAGE_EXT else "file"
        self._show_chip(f"{'🖼' if kind == 'image' else '📄'}  {p.name}")
        self.entry.focus_set()

    def _photo(self) -> None:
        """Take a webcam frame, then treat it as an attachment."""
        if self.busy:
            return
        self.status.config(text="opening the camera…")
        self.root.update_idletasks()

        def grab():
            shot = vision.capture()
            self.events.put(("photo", shot))

        threading.Thread(target=grab, daemon=True).start()

    # ---------- output ----------

    def _write(self, text: str, tag: str = "bot") -> None:
        self.chat.config(state=tk.NORMAL)
        self.chat.insert(tk.END, text,
                         tag if tag in ("user", "bot", "dim", "accent", "err",
                                        "stamp") else "bot")
        self.chat.see(tk.END)
        self.chat.config(state=tk.DISABLED)

    def _bubble(self, text: str, tag: str = "bot", stamp: bool = False):
        """One message.

        Only what you say gets a bubble. The assistant's replies are set as
        plain text on the page, the way ChatGPT and Claude do it, because a
        box around a thousand-word worked solution is a box around the whole
        screen - it adds nothing and costs the reading width. Yours are short
        and need to be told apart from the answer, which is exactly what a
        bubble is good at.

        `stamp` is off by default: a timestamp over every line is noise in a
        conversation you are having right now, and the sidebar already says
        when a chat was last touched.
        """
        row = tk.Frame(self.chat, bg=BG)

        if tag == "user":
            inner = tk.Frame(row, bg=BG)
            inner.pack(side=tk.RIGHT)
            bubble = tk.Frame(inner, bg=USER_BG)
            bubble.pack(side=tk.RIGHT)
            label = tk.Label(bubble, text=text.strip(), bg=USER_BG, fg=USER_FG,
                             font=BODY, justify=tk.LEFT, anchor="w",
                             wraplength=self._wrap())
            label.pack(padx=15, pady=10)
        else:
            ink = FG if tag != "err" else ERR
            bubble = tk.Frame(row, bg=BG)
            bubble.pack(side=tk.LEFT, fill=tk.X, expand=True)
            if tag == "err":
                # An error still needs to stand out from an ordinary answer.
                tk.Frame(bubble, bg=ERR, width=2).pack(side=tk.LEFT, fill=tk.Y)
            label = tk.Label(bubble, text=text.strip(), bg=BG, fg=ink,
                             font=BODY, justify=tk.LEFT, anchor="w",
                             wraplength=self._column())
            label.pack(side=tk.LEFT, padx=(10 if tag == "err" else 2, 0),
                       pady=2)

        if stamp:
            who = "you" if tag == "user" else cfg.get("assistant.name", "Athena")
            tk.Label(row, text=f"{who} · {time.strftime('%H:%M')}", bg=BG,
                     fg=DIM, font=SMALL).pack(
                         side=tk.RIGHT if tag == "user" else tk.LEFT)

        self._labels.append(label)

        self.chat.config(state=tk.NORMAL)
        start = self.chat.index(tk.END + "-1c")
        self.chat.window_create(tk.END, window=row,
                                padx=6 + self._gutter(),
                                pady=9 if tag == "user" else 7)
        self.chat.insert(tk.END, "\n")
        # An embedded window is only as wide as its contents, so packing it
        # to the right inside its own frame does nothing. Justifying the line
        # it sits on is what actually moves your messages to the right.
        if tag == "user":
            self.chat.tag_add("rightward", start, self.chat.index(tk.END + "-1c"))
        self.chat.see(tk.END)
        self.chat.config(state=tk.DISABLED)
        return bubble

    COLUMN = 700          # the reading column, as wide as text stays readable

    def _gutter(self) -> int:
        """Space either side, so the conversation sits in a centred column.

        Text the full width of a maximised window is unreadable - the eye
        loses the line. Every chat app answers this the same way, by capping
        the column and centring it, which Tk can do with padding on the row.
        """
        width = self.chat.winfo_width() or 800
        return max(0, (width - self.COLUMN) // 2)

    def _column(self) -> int:
        """Full reading width - what an assistant answer may use."""
        return max(300, min(self.chat.winfo_width() or 800, self.COLUMN) - 24)

    def _wrap(self) -> int:
        """How wide *your* bubble may grow before its text wraps.

        Narrower than the column so a long question still reads as one side
        of a conversation rather than a wall.
        """
        return max(240, int(self._column() * 0.78))

    def _rewrap(self, _event=None) -> None:
        """Keep messages proportional when the window is resized."""
        mine, theirs = self._wrap(), self._column()
        for label in self._labels:
            try:
                label.config(wraplength=mine if label["bg"] == USER_BG else theirs)
            except tk.TclError:
                pass

    def _write_answer(self, text: str) -> None:
        """Write an answer, turning a rendered page into something clickable.

        A study answer comes back as a sentence plus [file.html]. Printed
        as-is that is a filename the user has to go and find; as a link it is
        the page itself, with the equations and tables drawn properly.
        """
        m = re.search(r"\[([\w.\-]+\.html)\]\s*$", text)
        if not m:
            self._bubble(text, "bot")
            return

        page = render.ANSWERS / m.group(1)
        bubble = self._bubble(text[:m.start()].rstrip(), "bot")

        # The button belongs inside the bubble - a link floating underneath
        # reads as a separate message rather than part of the answer.
        btn = tk.Label(bubble, text="Open the full answer  ↗", bg=ACCENT,
                       fg="#06263a", font=("Segoe UI Semibold", 10),
                       padx=12, pady=6, cursor="hand2")
        btn.pack(anchor="w", padx=13, pady=(0, 11))
        btn.bind("<Button-1>", lambda _e, p=page: self._open_page(p))
        btn.bind("<Enter>", lambda _e: btn.config(bg="#7ad5ff"))
        btn.bind("<Leave>", lambda _e: btn.config(bg=ACCENT))
        self.chat.see(tk.END)

    def _open_page(self, path) -> None:
        try:
            webbrowser.open(path.as_uri())
        except Exception as e:
            self._write(f"\nCould not open {path.name}: {e}\n\n", "err")

    def _queue_step(self, s: Step) -> None:
        preview = s.result.replace("\n", " ")[:90]
        self.events.put(("step", f"   -> {s.tool}: {preview}\n"))

    def _confirm(self, name: str, args: dict) -> bool:
        """Called from the worker thread; bounce to the UI thread and wait."""
        done = threading.Event()

        def ask() -> None:
            self._confirm_answer = messagebox.askyesno(
                "Confirm action",
                f"Athena wants to run:\n\n{name}({args})\n\nAllow?",
                parent=self.root,
            )
            done.set()

        self.root.after(0, ask)
        done.wait(timeout=120)
        return bool(self._confirm_answer)

    # ---------- input ----------

    def _send(self) -> None:
        text = self.entry.get().strip()
        attached = self.attachment
        if self.busy or (not text and not attached):
            return
        self.entry.delete(0, tk.END)
        shown = text or ("What is in this picture?" if attached else "")
        self._bubble(shown + (f"\n📎 {attached.name}" if attached else ""), "user")
        self._clear_chip()

        if text.lower() in ("/clear", "clear"):
            self.agent.memory.clear()
            self._write("History cleared.\n\n", "dim")
            return
        if text.lower() == "/tools":
            self._write(registry.describe_all() + "\n\n", "dim")
            return

        self.busy = True
        self.send_btn.config(state=tk.DISABLED)
        # A worksheet through a reasoning model can take minutes. A status
        # line that never changes looks like a hang, so it counts.
        what = ("looking at the picture" if attached
                and attached.suffix.lower() in IMAGE_EXT
                else "working through " + attached.name if attached else "thinking")
        self._tick(what, time.time())
        threading.Thread(target=self._work, args=(shown, attached),
                         daemon=True).start()

    def _work(self, text: str, attached=None) -> None:
        try:
            if attached is None:
                self.events.put(("answer", self.agent.ask(text)))
                return

            if attached.suffix.lower() in IMAGE_EXT:
                # Images go to the vision model; the everyday one cannot see.
                self.events.put(("answer", vision.describe(attached, text)))
                return

            body = extract_text(attached).strip()
            if not body:
                self.events.put(
                    ("answer", f"I could not read any text out of {attached.name}."))
                return

            # A document deserves the same treatment as a class question:
            # full working, rendered to a page. Passing it to the ordinary
            # chat reply gave a short unformatted answer with the steps
            # missing, which is not what anyone attaches a worksheet for.
            # 20,000 characters is roughly what the context window holds
            # alongside the answer; 6,000 was cutting documents in half.
            limit = 20000
            clipped = body[:limit] + ("\n[...truncated]" if len(body) > limit else "")
            self.events.put(("answer", answer_page(text, clipped, attached.name)))
        except Exception as e:
            self.events.put(("error", str(e)))

    # ---------- event pump ----------

    def _drain(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "step":
                    self._write(payload, "dim")
                elif kind == "answer":
                    self._write_answer(payload)
                    self._done()
                elif kind == "error":
                    self._write(f"\nSomething broke: {payload}\n\n", "err")
                    self._done()
                elif kind == "photo":
                    self.status.config(text="")
                    if isinstance(payload, str):
                        self._bubble(payload, "err")
                    else:
                        self.attachment = payload
                        self._show_chip(f"🖼  {payload.name}")
                        self.entry.focus_set()
                elif kind == "reminder":
                    self._write(f"\n[reminder] {payload}\n\n", "accent")
                    self.root.bell()
        except queue.Empty:
            pass
        self.root.after(80, self._drain)

    def _tick(self, what: str, started: float) -> None:
        """Keep the status line moving while a long answer is being written."""
        if not self.busy:
            return
        secs = int(time.time() - started)
        dots = "." * (1 + secs % 3)
        clock = f"  {secs}s" if secs >= 3 else ""
        self.status.config(text=f"{what}{dots}{clock}")
        self.root.after(500, self._tick, what, started)

    def _done(self) -> None:
        self.busy = False
        self.send_btn.config(state=tk.NORMAL)
        self.status.config(text="")
        self.entry.focus_set()

    def run(self) -> None:
        self.root.mainloop()


def run() -> None:
    AthenaWindow().run()


if __name__ == "__main__":
    run()

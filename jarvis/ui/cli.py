"""Terminal chat interface."""
from __future__ import annotations

import sys

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

from .. import registry
from ..agent import Agent, Step
from ..config import cfg
from ..tools import scheduler

console = Console()

BANNER = r"""
     _   _    ______     _____ ____
    | | / \  |  _ \ \   / /_ _/ ___|
 _  | |/ _ \ | |_) \ \ / / | |\___ \
| |_| / ___ \|  _ < \ V /  | | ___) |
 \___/_/   \_\_| \_\ \_/  |___|____/
"""

HELP = """
[bold]Commands[/bold]
  /help      this message
  /tools     list every tool Athena can use
  /clear     wipe the conversation history
  /model     show which model is loaded
  /quiet     toggle showing tool calls as they happen
  /exit      quit

Anything else is sent to Athena. Examples:
  open chrome and search for the weather in Pune
  find every pdf in my downloads from the last week
  what is using all my memory right now
  remind me to stretch in 30 minutes
  organise my downloads folder
"""


def _confirm(name: str, args: dict) -> bool:
    console.print(f"\n[yellow]Athena wants to run[/yellow] [bold]{name}[/bold]({args})")
    try:
        return console.input("[yellow]Allow? [y/N] [/yellow]").strip().lower() in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        return False


def run() -> None:
    quiet = False

    def on_step(s: Step) -> None:
        if quiet:
            return
        preview = s.result.replace("\n", " ")[:110]
        console.print(f"[dim]  -> {s.tool}({_short(s.args)}) : {preview}[/dim]")

    agent = Agent(on_step=on_step, confirm=_confirm)

    console.print(f"[cyan]{BANNER}[/cyan]")
    ok, msg = agent.llm.available()
    if not ok:
        console.print(Panel(msg, title="[red]Not ready[/red]", border_style="red"))
        console.print("[dim]Fix the above, then run this again.[/dim]")
        return

    console.print(
        f"[dim]{agent.llm.model} | {len(registry.REGISTRY)} tools loaded | /help for commands[/dim]"
    )

    where = agent.llm.processor()
    if where not in ("GPU", "not loaded", "unknown"):
        console.print(
            f"[yellow]Heads up: the model is running on {where}, which is many times slower "
            "than the GPU. Restart Ollama to fix it.[/yellow]"
        )
    console.print(f"\n[green]{cfg.get('assistant.greeting', 'Ready.')}[/green]\n")

    scheduler.set_notifier(
        lambda text: console.print(Panel(text, title="[magenta]Reminder[/magenta]",
                                         border_style="magenta"))
    )
    scheduler.start_scheduler("cli")

    while True:
        try:
            user = console.input("[bold cyan]you >[/bold cyan] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Bye.[/dim]")
            return

        if not user:
            continue

        low = user.lower()
        if low in ("/exit", "/quit", "exit", "quit"):
            console.print("[dim]Bye.[/dim]")
            return
        if low == "/help":
            console.print(HELP)
            continue
        if low == "/tools":
            console.print(registry.describe_all())
            continue
        if low == "/clear":
            agent.memory.clear()
            console.print("[dim]History cleared.[/dim]")
            continue
        if low == "/model":
            console.print(f"[dim]{agent.llm.model} at {agent.llm.host}[/dim]")
            continue
        if low == "/quiet":
            quiet = not quiet
            console.print(f"[dim]Tool trace {'hidden' if quiet else 'shown'}.[/dim]")
            continue

        try:
            with console.status("[dim]thinking...[/dim]", spinner="dots"):
                answer = agent.ask(user)
        except KeyboardInterrupt:
            console.print("[dim]Cancelled.[/dim]")
            continue
        except Exception as e:
            console.print(f"[red]Something broke: {e}[/red]")
            continue

        console.print()
        console.print(Markdown(answer))
        console.print()


def _short(args: dict, limit: int = 60) -> str:
    s = ", ".join(f"{k}={v!r}" for k, v in args.items())
    return s if len(s) <= limit else s[:limit] + "..."


if __name__ == "__main__":
    sys.exit(run())

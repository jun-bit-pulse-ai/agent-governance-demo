"""Presentation helpers. No governance logic lives here."""

from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

console = Console(width=98)

VERDICT_STYLE = {
    "allow": ("green", "ALLOW"),
    "deny": ("bold red", "DENY"),
    "require_approval": ("yellow", "APPROVAL"),
    "warn": ("yellow", "WARN"),
    "log": ("dim", "LOG"),
}


def act(number: int, title: str, subtitle: str) -> None:
    console.print()
    console.rule(f"[bold cyan]Act {number}[/]  ·  {title}", style="cyan")
    console.print(Text(subtitle, style="dim italic"), justify="center")
    console.print()


def call(label: str, detail: str) -> None:
    console.print(f"  [bold white]▸ {label}[/]")
    console.print(f"    [dim]{detail}[/]")


def outcome(verdict: str, message: str, rule: str | None = None) -> None:
    style, tag = VERDICT_STYLE.get(verdict, ("white", verdict.upper()))
    line = Text("    ")
    line.append(f"[{tag}] ", style=style)
    line.append(message)
    console.print(line)
    if rule:
        console.print(f"    [dim]        matched rule: {rule}[/]")
    console.print()


def note(text: str) -> None:
    console.print(f"  [dim]{text}[/]")
    console.print()


def banner(text: str, style: str = "cyan") -> None:
    console.print(Panel(text, border_style=style, padding=(1, 2)))


def estate(tables: dict[str, int], caption: str) -> None:
    t = Table(box=None, pad_edge=False, show_header=True, header_style="dim")
    t.add_column("table", style="white")
    t.add_column("rows", justify="right", style="dim")
    for name, rows in tables.items():
        t.add_row(name, f"{rows:,}")
    if not tables:
        t.add_row("[red]— empty —[/]", "")
    console.print(Panel(t, title=caption, border_style="dim", padding=(0, 2), width=44))
    console.print()

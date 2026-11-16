"""Application shell: REPL and one-shot task execution.

Builds the message list (system prompt + repo context), runs the agent
loop, renders output. Session persistence and compaction land later.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from dsc import __version__
from dsc.client import ClientError, ChatClient, StreamEvent
from dsc.config import Config
from dsc.loop import ToolCallRecord, run, summarize_call
from dsc.permissions import Permission
from dsc.tools import READ_ONLY_TOOLS, WRITE_TOOLS

PROMPT = "dsc › "

# ANSI
C_RESET = "\x1b[0m"
C_DIM = "\x1b[2m"
C_BOLD = "\x1b[1m"
C_GREEN = "\x1b[32m"
C_RED = "\x1b[31m"
C_YELLOW = "\x1b[33m"
C_CYAN = "\x1b[36m"


class UI:
    """Terminal rendering; honors no_color."""

    def __init__(self, no_color: bool = False, width: int = 100) -> None:
        self.no_color = no_color or bool(__import__("os").environ.get("NO_COLOR"))
        self.width = width

    def _c(self, code: str, text: str) -> str:
        return text if self.no_color else f"{code}{text}{C_RESET}"

    def assistant_text(self, chunk: str) -> None:
        sys.stdout.write(chunk)
        sys.stdout.flush()

    def tool_line(self, rec: ToolCallRecord) -> None:
        summary = summarize_call(rec.name, rec.args)
        line = f"⚙ {summary}"
        if rec.output.startswith("error:"):
            line += self._c(C_RED, " (denied)" if "permission denied" in rec.output else "")
        print(f"  {self._c(C_DIM, line)}")

    def final(self, text: str) -> None:
        print()
        print(self._c(C_GREEN, "✔ ") + text)
        print(self._c(C_DIM, "─" * min(self.width, 60)))

    def info(self, text: str) -> None:
        print(self._c(C_DIM, text))


def build_system_prompt(cfg: Config) -> str:
    from importlib import resources

    try:
        return (resources.files("dsc") / "prompts" / "system.md").read_text(encoding="utf-8")
    except Exception:
        # fallback for editable installs
        p = Path(__file__).parent / "prompts" / "system.md"
        if p.is_file():
            return p.read_text(encoding="utf-8")
        return "You are dsc, a terminal coding agent."


def tool_names_for_mode(mode: str) -> list[str]:
    """Plan mode only gets read-only tools; other modes get everything."""
    all_tools = sorted(WRITE_TOOLS | READ_ONLY_TOOLS)
    if mode == "plan":
        return sorted(READ_ONLY_TOOLS)
    return all_tools


def execute_task(
    cfg: Config,
    task: str,
    ui: UI,
    perm: Permission,
    messages: Optional[list] = None,
    max_turns: int = 60,
) -> int:
    """Run one task to completion. Returns 0 on success, 1 on failure."""
    client = ChatClient(cfg)
    if messages is None:
        messages = [
            {"role": "system", "content": build_system_prompt(cfg)},
            {"role": "user", "content": task},
        ]
    ctx = {
        "ws": str(cfg.workspace),
        "workspace": str(cfg.workspace),
        "mode": cfg.mode,
        "cache": {},
        "backend": "none",  # refined in phase 6
        "allow_network": cfg.allow_network_for_bash,
        "tool_names": tool_names_for_mode(cfg.mode),
    }

    try:
        result = run(
            client,
            messages,
            ctx,
            max_turns=max_turns,
            on_text=ui.assistant_text,
            on_tool=ui.tool_line,
            check_permission=perm.check,
        )
    except ClientError as e:
        print(f"\n{ui._c(C_RED, f'API error: {e}')}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 1
    finally:
        client.close()

    if result.content:
        ui.final(result.content)
    if result.reason == "turn_cap":
        print(ui._c(C_YELLOW, "hit the 60-turn cap without a final answer"), file=sys.stderr)
        return 1
    return 0 if result.reason == "done" else 1


def repl(cfg: Config, ui: UI, perm: Permission) -> int:
    ui.info(f"dsc {__version__} — mode {cfg.mode} — workspace {cfg.workspace}")
    ui.info("type a task, or /quit to exit; trailing \\ continues the line")
    messages: list = []
    while True:
        try:
            line = _read_prompt()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not line.strip():
            continue
        if line.strip() in ("/quit", "/exit"):
            return 0
        if not messages:
            messages = [
                {"role": "system", "content": build_system_prompt(cfg)},
            ]
        messages.append({"role": "user", "content": line})
        try:
            execute_task(cfg, "", ui, perm, messages=messages)
        except Exception as e:
            print(f"error: {e}", file=sys.stderr)


def _read_prompt() -> str:
    parts = [input(PROMPT)]
    while parts[-1].endswith("\\"):
        parts[-1] = parts[-1][:-1]
        parts.append(input("... › "))
    return "".join(parts)


def run(cfg: Config, ns) -> int:
    if getattr(ns, "ping", False):
        try:
            reply = __import__("dsc.client", fromlist=["ping_config"]).ping_config(cfg)
        except ClientError as e:
            print(f"ping failed: {e}", file=sys.stderr)
            return 1
        print(f"✔ {reply}")
        return 0

    ui = UI(no_color=cfg.no_color)
    perm = Permission(mode=cfg.mode, auto_yes=cfg.auto_yes)
    if cfg.mode == "yolo":
        print(ui._c(C_RED, "⚠ YOLO mode: every tool call runs without approval ⚠"), file=sys.stderr)

    task = " ".join(ns.task).strip() if ns.task else ""
    if task:
        return execute_task(cfg, task, ui, perm, max_turns=cfg.max_turns)
    return repl(cfg, ui, perm)

"""Application shell: REPL and one-shot task execution.

Wires together config, client, loop, permissions, sandbox, sessions, and
context compaction. Renders streamed assistant text and tool lines.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

from dsc import __version__
from dsc.client import ChatClient, ClientError, StreamEvent
from dsc.config import Config
from dsc.context import (
    apply_summary,
    needs_compaction,
    repo_context,
    COMPACT_THRESHOLD,
)
from dsc.loop import ToolCallRecord, run, summarize_call
from dsc.permissions import Permission
from dsc.sandbox import detect_sandbox, set_notice_fn
from dsc.session import Session
from dsc.tools import READ_ONLY_TOOLS, WRITE_TOOLS

PROMPT = "dsc › "

# ANSI
C_RESET = "\x1b[0m"
C_DIM = "\x1b[2m"
C_BOLD = "\x1b[1m"
C_GREEN = "\x1b[32m"
C_RED = "\x1b[31m"
C_YELLOW = "\x1b[33m"


class UI:
    """Terminal rendering; honors no_color and NO_COLOR."""

    def __init__(self, no_color: bool = False, width: int = 100) -> None:
        self.no_color = no_color or bool(os.environ.get("NO_COLOR"))
        self.width = width
        self._mid_line = False  # streaming position tracking

    def _c(self, code: str, text: str) -> str:
        return text if self.no_color else f"{code}{text}{C_RESET}"

    def assistant_text(self, chunk: str) -> None:
        sys.stdout.write(chunk)
        sys.stdout.flush()
        self._mid_line = chunk.endswith("\n") is False and True

    def end_stream(self) -> None:
        if self._mid_line:
            print()

    def tool_line(self, rec: ToolCallRecord) -> None:
        self.end_stream()
        summary = summarize_call(rec.name, rec.args)
        line = f"⚙ {summary}"
        print(f"  {self._c(C_DIM, line)}")

    def final(self, text: str) -> None:
        print()
        print(self._c(C_GREEN, "✔ ") + text)
        print(self._c(C_DIM, "─" * min(self.width, 60)))

    def info(self, text: str) -> None:
        print(self._c(C_DIM, text))

    def warn(self, text: str) -> None:
        print(self._c(C_YELLOW, text), file=sys.stderr)


def build_system_prompt(cfg: Config) -> str:
    """System prompt + repo context (branch/status/log/README)."""
    from importlib import resources

    try:
        prompt = (resources.files("dsc") / "prompts" / "system.md").read_text(encoding="utf-8")
    except Exception:
        p = Path(__file__).parent / "prompts" / "system.md"
        prompt = p.read_text(encoding="utf-8") if p.is_file() else "You are dsc, a terminal coding agent."
    ctx = repo_context(cfg.workspace)
    return prompt + "\n\n## Current repository\n" + ctx


def tool_names_for_mode(mode: str) -> list[str]:
    """Plan mode only gets read-only tools; other modes get everything."""
    all_tools = sorted(WRITE_TOOLS | READ_ONLY_TOOLS)
    return sorted(READ_ONLY_TOOLS) if mode == "plan" else all_tools


def _make_compactor(client: ChatClient, cfg: Config, session: Session):
    """Build the compaction callback used by the loop."""
    from importlib import resources
    from dsc.context import split_for_compaction, estimate_messages_tokens

    try:
        cpath = resources.files("dsc") / "prompts" / "compact.md"
        compact_prompt = cpath.read_text(encoding="utf-8")
    except Exception:
        p = Path(__file__).parent / "prompts" / "compact.md"
        compact_prompt = p.read_text(encoding="utf-8") if p.is_file() else "Summarize the conversation densely."

    def compact(messages: list) -> list:
        if not needs_compaction(messages, cfg.context_tokens):
            return messages
        to_summarize, keep = split_for_compaction(messages)
        if not to_summarize:
            return messages
        ui_token_note = (
            f"[context compaction: {estimate_messages_tokens(messages)} est. tokens "
            f"exceed {int(cfg.context_tokens * COMPACT_THRESHOLD)} threshold]"
        )
        try:
            res = client.chat(
                [
                    {"role": "system", "content": compact_prompt},
                    *to_summarize,
                    {"role": "user", "content": "Produce the compaction summary now."},
                ],
                tools=None,
                stream=False,
            )
        except ClientError:
            return messages  # better to risk the cap than lose the thread
        session.log({
            "event": "compaction",
            "summarized": len(to_summarize),
            "kept": len(keep),
            "summary_len": len(res.content),
        })
        return apply_summary(messages, res.content)

    return compact


def execute_task(
    cfg: Config,
    task: str,
    ui: UI,
    perm: Permission,
    session: Session,
    messages: Optional[list] = None,
    max_turns: int = 60,
) -> int:
    """Run one task to completion. Returns 0 on success, 1 on failure."""
    backend = detect_sandbox(cfg.sandbox)
    client = ChatClient(cfg)
    state = {"todos": []}
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
        "backend": backend,
        "allow_network": cfg.allow_network_for_bash,
        "tool_names": tool_names_for_mode(cfg.mode),
        "cfg": cfg,
        "state": state,
    }
    session.log_meta(cfg.mode, cfg.model, str(cfg.workspace))
    session.log_chat(
        [{"role": m["role"], "len": len(str(m.get("content") or ""))} for m in messages],
        [{"function": {"name": n}} for n in ctx["tool_names"]],
    )

    def on_tool(rec: ToolCallRecord) -> None:
        ui.tool_line(rec)
        session.log_tool(rec.name, rec.args, rec.output, rec.duration_s)

    try:
        result = run(
            client,
            messages,
            ctx,
            max_turns=max_turns,
            on_text=ui.assistant_text,
            on_tool=on_tool,
            check_permission=perm.check,
            compact=_make_compactor(client, cfg, session),
        )
        session.save_messages(messages)
        session.log_final(result.content, result.reason)
    except ClientError as e:
        ui.end_stream()
        print(f"\n{ui._c(C_RED, f'API error: {e}')}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        ui.end_stream()
        print("\ninterrupted", file=sys.stderr)
        return 1
    finally:
        client.close()

    if result.content:
        ui.final(result.content)
    if result.reason == "turn_cap":
        ui.warn("hit the 60-turn cap without a final answer")
        return 1
    return 0 if result.reason == "done" else 1


def repl(cfg: Config, ui: UI, perm: Permission, session: Session) -> int:
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
            messages = [{"role": "system", "content": build_system_prompt(cfg)}]
        messages.append({"role": "user", "content": line})
        execute_task(cfg, "", ui, perm, session, messages=messages, max_turns=cfg.max_turns)


def _read_prompt() -> str:
    parts = [input(PROMPT)]
    while parts[-1].endswith("\\"):
        parts[-1] = parts[-1][:-1]
        parts.append(input("... › "))
    return "".join(parts)


def run(cfg: Config, ns) -> int:
    if getattr(ns, "ping", False):
        try:
            from dsc.client import ping_config

            reply = ping_config(cfg)
        except ClientError as e:
            print(f"ping failed: {e}", file=sys.stderr)
            return 1
        print(f"✔ {reply}")
        return 0

    ui = UI(no_color=cfg.no_color)
    perm = Permission(mode=cfg.mode, auto_yes=cfg.auto_yes)
    if cfg.mode == "yolo":
        print(ui._c(C_RED, "⚠ YOLO mode: every tool call runs without approval ⚠"), file=sys.stderr)
    set_notice_fn(lambda: ui.warn(
        "notice: running bash without container isolation "
        "(rlimits + cwd jail only)"
    ))

    # session selection
    session: Optional[Session] = None
    messages: Optional[list] = None
    if getattr(ns, "session", None):
        session = Session.by_id(ns.session)
        if session is None:
            ui.warn(f"session not found: {ns.session}")
            return 1
        messages = session.load_messages()
        ui.info(f"resuming session {ns.session} ({len(messages)} messages)")
    elif getattr(ns, "continue_session", False):
        session = Session.latest()
        if session is None:
            ui.warn("no previous session found")
            return 1
        messages = session.load_messages()
        ui.info(f"resuming latest session {session.sid} ({len(messages)} messages)")
    else:
        session = Session.new()
        ui.info(f"session {session.sid}")

    task = " ".join(ns.task).strip() if ns.task else ""
    if task:
        if messages is None:
            return execute_task(cfg, task, ui, perm, session, max_turns=cfg.max_turns)
        # resumed session + a new task: append it
        messages.append({"role": "user", "content": task})
        return execute_task(cfg, "", ui, perm, session, messages=messages, max_turns=cfg.max_turns)
    return repl(cfg, ui, perm, session)

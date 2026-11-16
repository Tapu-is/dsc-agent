"""Permission modes: plan | default | yolo, plus interactive approval.

check() returns "allow" | "deny" | "abort" for a requested tool call.
In default mode, risky actions prompt the user (y/n/a) on stderr; 'a'
allows everything for the rest of the session.
"""
from __future__ import annotations

import sys
from typing import Optional

from dsc.tools import WRITE_TOOLS

VALID_MODES = ("plan", "default", "yolo")


class Permission:
    """Tracks mode + session-wide approvals. No global mutable state."""

    def __init__(self, mode: str = "default", auto_yes: bool = False) -> None:
        if mode not in VALID_MODES:
            mode = "default"
        self.mode = mode
        self.auto_yes = auto_yes
        self.session_allowed: set[str] = set()
        self._input_fn = _ask_user  # overridable for tests

    # ------------------------------------------------------------- checks
    def check(self, tool: str, args: dict) -> str:
        """Return allow|deny|abort for this tool call."""
        if self.mode == "yolo":
            return "allow"
        if self.mode == "plan":
            if tool in WRITE_TOOLS:
                return "deny"
            return "allow"
        # default mode
        if tool not in WRITE_TOOLS:
            return "allow"
        if tool in self.session_allowed or self.auto_yes:
            return "allow"
        return self._prompt(tool, args)

    def _prompt(self, tool: str, args: dict) -> str:
        verdict = self._input_fn(tool, args)
        if verdict == "a":
            self.session_allowed.add(tool)
            return "allow"
        return verdict  # "y"->allow, "n"->deny

    # ------------------------------------------------------------ prompts
    def banner(self, err, color) -> None:
        pass


def describe(tool: str, args: dict) -> str:
    """Human-readable description of the risky action, shown when prompting."""
    if tool == "bash":
        return f"run shell command: {args.get('command', '')!r}"
    if tool in ("write_file", "edit_file"):
        return f"{tool}: {args.get('path', '')!r}"
    if tool == "web_fetch":
        return f"fetch URL: {args.get('url', '')!r}"
    if tool == "web_search":
        return f"web search: {args.get('query', '')!r}"
    return f"{tool}: {args}"


def _ask_user(tool: str, args: dict) -> str:
    """Interactive y/n/a prompt on stderr. Never raises."""
    try:
        sys.stderr.write(f"\n⚠ {describe(tool, args)}\napprove? [y/n/a(yes to all)] ")
        sys.stderr.flush()
        ans = input().strip().lower()
    except (EOFError, KeyboardInterrupt):
        return "n"
    if ans in ("y", "yes", ""):
        return "y"
    if ans in ("a", "always"):
        return "a"
    return "n"

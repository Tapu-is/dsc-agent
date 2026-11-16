"""Application shell: dispatch between REPL and one-shot mode.

Currently a stub for Phase 1; fleshed out as the loop and UX land.
"""
from __future__ import annotations

import sys

from dsc.config import Config


def run(cfg: Config, ns) -> int:
    if getattr(ns, "ping", False):
        print("ping: not yet implemented (Phase 2)")
        return 2
    task = " ".join(ns.task).strip() if ns.task else ""
    if task:
        print(f"one-shot mode not yet implemented (got task: {task!r})")
        return 2
    print(f"dsc {__import__('dsc').__version__} — REPL not yet implemented (Phase 4)")
    return 2

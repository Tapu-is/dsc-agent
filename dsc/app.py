"""Application shell: dispatch between REPL, one-shot, and ping modes."""
from __future__ import annotations

import sys

from dsc.client import ClientError, ping_config
from dsc.config import Config


def run(cfg: Config, ns) -> int:
    if getattr(ns, "ping", False):
        try:
            reply = ping_config(cfg)
        except ClientError as e:
            print(f"ping failed: {e}", file=sys.stderr)
            return 1
        print(f"✔ {reply}")
        return 0

    task = " ".join(ns.task).strip() if ns.task else ""
    if task:
        print("one-shot mode not yet implemented (Phase 4)")
        return 2
    print("dsc REPL not yet implemented (Phase 4)")
    return 2

"""Entry point for dsc: parse argv, load config, start REPL or one-shot."""
from __future__ import annotations

import argparse
import sys
from typing import Optional, Sequence

from dsc import __version__
from dsc.config import Config, MODES, load_config


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="dsc",
        description="dsc — a terminal coding agent powered by DeepSeek",
    )
    p.add_argument("task", nargs="*", help="one-shot task (omit for REPL)")
    p.add_argument("--mode", choices=MODES, help="permission mode")
    p.add_argument("--model", help="model name override")
    p.add_argument("--yes", action="store_true", help="auto-approve prompts")
    p.add_argument("--no-color", action="store_true", help="disable ANSI colors")
    p.add_argument("--continue", dest="continue_session", action="store_true",
                   help="resume the latest session")
    p.add_argument("--session", help="resume a session by id")
    p.add_argument("--ping", action="store_true", help="make one real API call and exit")
    p.add_argument("--version", action="store_true", help="print version and exit")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    ns = parser.parse_args(argv)

    if ns.version:
        print(f"dsc {__version__}")
        return 0

    overrides = {
        "mode": ns.mode,
        "model": ns.model,
        "auto_yes": True if ns.yes else None,
        "no_color": True if ns.no_color or sys.platform.startswith("win") else None,
    }
    cfg: Config = load_config(overrides)
    if os_environ_no_color():
        cfg.no_color = True

    problems = cfg.validate()
    if problems:
        for pr in problems:
            print(f"config error: {pr}", file=sys.stderr)
        return 2

    from dsc.app import run

    return run(cfg, ns)


def os_environ_no_color() -> bool:
    import os

    return bool(os.environ.get("NO_COLOR"))


if __name__ == "__main__":
    sys.exit(main())

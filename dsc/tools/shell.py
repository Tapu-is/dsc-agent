"""bash tool: runs a shell command through the sandbox jail."""
from __future__ import annotations

from pathlib import Path


def bash(ws: Path, backend: str = "none", **args) -> str:
    """bash(command, timeout_s?, cwd?) — always via sandbox.run_command."""
    from dsc.sandbox import run_command

    cmd = str(args.get("command", "")).strip()
    if not cmd:
        return "error: bash requires a command"
    try:
        timeout = min(float(args.get("timeout_s") or 120), 600)
    except (TypeError, ValueError):
        return "error: timeout_s must be a number"
    cwd = args.get("cwd")
    if cwd:
        cwd = str(Path(cwd))
    if not isinstance(cmd, str) or not cmd:
        return "error: bash requires a command"

    code, out, dur = run_command(
        cmd, str(ws), backend=backend, timeout=timeout, cwd=cwd
    )
    tag = f"(exit {code}, {dur:.1f}s)"
    body = out if out.strip() else "(no output)"
    return f"{body}\n[{tag}]"

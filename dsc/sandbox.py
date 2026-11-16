"""Command execution jail: docker | bwrap | rlimits fallback.

run_command() is the single entry point used by the bash tool. Platform
capability is detected once. The fallback (Termux / plain Linux) runs the
command with cwd inside the workspace, a scrubbed env, rlimits on CPU and
file size, a wall-clock timeout, and an output cap.
"""
from __future__ import annotations

import os
import resource
import shutil
import subprocess
import time
from dataclasses import dataclass
from typing import Optional, Tuple

OUTPUT_CAP = 100 * 1024 * 1024  # 100MB

_NOTICE_SHOWN = False
_degraded_notice_fn: Optional[callable] = None


def detect_sandbox(preferred: str = "auto") -> str:
    """Return the sandbox backend: 'docker' | 'bwrap' | 'none'."""
    if preferred == "docker" and shutil.which("docker"):
        return "docker"
    if preferred == "bwrap" and shutil.which("bwrap"):
        return "bwrap"
    if preferred == "auto":
        if os.environ.get("DSC_USE_DOCKER") in ("1", "true", "yes") and shutil.which("docker"):
            return "docker"
        if shutil.which("bwrap"):
            return "bwrap"
        return "none"
    return "none"


def set_notice_fn(fn) -> None:
    """Register a callable used to print the one-time 'isolation limited' notice."""
    global _degraded_notice_fn
    _degraded_notice_fn = fn


def _maybe_notice() -> None:
    global _NOTICE_SHOWN
    if not _NOTICE_SHOWN and _degraded_notice_fn is not None:
        _degraded_notice_fn()
        _NOTICE_SHOWN = True


def scrub_env(env: dict) -> dict:
    """Remove API keys and tokens from an environment copy."""
    out = {}
    for k, v in env.items():
        ku = k.upper()
        if ku in ("DEEPSEEK_API_KEY", "DSC_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
            continue
        if ku.endswith("_TOKEN") or ku.endswith("_KEY") or ku.endswith("_SECRET"):
            continue
        out[k] = v
    return out


@dataclass
class CommandResult:
    exit_code: int
    stdout: str  # combined output, capped head+tail
    duration_s: float
    timed_out: bool = False


def run_command(
    cmd: str,
    workspace: str,
    backend: str = "none",
    timeout: float = 120.0,
    cwd: Optional[str] = None,
    allow_network: bool = False,
    env_extra: Optional[dict] = None,
) -> Tuple[int, str, float]:
    """Run cmd inside the jail. Returns (exit_code, output, duration).

    Never raises: all failures are mapped to an exit code + message.
    """
    effective_cwd = os.path.abspath(cwd) if cwd else workspace
    if not os.path.isdir(effective_cwd):
        return 126, f"error: cwd does not exist: {effective_cwd}", 0.0
    if os.path.realpath(effective_cwd) != workspace and not os.path.realpath(
        effective_cwd
    ).startswith(os.path.realpath(workspace) + os.sep):
        return 126, f"error: cwd escapes workspace: {effective_cwd}", 0.0

    env = scrub_env(dict(os.environ))
    env.setdefault("TERM", "dumb")
    env.setdefault("LC_ALL", "C.UTF-8")
    if env_extra:
        env.update(env_extra)

    start = time.monotonic()
    if backend == "docker" and shutil.which("docker"):
        argv = [
            "docker", "run", "--rm", "--user", "nobody",
            "-v", f"{workspace}:/workspace", "-w", "/workspace",
            "network=none" if not allow_network else "network=bridge",
        ]
        # docker CLI expects flags, not a bare "network=..." token
        argv = ["docker", "run", "--rm", "--user", "nobody",
                "-v", f"{workspace}:/workspace", "-w", "/workspace"]
        if not allow_network:
            argv.append("--network=none")
        argv.extend(["sh", "-c", cmd])
        return _run(argv, effective_cwd, env, timeout, start, shell=False)
    if backend == "bwrap" and shutil.which("bwrap"):
        argv = [
            "bwrap",
            "--ro-bind", "/", "/",
            "--bind", effective_cwd, effective_cwd,
            "--dev", "/dev",
            "--proc", "/proc",
        ]
        if not allow_network:
            argv.append("--unshare-net")
        argv.extend(["sh", "-c", cmd])
        return _run(argv, effective_cwd, env, timeout, start, shell=False)
    # plain fallback (Termux / Linux no-root)
    _maybe_notice()
    return _run_shell(cmd, effective_cwd, env, timeout, start)


def _run_shell(cmd, cwd, env, timeout, start):
    preexec = _make_preexec()
    try:
        proc = subprocess.Popen(
            cmd,
            shell=True,
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            preexec_fn=preexec,
        )
    except OSError as e:
        return 127, f"error: spawn failed: {e}", 0.0
    return _collect(proc, timeout, start)


def _run(argv, cwd, env, timeout, start, shell):
    try:
        proc = subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
    except OSError as e:
        return 127, f"error: spawn failed: {e}", 0.0
    return _collect(proc, timeout, start)


def _make_preexec():
    def pre():
        try:
            os.setpgrp()  # own process group so timeouts kill only the child tree
            resource.setrlimit(resource.RLIMIT_CPU, (300, 300))
            resource.setrlimit(resource.RLIMIT_FSIZE, (OUTPUT_CAP, OUTPUT_CAP))
            resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))
        except (ValueError, OSError):
            pass

    return pre


def _collect(proc, timeout, start) -> Tuple[int, str, float]:
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        try:
            out, _ = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            out = b""
        dur = time.monotonic() - start
        return 124, _cap(out.decode(errors="replace")) + f"\n[timeout after {timeout:.0f}s]", dur
    dur = time.monotonic() - start
    return proc.returncode or 0, _cap(out.decode(errors="replace")), dur


def _kill_tree(proc) -> None:
    import signal

    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except OSError:
            pass


def _cap(text: str, limit: int = 30_000) -> str:
    """Keep head AND tail when output is huge."""
    if len(text) <= limit:
        return text
    half = limit // 2
    cut = len(text) - limit
    return text[:half] + f"\n...[{cut} chars omitted]...\n" + text[-half:]

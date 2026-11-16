"""Context management: token estimation, file cache, repo context, compaction.

Token estimation is len(text)//4 (documented approximation; swap in a real
tokenizer later via config). Compaction replaces all but the last 4
messages with a dense summary produced by the model.
"""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Optional

from dsc.config import Config

COMPACT_THRESHOLD = 0.70          # fire compaction above 70% of context
KEEP_LAST_MESSAGES = 4            # messages preserved verbatim after compaction
REPO_CONTEXT_MAX = 4_000          # chars of repo context injected at start


def estimate_tokens(text: str) -> int:
    """~chars/4. Documented approximation; good enough for thresholds."""
    return len(text) // 4


def estimate_messages_tokens(messages: list) -> int:
    total = 0
    for m in messages:
        total += estimate_tokens(str(m.get("content") or ""))
        for tc in m.get("tool_calls") or []:
            total += estimate_tokens(tc.get("function", {}).get("arguments", ""))
    return total


# ------------------------------------------------------------- file cache
def file_cache_note(cache: dict, path: str) -> Optional[str]:
    """If path is unchanged since last read, return the short note."""
    entry = cache.get(path)
    if not entry:
        return None
    p = Path(path)
    if not p.is_file():
        return None
    try:
        content = p.read_bytes()
    except OSError:
        return None
    h = hashlib.sha256(content).hexdigest()[:12]
    if h == entry["hash"]:
        return f"unchanged since last read (hash {h}…)"
    return None


def track_cache(cache: dict, path: str) -> None:
    p = Path(path)
    try:
        content = p.read_bytes()
    except OSError:
        return
    cache[path] = {
        "mtime": p.stat().st_mtime,
        "size": len(content),
        "hash": hashlib.sha256(content).hexdigest()[:12],
    }


# ---------------------------------------------------------- repo context
def repo_context(ws: Path, max_chars: int = REPO_CONTEXT_MAX) -> str:
    """Snapshot of repo state: branch, status, recent log, doc files."""
    parts: list[str] = []

    def _git(*args: str) -> Optional[str]:
        try:
            r = subprocess.run(
                ["git", *args], cwd=ws, capture_output=True, text=True, timeout=5
            )
            return r.stdout.strip() if r.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired):
            return None

    branch = _git("branch", "--show-current")
    if branch is not None:
        parts.append(f"branch: {branch}")
        status = _git("status", "--short")
        if status:
            parts.append(f"git status --short:\n{status}")
        log = _git("log", "--oneline", "-5")
        if log:
            parts.append(f"recent commits:\n{log}")
    else:
        parts.append("(not a git repository)")

    for name in ("AGENTS.md", "CLAUDE.md", "README.md"):
        f = ws / name
        if f.is_file():
            try:
                text = f.read_text(encoding="utf-8", errors="replace")[:1_500]
                parts.append(f"--- {name} ---\n{text}")
            except OSError:
                pass

    out = "\n\n".join(parts)
    if len(out) > max_chars:
        out = out[:max_chars] + "\n...[truncated]"
    return out


# ------------------------------------------------------------- compaction
def needs_compaction(messages: list, context_tokens: int) -> bool:
    return estimate_messages_tokens(messages) > int(context_tokens * COMPACT_THRESHOLD)


def split_for_compaction(messages: list) -> tuple[list, list]:
    """Return (to_summarize, keep_verbatim); keeps last KEEP_LAST_MESSAGES."""
    if len(messages) <= KEEP_LAST_MESSAGES:
        return [], list(messages)
    return list(messages[:-KEEP_LAST_MESSAGES]), list(messages[-KEEP_LAST_MESSAGES:])


def apply_summary(messages: list, summary: str) -> list:
    """Replace all but the last 4 messages with the summary as a system msg."""
    _, keep = split_for_compaction(messages)
    if not summary:
        return messages
    sysmsg = {
        "role": "system",
        "content": (
            "Summary of the conversation so far (earlier messages were compacted):\n\n"
            + summary
        ),
    }
    return [sysmsg] + keep

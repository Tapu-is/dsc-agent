"""File tools: read, write, edit, list, glob, grep. All return strings.

Every handler validates its arguments and returns an error string instead
of raising. Output is truncated to TOOL_OUTPUT_MAX chars with a truncation
notice. Paths are resolved relative to the workspace root.
"""
from __future__ import annotations

import fnmatch
import hashlib
import os
import re
from pathlib import Path
from typing import Optional

TOOL_OUTPUT_MAX = 20_000
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".tox", ".mypy_cache"}
GLOB_CAP = 500
GREP_CAP = 200
READ_CAP_LINES = 2000
BINARY_EXT = {".png", ".jpg", ".jpeg", ".gif", ".pdf", ".zip", ".tar", ".gz", ".pyc",
              ".so", ".bin", ".exe", ".o", ".class", ".woff", ".woff2"}


# --------------------------------------------------------------- helpers
def _resolve(ws: Path, path: str) -> Path:
    """Resolve path against workspace; returns an absolute path."""
    p = Path(path)
    if not p.is_absolute():
        p = ws / p
    return p.expanduser().resolve()


def _err(msg: str) -> str:
    return f"error: {msg}"


def _truncate(text: str, cap: int = TOOL_OUTPUT_MAX) -> str:
    if len(text) <= cap:
        return text
    return text[:cap] + f"\n...[truncated {len(text) - cap} chars]"


def _looks_binary(path: Path) -> bool:
    if path.suffix.lower() in BINARY_EXT:
        return True
    try:
        with open(path, "rb") as f:
            return b"\x00" in f.read(1024)
    except OSError:
        return False


def in_workspace(ws: Path, path: Path) -> bool:
    """True if path is inside workspace (workspace==path also allowed)."""
    try:
        path.relative_to(ws)
        return True
    except ValueError:
        return False


# -------------------------------------------------------------- handlers
def read_file(
    ws: Path,
    cache: Optional[dict] = None,
    **args,
) -> str:
    """read_file(path, offset?, limit?) — numbered lines, capped."""
    path = str(args.get("path", ""))
    offset = int(args.get("offset") or 1)
    limit = int(args.get("limit") or READ_CAP_LINES)
    if not path:
        return _err("read_file requires a path")
    if offset < 1:
        return _err("offset must be >= 1")
    limit = min(limit, READ_CAP_LINES)

    p = _resolve(ws, path)
    if not p.is_file():
        if p.is_dir():
            return _err(f"{path} is a directory (use list_dir)")
        return _err(f"file not found: {path}")
    if _looks_binary(p):
        return _err(f"{path} looks binary; refusing to read")

    try:
        stat = p.stat()
        content = p.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return _err(f"cannot read {path}: {e}")

    if cache is not None:
        h = hashlib.sha256(content.encode()).hexdigest()[:12]
        prev = cache.get(str(p))
        if prev and prev.get("hash") == h and prev.get("mtime") == stat.st_mtime:
            return f"unchanged since last read (hash {h}…) — {len(content.splitlines())} lines, {stat.st_size} bytes"
        cache[str(p)] = {"mtime": stat.st_mtime, "size": stat.st_size, "hash": h}

    lines = content.splitlines()
    total = len(lines)
    start = min(offset, max(total, 1)) - 1
    window = lines[start : start + limit]
    out_lines = [f"{i + start + 1:6d}\t{t}" for i, t in enumerate(window)]
    header = f"# {path} ({total} lines)"
    if start > 0 or start + len(window) < total:
        header += f" — showing {start + 1}-{start + len(window)}"
    out = "\n".join([header] + out_lines)
    return _truncate(out)


def write_file(ws: Path, mode: str = "default", **args) -> str:
    """write_file(path, content) — creates parents; escapes need approval."""
    path = str(args.get("path", ""))
    content = args.get("content", "")
    if not path:
        return _err("write_file requires a path")
    if not isinstance(content, str):
        return _err("content must be a string")

    p = _resolve(ws, path)
    if not in_workspace(ws, p):
        return _err(
            f"path escapes workspace: {path} (resolve {p}); "
            "write outside workspace requires approval/yolo"
        )
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        existed = p.exists()
        p.write_text(content, encoding="utf-8")
        return f"wrote {len(content)} chars to {path}{'' if existed else ' (new file)'}"
    except OSError as e:
        return _err(f"cannot write {path}: {e}")


def edit_file(ws: Path, **args) -> str:
    """edit_file(path, old_string, new_string, replace_all?) — exact match."""
    path = str(args.get("path", ""))
    old = args.get("old_string")
    new = args.get("new_string")
    replace_all = bool(args.get("replace_all", False))
    if not path:
        return _err("edit_file requires a path")
    if old is None or new is None:
        return _err("edit_file requires old_string and new_string")
    if old == new:
        return _err("old_string and new_string are identical")

    p = _resolve(ws, path)
    if not p.is_file():
        return _err(f"file not found: {path}")
    try:
        content = p.read_text(encoding="utf-8", errors="strict")
    except (OSError, UnicodeDecodeError) as e:
        return _err(f"cannot read {path}: {e}")

    count = content.count(old)
    if count == 0:
        return _err(
            f"old_string not found in {path}. "
            + _closest_matches(content, old)
        )
    if count > 1 and not replace_all:
        line_nums = sorted(
            {content[:i].count("\n") + 1 for i in _all_occurrences(content, old)}
        )
        return _err(
            f"old_string appears {count} times in {path} (lines {line_nums[:5]}); "
            "add more surrounding context or set replace_all=true"
        )
    if count > 1:
        content = content.replace(old, new)
    else:
        content = content.replace(old, new, 1)
    try:
        p.write_text(content, encoding="utf-8")
    except OSError as e:
        return _err(f"cannot write {path}: {e}")
    note = f" (replaced {count} occurrences)" if count > 1 else ""
    return f"edited {path}{note}"


def _all_occurrences(content: str, needle: str):
    start = 0
    while True:
        i = content.find(needle, start)
        if i < 0:
            return
        yield i
        start = i + max(len(needle), 1)


def _closest_matches(content: str, old: str, k: int = 3) -> str:
    """Summarize the k closest candidate lines for a failed exact match."""
    import difflib

    needle = old.splitlines()[0].strip() if old.strip() else ""
    lines = content.splitlines()
    scored = sorted(
        ((difflib.SequenceMatcher(None, needle, ln.strip()).ratio(), i, ln)
         for i, ln in enumerate(lines)),
        key=lambda t: -t[0],
    )[:k]
    parts = [f"closest match at line {i + 1}: {ln.strip()[:120]!r}" for r, i, ln in scored]
    return "Did you mean:\n" + "\n".join(parts) if parts else "file appears empty."


def list_dir(ws: Path, **args) -> str:
    """list_dir(path?) — depth-1 listing, dirs marked with trailing /."""
    path = str(args.get("path") or ".")
    p = _resolve(ws, path)
    if not p.is_dir():
        return _err(f"not a directory: {path}")
    try:
        entries = sorted(p.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
    except OSError as e:
        return _err(f"cannot list {path}: {e}")
    lines = []
    for e in entries[:1000]:
        name = e.name + ("/" if e.is_dir() else "")
        lines.append(name)
    if len(entries) > 1000:
        lines.append(f"...[{len(entries) - 1000} more]")
    return "\n".join(lines) or "(empty)"


def glob(ws: Path, **args) -> str:
    """glob(pattern, path?) — cap 500, sorted by mtime desc."""
    pattern = str(args.get("pattern", ""))
    base = str(args.get("path") or ".")
    if not pattern:
        return _err("glob requires a pattern")
    root = _resolve(ws, base)
    if not root.is_dir():
        return _err(f"not a directory: {base}")
    hits: list[Path] = []
    for p in root.glob(pattern):
        if p.is_file():
            hits.append(p)
        if len(hits) >= GLOB_CAP * 2:
            break
    hits = sorted(hits, key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
    hits = [h for h in hits if not any(part in SKIP_DIRS for part in h.parts)][:GLOB_CAP]
    if not hits:
        return "(no matches)"
    rels = [str(h.relative_to(root)) for h in hits]
    out = "\n".join(rels)
    if len(hits) >= GLOB_CAP:
        out += f"\n...[capped at {GLOB_CAP} results]"
    return out


def grep(ws: Path, **args) -> str:
    """grep(pattern, path?, glob?, case_insensitive?) — regex over files."""
    pattern = str(args.get("pattern", ""))
    if not pattern:
        return _err("grep requires a pattern")
    try:
        rx = re.compile(pattern, re.IGNORECASE if args.get("case_insensitive") else 0)
    except re.error as e:
        return _err(f"bad regex: {e}")
    file_glob = str(args.get("glob") or "")
    base = str(args.get("path") or ".")
    root = _resolve(ws, base)
    if not root.is_dir():
        return _err(f"not a directory: {base}")

    matches: list[str] = []
    truncated = False
    for f in _iter_files(root):
        if file_glob and not fnmatch.fnmatch(f.name, file_glob):
            continue
        if _looks_binary(f):
            continue
        try:
            with open(f, encoding="utf-8", errors="replace") as fh:
                for lineno, line in enumerate(fh, 1):
                    if rx.search(line):
                        rel = f.relative_to(root)
                        matches.append(f"{rel}:{lineno}: {line.rstrip()[:300]}")
                        if len(matches) >= GREP_CAP:
                            truncated = True
                            break
        except OSError:
            continue
        if truncated:
            break
    if not matches:
        return "(no matches)"
    out = "\n".join(matches)
    if truncated:
        out += f"\n...[capped at {GREP_CAP} matches]"
    return _truncate(out)


def _iter_files(root: Path):
    """Walk root, skipping vendor dirs, files first for determinism."""
    stack = [root]
    while stack:
        d = stack.pop()
        try:
            entries = sorted(d.iterdir(), key=lambda e: e.name)
        except OSError:
            continue
        for e in entries:
            if e.is_dir():
                if e.name not in SKIP_DIRS and not e.is_symlink():
                    stack.append(e)
                continue
            yield e


def search_agent(ws: Path, cfg=None, **args) -> str:
    """search_agent(question) — nested read-only exploration loop.

    Runs a bounded sub-loop with only read_file/grep/glob and returns a
    short answer plus the files consulted. Keeps the main context clean.
    """
    if cfg is None:
        return "error: search_agent unavailable (no client config)"
    question = str(args.get("question", "")).strip()
    if not question:
        return "error: search_agent requires a question"

    from dsc.client import ChatClient, ClientError
    from dsc.loop import run as loop_run

    client = ChatClient(cfg)
    try:
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a read-only code exploration subagent. Answer the "
                    "question using read_file/grep/glob only. Reply with a "
                    "concise answer (max 200 words) and a list of the files "
                    "you consulted. Do not modify anything."
                ),
            },
            {"role": "user", "content": f"Workspace: {ws}\nQuestion: {question}"},
        ]
        sub_ctx = {
            "ws": str(ws),
            "workspace": str(ws),
            "mode": "plan",
            "cache": {},
            "tool_names": ["read_file", "grep", "glob", "list_dir"],
        }
        result = loop_run(client, messages, sub_ctx, max_turns=8)
        if not result.content:
            return f"search_agent found nothing (reason: {result.reason})"
        return result.content
    except ClientError as e:
        return f"error: search_agent API call failed: {e}"
    except Exception as e:
        return f"error: search_agent crashed: {type(e).__name__}: {e}"
    finally:
        client.close()

"""TOOL_REGISTRY: name -> schema + handler metadata.

Handlers are pure-ish callables taking (workspace, **kwargs). The loop
resolves extra context (mode, cache, sandbox backend) via the ctx dict
passed through kwargs.
"""
from __future__ import annotations

from typing import Callable

from dsc.tools import files, shell

TOOL_OUTPUT_MAX = files.TOOL_OUTPUT_MAX


def _schema(desc: str, props: dict, required: list) -> dict:
    return {
        "type": "function",
        "function": {
            "name": None,  # filled by register
            "description": desc,
            "parameters": {
                "type": "object",
                "properties": props,
                "required": required,
            },
        },
    }


FILE_PATH = {"type": "string", "description": "path relative to workspace root"}

SCHEMAS: dict[str, dict] = {
    "read_file": _schema(
        "Read a text file with line numbers. Use offset/limit for large files. "
        "Returns 'unchanged' if the file was already read.",
        {
            "path": FILE_PATH,
            "offset": {"type": "integer", "description": "1-based start line"},
            "limit": {"type": "integer", "description": "max lines (default 2000, cap 2000)"},
        },
        ["path"],
    ),
    "write_file": _schema(
        "Create or overwrite a file. Prefer edit_file for existing files.",
        {"path": FILE_PATH, "content": {"type": "string"}},
        ["path", "content"],
    ),
    "edit_file": _schema(
        "Exact-match replace in a file. old_string must match exactly and "
        "uniquely (or set replace_all). Read the file first.",
        {
            "path": FILE_PATH,
            "old_string": {"type": "string"},
            "new_string": {"type": "string"},
            "replace_all": {"type": "boolean", "default": False},
        },
        ["path", "old_string", "new_string"],
    ),
    "list_dir": _schema(
        "List one directory level; directories get a trailing slash.",
        {"path": FILE_PATH},
        [],
    ),
    "glob": _schema(
        "Find files by glob pattern (e.g. '**/*.py'), sorted by mtime desc.",
        {"pattern": {"type": "string"}, "path": FILE_PATH},
        ["pattern"],
    ),
    "grep": _schema(
        "Regex search over files. Returns file:line: text. Skips .git, "
        "node_modules, __pycache__, .venv.",
        {
            "pattern": {"type": "string"},
            "path": FILE_PATH,
            "glob": {"type": "string", "description": "filename filter, e.g. '*.py'"},
            "case_insensitive": {"type": "boolean", "default": False},
        },
        ["pattern"],
    ),
    "bash": _schema(
        "Run a shell command in the workspace. Output is capped; use cwd and "
        "timeout_s as needed. Runs through the sandbox jail.",
        {
            "command": {"type": "string"},
            "timeout_s": {"type": "integer", "default": 120},
            "cwd": {"type": "string"},
        },
        ["command"],
    ),
}

HANDLERS: dict[str, Callable[..., str]] = {
    "read_file": files.read_file,
    "write_file": files.write_file,
    "edit_file": files.edit_file,
    "list_dir": files.list_dir,
    "glob": files.glob,
    "grep": files.grep,
    "bash": shell.bash,
}

# tools that mutate state (used by permissions + plan mode)
WRITE_TOOLS = {"write_file", "edit_file", "bash", "web_fetch", "web_search", "todo_write"}
READ_ONLY_TOOLS = {"read_file", "list_dir", "glob", "grep"}


def tool_schemas(names: list[str]) -> list[dict]:
    """Build the OpenAI tools array for a set of tool names."""
    out = []
    for name in names:
        schema = SCHEMAS.get(name)
        if not schema:
            continue
        d = dict(schema["function"])
        d["name"] = name
        out.append({"type": "function", "function": d})
    return out


def dispatch(name: str, ctx: dict, args: dict) -> str:
    """Run a tool by name. Returns an error string for unknown tools."""
    handler = HANDLERS.get(name)
    if not handler:
        return f"error: unknown tool: {name}"
    merged = dict(args or {})
    merged.update(
        {
            k: ctx[k]
            for k in ("ws", "mode", "cache", "backend", "allow_network", "cfg")
            if k in ctx
        }
    )
    merged.setdefault("ws", ctx.get("workspace", "."))
    return handler(**merged)

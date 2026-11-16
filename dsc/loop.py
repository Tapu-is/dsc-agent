"""The agent loop: model calls, tool dispatch, turn cap, compaction hook.

loop.run() drives a conversation until the model produces a final answer
or the turn cap is hit. Tool calls are dispatched through the registry;
permission checks and compaction are delegated to their modules so the
loop stays generic.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Callable, Optional, Protocol

from dsc.tools import dispatch, tool_schemas, WRITE_TOOLS


class Client(Protocol):
    """Structural type so tests can inject a FakeClient."""

    def chat(self, messages, tools=None, stream=True, on_event=None): ...


@dataclass
class LoopResult:
    content: str = ""              # final assistant text
    turns: int = 0                 # model calls made
    tool_calls: int = 0
    aborted: bool = False          # turn cap hit or hard stop
    reason: str = ""               # "done" | "turn_cap" | "interrupted" | ...


@dataclass
class ToolCallRecord:
    name: str
    args: dict
    output: str
    duration_s: float = 0.0


def summarize_call(name: str, args: dict) -> str:
    """Compact one-line summary for rendering: `→ edit_file src/app.py`."""
    if name == "bash":
        a = str(args.get("command", ""))[:80]
        return f"bash: {a}"
    for k in ("path", "pattern", "url", "query", "command"):
        if k in args:
            return f"{name} {args[k]}"[:100]
    return name


def run(
    client: Client,
    messages: list,
    ctx: dict,
    max_turns: int = 60,
    on_text: Optional[Callable[[str], None]] = None,
    on_tool: Optional[Callable[[ToolCallRecord], None]] = None,
    check_permission: Optional[Callable[[str, dict], str]] = None,
    compact: Optional[Callable[[list], list]] = None,
) -> LoopResult:
    """Drive the agent loop. Mutates `messages` in place and returns status.

    check_permission(name, args) -> "allow" | "deny" | "abort".
    compact(messages) -> compacted message list (may return same list).
    """
    tools = tool_schemas(ctx.get("tool_names", []))
    result = LoopResult()
    interrupted = False

    while result.turns < max_turns:
        if compact is not None:
            compacted = compact(messages)
            messages[:] = compacted
        result.turns += 1

        try:
            res = client.chat(messages, tools=tools, stream=True, on_event=on_event_pipe(on_text))
        except KeyboardInterrupt:
            interrupted = True
            break

        if res.tool_calls:
            messages.append(_assistant_msg(res))
            for tc in res.tool_calls:
                record = _execute(tc, ctx, check_permission, on_tool)
                result.tool_calls += 1
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": record.output,
                    }
                )
                if record.output.startswith("\x00abort"):
                    result.aborted = True
                    result.reason = "aborted"
                    result.content = record.output[6:]
                    return result
            continue

        result.content = res.content
        result.reason = "done"
        messages.append({"role": "assistant", "content": res.content})
        return result

    result.reason = "turn_cap" if not interrupted else "interrupted"
    result.aborted = True
    return result


def on_event_pipe(on_text):
    if on_text is None:
        return None
    from dsc.client import StreamEvent

    def pipe(ev):
        if ev.kind == "text" and ev.text:
            on_text(ev.text)

    return pipe


def _assistant_msg(res) -> dict:
    """Build the assistant message (content + tool_calls) for the transcript."""
    msg: dict = {"role": "assistant"}
    if res.content:
        msg["content"] = res.content
    msg["tool_calls"] = [
        {
            "id": tc.id,
            "type": "function",
            "function": {"name": tc.name, "arguments": tc.arguments},
        }
        for tc in res.tool_calls
    ]
    return msg


def _execute(
    tc, ctx: dict,
    check_permission: Optional[Callable],
    on_tool: Optional[Callable],
) -> ToolCallRecord:
    """Execute one tool call; never raises. Aborts use a sentinel prefix."""
    name = tc.name
    args = tc.parse_args()
    start = time.monotonic()

    if check_permission is not None:
        verdict = check_permission(name, args)
        if verdict == "abort":
            return ToolCallRecord(
                name, args, "\x00abort" + (f"stopped: {args.get('reason', 'denied')}" or "stopped"),
                time.monotonic() - start,
            )
        if verdict == "deny":
            out = (
                f"error: permission denied for {name} in current mode. "
                "Ask the user to switch modes (--mode yolo/default) if needed."
            )
            rec = ToolCallRecord(name, args, out, time.monotonic() - start)
            if on_tool:
                on_tool(rec)
            return rec

    try:
        out = dispatch(name, ctx, args)
    except Exception as e:  # tool boundary: exceptions become values
        out = f"error: tool {name} crashed: {type(e).__name__}: {e}"
    rec = ToolCallRecord(name, args, out, time.monotonic() - start)
    if on_tool:
        on_tool(rec)
    return rec

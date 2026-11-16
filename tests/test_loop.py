"""Tests for the agent loop using a scripted FakeClient (no network)."""
from __future__ import annotations

import json

import pytest

from dsc.client import ChatResult, ToolCall
from dsc.loop import run
from dsc.tools import HANDLERS


class FakeClient:
    """Returns a scripted sequence of ChatResults."""

    def __init__(self, results):
        self.results = list(results)
        self.calls = []  # (messages, tools) per invocation

    def chat(self, messages, tools=None, stream=True, on_event=None):
        self.calls.append({"messages": [dict(m) for m in messages], "tools": tools})
        return self.results.pop(0)


def tc(name, args, id="t1"):
    return ToolCall(id=id, name=name, arguments=json.dumps(args))


def make_ctx(tmp_path, tool_names=None, mode="default"):
    return {
        "ws": str(tmp_path),
        "workspace": str(tmp_path),
        "mode": mode,
        "cache": {},
        "tool_names": tool_names or sorted(HANDLERS),
    }


class TestScriptedTask:
    def test_tool_then_tool_then_answer(self, tmp_path):
        (tmp_path / "a.txt").write_text("hi\n")
        client = FakeClient([
            ChatResult(tool_calls=[tc("read_file", {"path": "a.txt"})]),
            ChatResult(tool_calls=[tc("write_file", {"path": "b.txt", "content": "x"})]),
            ChatResult(content="all done"),
        ])
        messages = [{"role": "user", "content": "task"}]
        res = run(client, messages, make_ctx(tmp_path))
        assert res.reason == "done" and res.content == "all done"
        assert res.turns == 3 and res.tool_calls == 2
        roles = [m["role"] for m in messages]
        assert roles == ["user", "assistant", "tool", "assistant", "tool", "assistant"]
        assert (tmp_path / "b.txt").read_text() == "x"
        assert "unchanged" not in messages[2]["content"]

    def test_turn_cap(self, tmp_path):
        call = ChatResult(tool_calls=[tc("list_dir", {}, id="x")])
        client = FakeClient([call] * 5)
        res = run(client, [], make_ctx(tmp_path), max_turns=3)
        assert res.reason == "turn_cap" and res.aborted and res.turns == 3

    def test_plan_mode_blocks_writes(self, tmp_path):
        client = FakeClient([
            ChatResult(tool_calls=[tc("write_file", {"path": "x", "content": "y"})]),
            ChatResult(content="ok, cannot write"),
        ])
        messages = []
        res = run(client, messages, make_ctx(tmp_path, mode="plan"))
        assert res.reason == "done"
        tool_msgs = [m for m in messages if m["role"] == "tool"]
        assert "permission denied" in tool_msgs[0]["content"]
        assert not (tmp_path / "x").exists()

    def test_plan_mode_allows_reads(self, tmp_path):
        (tmp_path / "a.txt").write_text("hi")
        client = FakeClient([
            ChatResult(tool_calls=[tc("read_file", {"path": "a.txt"})]),
            ChatResult(content="read ok"),
        ])
        res = run(client, [], make_ctx(tmp_path, mode="plan"))
        assert res.reason == "done" and not res.aborted

    def test_unknown_tool_returns_error_string(self, tmp_path):
        client = FakeClient([
            ChatResult(tool_calls=[tc("nonexistent", {})]),
            ChatResult(content="ok"),
        ])
        messages = []
        res = run(client, messages, make_ctx(tmp_path))
        assert "unknown tool" in [m for m in messages if m["role"] == "tool"][0]["content"]

    def test_tool_crash_becomes_error_value(self, tmp_path):
        client = FakeClient([
            ChatResult(tool_calls=[tc("read_file", {"path": None})]),
            ChatResult(content="recovered"),
        ])
        messages = []
        res = run(client, messages, make_ctx(tmp_path))
        tool_msgs = [m for m in messages if m["role"] == "tool"]
        assert tool_msgs[0]["content"].startswith("error:")

    def test_on_tool_callback(self, tmp_path):
        seen = []
        client = FakeClient([
            ChatResult(tool_calls=[tc("list_dir", {})]),
            ChatResult(content="done"),
        ])
        run(client, [], make_ctx(tmp_path), on_tool=lambda r: seen.append(r.name))
        assert seen == ["list_dir"]

    def test_permission_deny_via_callback(self, tmp_path):
        client = FakeClient([
            ChatResult(tool_calls=[tc("bash", {"command": "echo hi"})]),
            ChatResult(content="fine"),
        ])
        messages = []
        run(client, messages, make_ctx(tmp_path), check_permission=lambda n, a: "deny")
        tool_msgs = [m for m in messages if m["role"] == "tool"]
        assert "permission denied" in tool_msgs[0]["content"]

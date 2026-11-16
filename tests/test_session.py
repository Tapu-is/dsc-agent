"""Tests for session transcripts and loop-level compaction triggering."""
from __future__ import annotations

import json

import pytest

from dsc.client import ChatResult, ToolCall
from dsc.loop import run
from dsc.session import Session
from dsc.context import apply_summary, needs_compaction


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    return Session.new()


class FakeClient:
    def __init__(self, results):
        self.results = list(results)

    def chat(self, messages, tools=None, stream=True, on_event=None):
        return self.results.pop(0)


class TestSession:
    def test_new_creates_file_and_logs(self, session):
        session.log_meta("default", "deepseek-chat", "/tmp")
        session.log({"event": "tool", "name": "bash", "args": {"command": "ls"}, "output_len": 3, "duration_s": 0.1})
        lines = session.path.read_text().strip().splitlines()
        assert len(lines) == 2
        evs = [json.loads(l)["event"] for l in lines]
        assert evs == ["session_start", "tool"]

    def test_latest(self, session, tmp_path):
        session.log({"event": "x"})
        assert Session.latest() is not None
        assert Session.latest().sid == session.sid

    def test_save_and_load_messages(self, session):
        msgs = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}]
        session.save_messages(msgs)
        assert session.load_messages() == msgs

    def test_load_messages_takes_last(self, session):
        session.save_messages([{"role": "user", "content": "old"}])
        session.save_messages([{"role": "user", "content": "new"}])
        assert session.load_messages() == [{"role": "user", "content": "new"}]

    def test_by_id_missing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
        assert Session.by_id("nope") is None


class TestCompactionInLoop:
    """Simulate a long scripted task and assert compaction fires."""

    def make_ctx(self, tmp_path):
        return {
            "ws": str(tmp_path), "workspace": str(tmp_path), "mode": "default",
            "cache": {}, "tool_names": ["bash"],
        }

    def test_compaction_fires_on_long_task(self, tmp_path):
        bulk_cmd = "head -c 9000 /dev/zero | tr '\\0' x"  # each output ~2250 tokens
        tcs = [
            ChatResult(tool_calls=[ToolCall(id=f"t{i}", name="bash",
                                            arguments=json.dumps({"command": bulk_cmd}))])
            for i in range(4)
        ] + [ChatResult(content="done")]

        client = FakeClient(tcs)
        messages = [{"role": "user", "content": "long task"}]
        compacted = []

        def compact(msgs):
            if needs_compaction(msgs, context_tokens=2500):
                compacted.append(len(msgs))
                return apply_summary(msgs, "SUMMARY: user asked for a long task; f0..f5 echoed")
            return msgs

        res = run(client, messages, self.make_ctx(tmp_path), max_turns=60, compact=compact)
        assert res.reason == "done"
        assert compacted, "compaction should have fired during the long task"
        summaries = [
            m for m in messages
            if m.get("role") == "system" and "Summary" in str(m.get("content", ""))
        ]
        assert summaries, "compaction summary must be present as a system message"
        # after compaction: system summary + <=4 kept messages + final assistant
        assert len(messages) <= 7

    def test_no_compaction_when_small(self, tmp_path):
        client = FakeClient([ChatResult(content="done")])
        messages = [{"role": "user", "content": "hi"}]
        seen = []

        def compact(msgs):
            seen.append(len(msgs))
            return msgs

        run(client, messages, self.make_ctx(tmp_path), compact=compact)
        assert seen == [1]
        assert len(messages) == 2

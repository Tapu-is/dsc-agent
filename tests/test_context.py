"""Tests for context management: estimation, cache, repo context, compaction."""
from __future__ import annotations

from pathlib import Path

from dsc.context import (
    apply_summary,
    estimate_messages_tokens,
    estimate_tokens,
    file_cache_note,
    needs_compaction,
    repo_context,
    split_for_compaction,
    track_cache,
)


def test_estimate_tokens():
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd" * 10) == 10
    assert estimate_tokens("x" * 9) == 2


def test_estimate_messages_tokens():
    msgs = [{"role": "user", "content": "a" * 40}, {"role": "assistant", "content": "b" * 8}]
    assert estimate_messages_tokens(msgs) == 12


class TestFileCache:
    def test_unchanged_note(self, tmp_path: Path):
        f = tmp_path / "x.py"
        f.write_text("hello\n")
        cache = {}
        track_cache(cache, str(f))
        note = file_cache_note(cache, str(f))
        assert note is not None and "unchanged" in note and "hash" in note

    def test_changed_file_gets_none(self, tmp_path):
        f = tmp_path / "x.py"
        f.write_text("v1\n")
        cache = {}
        track_cache(cache, str(f))
        f.write_text("v2!\n")
        assert file_cache_note(cache, str(f)) is None

    def test_unknown_file(self, tmp_path):
        assert file_cache_note({}, str(tmp_path / "nope")) is None


class TestRepoContext:
    def test_in_git_repo(self, tmp_path):
        import subprocess
        subprocess.run(["git", "init", "-q"], cwd=tmp_path)
        (tmp_path / "README.md").write_text("# demo")
        ctx = repo_context(tmp_path)
        assert "branch" in ctx or "not a git" in ctx
        assert "# demo" in ctx

    def test_non_git(self, tmp_path):
        assert "not a git repository" in repo_context(tmp_path)


class TestCompaction:
    def test_threshold_triggers(self):
        msgs = [{"role": "user", "content": "x" * 4000}]  # ~1000 tokens
        assert not needs_compaction(msgs, context_tokens=100_000)
        assert needs_compaction(msgs, context_tokens=500)

    def test_split_keeps_last_four(self):
        msgs = [{"role": "user", "content": f"m{i}"} for i in range(10)]
        summarize, keep = split_for_compaction(msgs)
        assert len(summarize) == 6
        assert len(keep) == 4
        assert [m["content"] for m in keep] == ["m6", "m7", "m8", "m9"]

    def test_small_conversation_untouched(self):
        msgs = [{"role": "user", "content": "a"}] * 3
        summarize, keep = split_for_compaction(msgs)
        assert summarize == [] and len(keep) == 3

    def test_apply_summary_replaces(self):
        msgs = [{"role": "user", "content": f"m{i}"} for i in range(10)]
        out = apply_summary(msgs, "the user wants X; files a.py,b.py touched")
        assert len(out) == 5
        assert out[0]["role"] == "system"
        assert "Summary of the conversation" in out[0]["content"]
        assert "the user wants X" in out[0]["content"]
        assert [m["content"] for m in out[1:]] == ["m6", "m7", "m8", "m9"]

    def test_apply_summary_empty_is_noop(self):
        msgs = [{"role": "user", "content": f"m{i}"} for i in range(10)]
        assert apply_summary(msgs, "") == msgs

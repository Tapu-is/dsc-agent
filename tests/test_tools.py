"""Tests for the file tools and bash tool (offline)."""
from __future__ import annotations

from pathlib import Path

import pytest

from dsc.tools import files, shell
from dsc.tools import HANDLERS


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    (tmp_path / "app.py").write_text(
        "def hello():\n    return 1\n\ndef bye():\n    return 2\n"
    )
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "readme.md").write_text("# hi\n")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "lib.js").write_text("var x = 'needle'\n")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("import needle\nneedle()\n")
    (tmp_path / "big.txt").write_text("\n".join(f"line {i}" for i in range(3000)))
    return tmp_path


class TestEditFile:
    def test_exact_match_success(self, ws):
        r = files.edit_file(ws, path="app.py", old_string="return 1", new_string="return 42")
        assert "edited" in r
        assert "return 42" in (ws / "app.py").read_text()

    def test_not_found_with_suggestions(self, ws):
        r = files.edit_file(ws, path="app.py", old_string="return 999", new_string="x")
        assert r.startswith("error:")
        assert "closest match" in r
        assert "return" in r

    def test_ambiguous_match_errors(self, ws):
        (ws / "dup.py").write_text("x = 1\ny = 1\n")
        r = files.edit_file(ws, path="dup.py", old_string=" = 1\n", new_string=" = 2\n")
        assert "appears 2 times" in r

    def test_replace_all(self, ws):
        (ws / "dup.py").write_text("x = 1\ny = 1\n")
        r = files.edit_file(
            ws, path="dup.py", old_string="= 1", new_string="= 2", replace_all=True
        )
        assert "replaced 2 occurrences" in r
        assert "= 2" in (ws / "dup.py").read_text()

    def test_multiline_edit(self, ws):
        r = files.edit_file(
            ws, path="app.py",
            old_string="def hello():\n    return 1",
            new_string="def hello():\n    return 10",
        )
        assert "edited" in r
        assert "return 10" in (ws / "app.py").read_text()


class TestReadFile:
    def test_line_numbers_and_cap(self, ws):
        r = files.read_file(ws, path="big.txt", limit=10)
        assert r.splitlines()[0].startswith("# big.txt")
        assert "     1\tline 0" in r
        assert "line 9" in r and "line 10" not in r

    def test_offset(self, ws):
        r = files.read_file(ws, path="big.txt", offset=5, limit=2)
        assert "     5\tline 4" in r

    def test_truncation_notice(self, ws):
        r = files.read_file(ws, path="big.txt")
        assert "truncated" in r

    def test_missing_file(self, ws):
        assert files.read_file(ws, path="nope.py").startswith("error:")


class TestWriteFile:
    def test_write_creates_parents(self, ws):
        r = files.write_file(ws, path="a/b/c.txt", content="hello")
        assert "wrote" in r
        assert (ws / "a/b/c.txt").read_text() == "hello"

    def test_escape_refused(self, ws):
        r = files.write_file(ws, path="../evil.txt", content="no")
        assert r.startswith("error:")
        assert not (ws.parent / "evil.txt").exists()

    def test_absolute_outside_refused(self, ws):
        r = files.write_file(ws, path="/tmp/evil-dsc-test.txt", content="no")
        assert r.startswith("error:")


class TestGrep:
    def test_skips_vendor_dirs(self, ws):
        r = files.grep(ws, pattern="needle")
        assert "src/a.py:1" in r
        assert "node_modules" not in r

    def test_glob_filter_and_case(self, ws):
        r = files.grep(ws, pattern="NEEDLE", glob="*.py", case_insensitive=True)
        assert "src/a.py" in r
        r2 = files.grep(ws, pattern="NEEDLE", glob="*.py")
        assert "no matches" in r2


class TestMisc:
    def test_list_dir(self, ws):
        r = files.list_dir(ws, path=".")
        assert "app.py" in r and "docs/" in r

    def test_glob(self, ws):
        r = files.glob(ws, pattern="**/*.py")
        assert "app.py" in r and "src/a.py" in r

    def test_unknown_tool(self):
        from dsc.tools import dispatch
        assert dispatch("nope", {"ws": "."}, {}).startswith("error: unknown tool")


class TestBash:
    def test_runs_and_reports_exit(self, ws):
        r = shell.bash(ws, backend="none", command="echo hi")
        assert "hi" in r and "(exit 0" in r

    def test_nonzero_exit(self, ws):
        r = shell.bash(ws, backend="none", command="exit 3")
        assert "(exit 3" in r

    def test_empty_command(self, ws):
        assert shell.bash(ws, backend="none", command="").startswith("error:")

    def test_registry_has_required_tools(self):
        for name in ("read_file", "write_file", "edit_file", "list_dir", "glob",
                     "grep", "bash"):
            assert name in HANDLERS

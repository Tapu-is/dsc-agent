"""Tests for the sandbox: timeout kill, env scrubbing, path escape."""
from __future__ import annotations

import os
from pathlib import Path

from dsc.sandbox import detect_sandbox, run_command, scrub_env


def test_scrub_env_removes_keys():
    env = {
        "PATH": "/usr/bin",
        "DEEPSEEK_API_KEY": "sk-123",
        "DSC_API_KEY": "sk-456",
        "GITHUB_TOKEN": "gh_1",
        "MY_KEY": "x",
        "MY_SECRET": "y",
        "HOME": "/root",
    }
    out = scrub_env(env)
    assert "PATH" in out and "HOME" in out
    for k in ("DEEPSEEK_API_KEY", "DSC_API_KEY", "GITHUB_TOKEN", "MY_KEY", "MY_SECRET"):
        assert k not in out


def test_detect_sandbox_fallback():
    assert detect_sandbox("none") == "none"
    assert detect_sandbox("docker") in ("docker", "none")  # depends on env


class TestRunCommand:
    def test_basic_run(self, tmp_path: Path):
        code, out, dur = run_command("echo hello", str(tmp_path))
        assert code == 0 and "hello" in out and dur >= 0

    def test_cwd_defaults_to_workspace(self, tmp_path: Path):
        (tmp_path / "marker.txt").write_text("here")
        code, out, _ = run_command("cat marker.txt", str(tmp_path))
        assert code == 0 and "here" in out

    def test_path_escape_refused(self, tmp_path: Path):
        code, out, _ = run_command("pwd", str(tmp_path), cwd="/etc")
        assert code == 126
        assert "escapes workspace" in out

    def test_timeout_kills_process(self, tmp_path: Path):
        code, out, dur = run_command(
            "sleep 30", str(tmp_path), timeout=1.0
        )
        assert code == 124
        assert "timeout" in out
        assert dur < 10

    def test_env_scrubbing_live(self, tmp_path: Path):
        os.environ["DSC_TEST_KEY"] = "sekrit"
        try:
            code, out, _ = run_command(
                "echo ${DSC_TEST_KEY:-clean}", str(tmp_path)
            )
            assert "clean" in out
            assert "sekrit" not in out
        finally:
            os.environ.pop("DSC_TEST_KEY", None)

    def test_exit_code_propagates(self, tmp_path: Path):
        code, _, _ = run_command("exit 7", str(tmp_path))
        assert code == 7

    def test_output_cap_keeps_head_and_tail(self, tmp_path: Path):
        code, out, _ = run_command(
            "python3 -c \"print('A'*20000); print('B'*20000); print('TAIL_MARKER')\"",
            str(tmp_path),
        )
        assert "TAIL_MARKER" in out
        assert "chars omitted" in out

    def test_backend_bwrap_if_present(self, tmp_path: Path):
        backend = detect_sandbox("auto")
        code, out, _ = run_command("echo bw", str(tmp_path), backend=backend)
        assert code == 0 and "bw" in out

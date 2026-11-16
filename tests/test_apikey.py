"""Tests for config saving and the interactive API key prompt."""
from __future__ import annotations

import builtins

import pytest

from dsc import app as app_mod
from dsc.app import UI, ensure_api_key
from dsc.config import Config, save_config_value


class TestSaveConfigValue:
    def test_append(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
        assert save_config_value("api_key", "sk-test")
        assert 'api_key = "sk-test"' in (tmp_path / "cfg" / "dsc" / "config.toml").read_text()

    def test_replace_existing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
        d = tmp_path / "cfg" / "dsc"
        d.mkdir(parents=True)
        (d / "config.toml").write_text('model = "deepseek-chat"\napi_key = "old"\n')
        save_config_value("api_key", "new")
        text = (d / "config.toml").read_text()
        assert '"new"' in text and '"old"' not in text
        assert 'model = "deepseek-chat"' in text

    def test_load_uses_saved_key(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
        save_config_value("api_key", "sk-loaded")
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        monkeypatch.delenv("DSC_API_KEY", raising=False)
        cfg = __import__("dsc.config", fromlist=["load_config"]).load_config()
        assert cfg.api_key == "sk-loaded"


class TestEnsureApiKey:
    def test_noop_when_key_set(self):
        cfg = Config(api_key="sk-present")
        assert ensure_api_key(cfg, UI(no_color=True)) is True
        assert cfg.api_key == "sk-present"

    def test_non_interactive_fails(self):
        cfg = Config(api_key=None)
        assert ensure_api_key(cfg, UI(no_color=True), interactive=False) is False

    def test_prompt_gets_key(self, monkeypatch):
        cfg = Config(api_key=None)
        answers = iter(["sk-interactive", "n"])

        def fake_input(_=""):
            return next(answers)

        import getpass
        monkeypatch.setattr(getpass, "getpass", lambda _="": next(answers))
        monkeypatch.setattr(builtins, "input", fake_input)
        assert ensure_api_key(cfg, UI(no_color=True), interactive=True) is True
        assert cfg.api_key == "sk-interactive"

    def test_empty_answer_fails(self, monkeypatch):
        cfg = Config(api_key=None)
        import getpass
        monkeypatch.setattr(getpass, "getpass", lambda _="": "")
        assert ensure_api_key(cfg, UI(no_color=True), interactive=True) is False

    def test_save_answer_persists(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
        cfg = Config(api_key=None)
        it = iter(["sk-save-me", "y"])

        def fake_input(_=""):
            return next(it)

        import getpass
        monkeypatch.setattr(getpass, "getpass", lambda _="": next(it))
        monkeypatch.setattr(builtins, "input", fake_input)
        assert ensure_api_key(cfg, UI(no_color=True), interactive=True) is True
        saved = (tmp_path / "cfg" / "dsc" / "config.toml").read_text()
        assert 'api_key = "sk-save-me"' in saved

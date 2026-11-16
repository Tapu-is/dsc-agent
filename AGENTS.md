# AGENTS.md

This repo is **dsc**, a Claude-Code-style terminal coding agent powered by DeepSeek.

- Run `pytest -q` before committing. All tests must pass offline (no API key needed).
- Keep modules under ~400 lines; type hints everywhere; docstring per module.
- Tool handlers return error strings, never raise.

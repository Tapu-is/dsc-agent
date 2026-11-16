# dsc — a Claude-Code-style terminal coding agent, powered by DeepSeek

`dsc` is an autonomous terminal coding agent. Give it a task and it reads,
edits, and creates files, runs shell commands, searches the codebase, fetches
web pages, and keeps a todo list — iterating until the task is done.

Runs on plain Linux and on Android Termux (aarch64, no root, no Docker required).

## Install (Linux)

```sh
git clone <this-repo> dsc-agent
cd dsc-agent
pip install .            # or: pipx install .
export DEEPSEEK_API_KEY=sk-...
dsc --version
dsc --ping               # one real API call, should print ✔ pong
```

Requires Python 3.9+. Dependencies: `httpx` only (plus `pytest` for dev).

## Install (Termux)

```sh
pkg update
pkg install python git
pip install .            # inside the repo directory
export DEEPSEEK_API_KEY=sk-...
dsc --ping
```

Notes for Termux:
- Storage: `termux-setup-storage` if you want to work in `/sdcard`.
- The sandbox falls back to rlimits + cwd jailing (bwrap is available via
  `pkg install bubblewrap` for stronger isolation). A one-time notice is printed.
- No root and no Docker needed anywhere in the default path.

## Configuration

Precedence: **CLI flags > environment variables > `~/.config/dsc/config.toml` > defaults.**

Config file (`~/.config/dsc/config.toml`, all keys optional):

```toml
model = "deepseek-chat"
base_url = "https://api.deepseek.com/v1"
context_tokens = 96000
mode = "default"           # plan | default | yolo
workspace_root = "/home/me/project"
search_backend = "duckduckgo"
sandbox = "auto"           # auto | docker | bwrap | none
allow_network_for_bash = false
```

Environment variables: `DEEPSEEK_API_KEY` (or `DSC_API_KEY`), `DSC_BASE_URL`,
`DSC_MODEL`, `DSC_CONTEXT_TOKENS`, `DSC_MODE`, `DSC_WORKSPACE`, `DSC_SANDBOX`,
`DSC_SEARCH_BACKEND`, `DSC_ALLOW_NETWORK_FOR_BASH`, `DSC_USE_DOCKER=1`.

CLI flags: `--mode`, `--model`, `--yes`, `--no-color`, `--continue`,
`--session <id>`, `--ping`, `--version`. `NO_COLOR` is respected.

## Modes

- `plan` — read-only tools only; bash and all writes are refused.
- `default` — bash and out-of-workspace writes prompt y/n/a on stderr
  (`a` = allow for the rest of the session).
- `yolo` — everything auto-approved, with a red warning banner.

## Usage

```sh
dsc                                    # REPL
dsc "fix the failing test in tests/test_x.py"   # one-shot task
dsc --mode plan "explain how routing works"     # read-only exploration
dsc --continue                                  # resume latest session
dsc --session 20260101-120000-abc123            # resume a specific session
```

Transcripts are appended to `~/.local/share/dsc/sessions/<id>.jsonl`.

## 60-second demo

```sh
export DEEPSEEK_API_KEY=sk-...
mkdir -p /tmp/demo && cd /tmp/demo
# 1) hello world in one shot
dsc "create hello.py that prints the 10th Fibonacci number, run it, and verify the output"
# 2) plan mode refuses writes
dsc --mode plan "delete everything in /tmp"
# 3) default mode asks before destructive commands
dsc --mode default "run rm -rf build"
# 4) web task
dsc "fetch https://example.com and summarize it in 3 bullets"
# 5) resume and ask what happened
dsc --continue "what did you do last?"
```

## Architecture

- `dsc/client.py` — OpenAI-compatible chat client: SSE streaming, 429/5xx
  backoff with Retry-After, key redaction.
- `dsc/loop.py` — agent loop: tool dispatch, 60-turn cap, interruption.
- `dsc/context.py` — token estimation (chars/4), file cache ("unchanged since
  last read"), repo context, compaction at 70% of context keeping last 4 messages.
- `dsc/permissions.py` — plan/default/yolo enforcement + approval prompts.
- `dsc/sandbox.py` — docker / bubblewrap / rlimits command jail, env scrubbing.
- `dsc/session.py` — append-only JSONL transcripts.
- `dsc/tools/` — file, shell, web, todo tools + `search_agent` subagent.

## Development

```sh
pip install -e ".[dev]"
pytest -q
```

See `AGENTS.md` and `DECISIONS.md`.

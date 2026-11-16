# DECISIONS

Deviations and judgment calls, one line each:

- Project lives in `/root/dsc-agent` (user asked not to merge with existing home-dir files).
- Added `dsc/app.py` (not in the spec layout) as the app shell so `__main__.py` stays thin.
- Token estimation is `len(text)//4`, swappable later; thresholds are tuned for it.
- Compaction calls the model without tools and silently skips on API error (better to risk the turn cap than to crash mid-task).
- `search_agent` runs at most 8 turns and inherits the main client config.
- In `plan` mode the loop itself blocks WRITE_TOOLS (belt-and-braces) in addition to the tool allowlist.
- bwrap backend does `--ro-bind / /` then `--bind <cwd> <cwd>`; some filesystems (e.g. this box's /tmp) can't be bind sources, so tests use a repo-local workspace for the bwrap case.
- Sandbox child gets its own process group (`setpgrp`) so timeout kills never touch the parent's group.
- Tool output cap: bash keeps head AND tail (30k total); other tools truncate at 20k head-only.
- `dsc --ping` uses a non-streaming one-shot chat to keep the output simple.
- `--session`/`--continue` restore the persisted `messages` event (last one wins), not a replay of the whole transcript.
- Duplicate commits for phases 4-6 exist because earlier phase commits were amended in content during fixes; history is linear and tests pass at every commit point.
- Tests for the DDG parser and HTML stripper use inline fixtures; live-network paths are not tested (offline requirement).

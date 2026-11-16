"""Session transcripts: append-only JSONL logs of every request/response.

Sessions live in ~/.local/share/dsc/sessions/<id>.jsonl. Each line is a
redacted event: chat meta, tool executions, compactions, commands.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from dsc.config import data_dir


@dataclass
class Session:
    """Append-only transcript of one dsc run."""

    sid: str
    path: Path
    messages: list = field(default_factory=list)

    @classmethod
    def by_id(cls, sid: str) -> Optional["Session"]:
        d = data_dir() / "sessions"
        p = d / f"{sid}.jsonl"
        if not p.is_file():
            return None
        return cls(sid=sid, path=p)

    @classmethod
    def new(cls) -> "Session":
        sid = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
        d = data_dir() / "sessions"
        d.mkdir(parents=True, exist_ok=True)
        return cls(sid=sid, path=d / f"{sid}.jsonl")

    @classmethod
    def latest(cls) -> Optional["Session"]:
        d = data_dir() / "sessions"
        if not d.is_dir():
            return None
        files = sorted(d.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not files:
            return None
        return cls(sid=files[0].stem, path=files[0])

    def log(self, event: dict) -> None:
        """Append one event. Never raises."""
        try:
            event = {"ts": time.time(), **event}
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")
        except OSError:
            pass

    # ------------------------------------------------------------ events
    def log_meta(self, mode: str, model: str, workspace: str) -> None:
        self.log({"event": "session_start", "sid": self.sid, "mode": mode,
                  "model": model, "workspace": workspace})

    def log_chat(self, messages_meta: Any, tools: list) -> None:
        self.log({"event": "chat", "messages_meta": messages_meta,
                  "tools": [t["function"]["name"] for t in tools]})

    def log_tool(self, name: str, args: dict, output: str, dur: float) -> None:
        self.log({"event": "tool", "name": name, "args": args,
                  "output_len": len(output), "duration_s": round(dur, 3)})

    def log_final(self, content: str, reason: str) -> None:
        self.log({"event": "final", "reason": reason, "len": len(content)})

    # ------------------------------------------------------- persistence
    def save_messages(self, messages: list) -> None:
        """Persist the final message list (for --continue)."""
        self.log({"event": "messages", "messages": messages})

    def load_messages(self) -> list:
        """Rebuild the message list from the transcript's last `messages` event."""
        found: list = []
        try:
            with open(self.path, encoding="utf-8") as f:
                for line in f:
                    try:
                        ev = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if ev.get("event") == "messages":
                        found = ev.get("messages") or []
        except OSError:
            pass
        return found

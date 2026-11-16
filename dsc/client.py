"""DeepSeek/OpenAI-compatible chat client: streaming, retries, redaction.

Uses httpx against an OpenAI-compatible /chat/completions endpoint.
Retries with exponential backoff on 429/5xx (max 4 attempts, honoring
Retry-After). Streaming uses SSE and yields incremental deltas. Every
request/response is reported to an optional audit hook with the API key
redacted so the session transcript can log it.
"""
from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Optional

import httpx

from dsc.config import Config

DEFAULT_TIMEOUT = 300.0
MAX_ATTEMPTS = 4
AuditHook = Callable[[dict], None]  # receives a redacted event dict


class ClientError(Exception):
    """Raised for unrecoverable client/API errors."""


def _redact(payload: Any) -> Any:
    """Recursively replace api key material with [redacted]."""
    if isinstance(payload, dict):
        out = {}
        for k, v in payload.items():
            if k.lower() in ("authorization", "api-key", "x-api-key") or (
                isinstance(v, str) and ("sk-" in v and len(v) > 20)
            ):
                out[k] = "[redacted]"
            else:
                out[k] = _redact(v)
        return out
    if isinstance(payload, list):
        return [_redact(x) for x in payload]
    return payload


@dataclass
class ToolCallDelta:
    """Incremental fragment of a streamed tool call."""

    index: int = 0
    id: str = ""
    name: str = ""
    arguments: str = ""


@dataclass
class StreamEvent:
    """One event from a streaming completion."""

    kind: str  # "text" | "tool_call_start" | "tool_call_delta" | "end"
    text: str = ""
    tool: Optional[ToolCallDelta] = None


@dataclass
class ToolCall:
    """A completed tool call returned by the model."""

    id: str
    name: str
    arguments: str  # raw JSON string

    def parse_args(self) -> dict:
        try:
            v = json.loads(self.arguments or "{}")
            return v if isinstance(v, dict) else {}
        except json.JSONDecodeError:
            return {}


@dataclass
class ChatResult:
    """Full result of a (possibly streamed) chat completion."""

    content: str = ""
    tool_calls: list = field(default_factory=list)  # list[ToolCall]
    finish_reason: str = ""
    usage: dict = field(default_factory=dict)


class ChatClient:
    """OpenAI-compatible chat completion client with streaming + retries."""

    def __init__(
        self,
        cfg: Config,
        audit: Optional[AuditHook] = None,
        transport: Optional[httpx.BaseTransport] = None,
    ) -> None:
        self.cfg = cfg
        self.audit = audit
        headers = {
            "Authorization": f"Bearer {cfg.api_key or ''}",
            "Content-Type": "application/json",
        }
        self.http = httpx.Client(
            base_url=cfg.base_url,
            headers=headers,
            timeout=DEFAULT_TIMEOUT,
            transport=transport,
        )

    # ------------------------------------------------------------------ core
    def chat(
        self,
        messages: list,
        tools: Optional[list] = None,
        stream: bool = True,
        on_event: Optional[Callable[[StreamEvent], None]] = None,
    ) -> ChatResult:
        """Send a chat completion request; returns the assembled result."""
        body: dict[str, Any] = {
            "model": self.cfg.model,
            "messages": messages,
            "stream": stream,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"

        payload = self._send_with_retries(body)
        if stream:
            return self._consume_stream(payload, on_event)
        return self._consume_json(payload)

    # --------------------------------------------------------------- retries
    def _send_with_retries(self, body: dict) -> httpx.Response:
        attempt = 0
        while True:
            attempt += 1
            try:
                resp = self.http.post(
                    "/chat/completions", json=body, headers=self._headers(body)
                )
            except httpx.TimeoutException as e:
                if attempt >= MAX_ATTEMPTS:
                    raise ClientError(f"API request timed out after {MAX_ATTEMPTS} attempts: {e}")
                self._audit({"event": "retry", "attempt": attempt, "error": "timeout"})
                time.sleep(self._backoff(attempt, None))
                continue
            except httpx.HTTPError as e:
                if attempt >= MAX_ATTEMPTS:
                    raise ClientError(f"API connection failed after {MAX_ATTEMPTS} attempts: {e}")
                self._audit({"event": "retry", "attempt": attempt, "error": str(e)})
                time.sleep(self._backoff(attempt, None))
                continue

            self._audit(
                {
                    "event": "http_response",
                    "status": resp.status_code,
                    "request": _redact(
                        {k: v for k, v in body.items() if k != "messages"}
                    ),
                    "messages_meta": [
                        {"role": m.get("role"), "len": len(str(m.get("content") or ""))}
                        for m in body.get("messages", [])
                    ],
                }
            )
            if resp.status_code == 200:
                return resp
            if resp.status_code in (429, 500, 502, 503, 504, 529) and attempt < MAX_ATTEMPTS:
                retry_after = resp.headers.get("Retry-After")
                self._audit(
                    {
                        "event": "retry",
                        "attempt": attempt,
                        "status": resp.status_code,
                        "retry_after": retry_after,
                    }
                )
                time.sleep(self._backoff(attempt, retry_after))
                continue
            if resp.status_code in (401, 403):
                raise ClientError(
                    f"API returned {resp.status_code}: check DEEPSEEK_API_KEY "
                    f"({self._err_body(resp)})"
                )
            raise ClientError(
                f"API returned {resp.status_code}: {self._err_body(resp)}"
            )

    @staticmethod
    def _headers(body: dict) -> dict:
        return {"Accept": "text/event-stream" if body.get("stream") else "application/json"}

    @staticmethod
    def _backoff(attempt: int, retry_after: Optional[str]) -> float:
        if retry_after:
            try:
                return min(float(retry_after), 60.0)
            except ValueError:
                pass
        return min(2.0 ** attempt + random.random(), 30.0)

    @staticmethod
    def _err_body(resp: httpx.Response) -> str:
        try:
            data = resp.json()
            msg = data.get("error", {})
            return msg.get("message", json.dumps(data)) if isinstance(msg, dict) else str(msg)
        except Exception:
            return resp.text[:500]

    def _audit(self, event: dict) -> None:
        if self.audit:
            try:
                self.audit(_redact(event))
            except Exception:
                pass

    # --------------------------------------------------------------- parsing
    def _consume_json(self, resp: httpx.Response) -> ChatResult:
        try:
            data = resp.json()
        except json.JSONDecodeError as e:
            raise ClientError(f"invalid JSON from API: {e}")
        try:
            choice = data["choices"][0]
        except (KeyError, IndexError):
            raise ClientError(f"unexpected API response shape: {json.dumps(data)[:500]}")
        msg = choice.get("message", {})
        result = ChatResult(
            content=msg.get("content") or "",
            finish_reason=choice.get("finish_reason") or "",
            usage=data.get("usage") or {},
        )
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function", {})
            result.tool_calls.append(
                ToolCall(id=tc.get("id", ""), name=fn.get("name", ""),
                         arguments=fn.get("arguments", ""))
            )
        return result

    def _consume_stream(
        self, resp: httpx.Response, on_event: Optional[Callable[[StreamEvent], None]] = None
    ) -> ChatResult:
        result = ChatResult()
        open_tools: dict[int, ToolCall] = {}

        for line in resp.iter_lines():
            if not line or not line.startswith("data:"):
                continue
            data = line[len("data:"):].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue
            choices = chunk.get("choices") or [{}]
            delta = choices[0].get("delta", {}) if choices else {}
            if chunk.get("usage"):
                result.usage = chunk["usage"]
            fr = choices[0].get("finish_reason") if choices else None
            if fr:
                result.finish_reason = fr

            text = delta.get("content")
            if text:
                result.content += text
                if on_event:
                    on_event(StreamEvent(kind="text", text=text))

            for td in delta.get("tool_calls") or []:
                idx = td.get("index", 0)
                fn = td.get("function", {}) or {}
                if idx not in open_tools:
                    tc = ToolCall(
                        id=td.get("id", ""), name=fn.get("name", ""), arguments=""
                    )
                    open_tools[idx] = tc
                    if on_event:
                        on_event(
                            StreamEvent(
                                kind="tool_call_start",
                                tool=ToolCallDelta(index=idx, id=tc.id, name=tc.name),
                            )
                        )
                if td.get("id"):
                    open_tools[idx].id = td["id"]
                if fn.get("name"):
                    open_tools[idx].name = fn["name"]
                args = fn.get("arguments")
                if args:
                    open_tools[idx].arguments += args
                    if on_event:
                        on_event(
                            StreamEvent(
                                kind="tool_call_delta",
                                tool=ToolCallDelta(index=idx, arguments=args),
                            )
                        )
        result.tool_calls = [open_tools[i] for i in sorted(open_tools)]
        if on_event:
            on_event(StreamEvent(kind="end"))
        return result

    # ------------------------------------------------------------------ misc
    def ping(self) -> str:
        """One tiny real API call; returns the assistant reply text."""
        res = self.chat(
            [{"role": "user", "content": "Reply with exactly: pong"}], stream=False
        )
        return res.content.strip()

    def close(self) -> None:
        self.http.close()


def ping_config(cfg: Config) -> str:
    """Standalone ping used by `dsc --ping`."""
    if not cfg.api_key:
        raise ClientError(
            "No API key found. Set DEEPSEEK_API_KEY (or DSC_API_KEY) in the environment, "
            "or api_key in ~/.config/dsc/config.toml."
        )
    c = ChatClient(cfg)
    try:
        return c.ping()
    finally:
        c.close()

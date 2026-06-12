"""Minimal OpenAI-compatible LLM client (tianshu gateway / amd-bridge / any).

Configured entirely via environment variables so the deployment can point at
any OpenAI-compatible endpoint:

    LLM_BASE_URL   default https://tianshu-gateway.cloud/v1
    LLM_API_KEY    bearer key
    LLM_MODEL      default "auto"

Two roles only — both stay within the "conversational retrieval" boundary:
  * complete_json: turn the user's plain-English question into a precise search
    (or decide a clarifying question is needed). Returns parsed JSON.
  * stream_chat: organize the *retrieved real cases* into a readable answer,
    streaming tokens. The model never invents cases or gives legal conclusions.
"""
from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator

import httpx

LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://tianshu-gateway.cloud/v1").rstrip("/")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
# Default to a model that returns the final answer in `content` (a separate
# `reasoning` channel may also exist and is ignored). "auto" can resolve to a
# backend that only emits `reasoning` with content==null, which we cannot use.
LLM_MODEL = os.getenv("LLM_MODEL", "Qwen/Qwen3-8B")
_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "60"))


def _headers() -> dict:
    h = {"Content-Type": "application/json"}
    if LLM_API_KEY:
        h["Authorization"] = f"Bearer {LLM_API_KEY}"
    return h


async def complete_json(messages: list[dict], *, max_tokens: int = 400) -> dict:
    """Non-streaming call expecting a JSON object back. Returns {} on failure."""
    payload = {
        "model": LLM_MODEL,
        "messages": messages,
        "temperature": 0.1,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
        # Disable upstream "thinking" so the answer lands in `content` (not in a
        # separate reasoning channel). The gateway maps this to enable_thinking=false
        # for vLLM/SiliconFlow and drops it for plain models.
        "reasoning_effort": "off",
    }
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(
            f"{LLM_BASE_URL}/chat/completions", headers=_headers(), json=payload
        )
        resp.raise_for_status()
        msg = resp.json()["choices"][0]["message"]
        # Thinking models may put the text in reasoning_content/reasoning and
        # leave content == null; fall back so the JSON can still be salvaged.
        content = msg.get("content") or msg.get("reasoning_content") or msg.get("reasoning") or ""
    try:
        return json.loads(content)
    except (json.JSONDecodeError, TypeError):
        # Some gateways ignore response_format; try to salvage a JSON object.
        start, end = content.find("{"), content.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(content[start : end + 1])
            except json.JSONDecodeError:
                pass
        return {}


async def stream_chat(messages: list[dict], *, max_tokens: int = 900) -> AsyncIterator[str]:
    """Streaming chat completion; yields text deltas as they arrive."""
    payload = {
        "model": LLM_MODEL,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": max_tokens,
        "stream": True,
        # Disable upstream "thinking" so the grounded answer streams in `content`.
        "reasoning_effort": "off",
    }
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        async with client.stream(
            "POST", f"{LLM_BASE_URL}/chat/completions",
            headers=_headers(), json=payload,
        ) as resp:
            resp.raise_for_status()
            saw_content = False
            reasoning_buf = []
            finish_reason = ""
            async for line in resp.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                    choice = obj["choices"][0]
                    delta = choice.get("delta") or {}
                    finish_reason = choice.get("finish_reason") or finish_reason
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue
                # Prefer the real answer channel (`content`). Stream it straight
                # through. Buffer any `reasoning` only as a fallback for models
                # that put their whole answer there with content == null.
                content = delta.get("content")
                if content:
                    saw_content = True
                    yield content
                    continue
                if not saw_content:
                    chunk = delta.get("reasoning_content") or delta.get("reasoning")
                    if chunk:
                        reasoning_buf.append(chunk)
            # Nothing came through `content` at all: fall back to reasoning so the
            # user still gets the model's grounded analysis instead of nothing.
            if not saw_content and reasoning_buf:
                yield "".join(reasoning_buf)
            if finish_reason == "length":
                yield "\n\n_[Response truncated by the model's length limit. Ask a narrower follow-up question to continue the research.]_"

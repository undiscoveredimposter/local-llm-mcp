import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx

from .usage import estimate_tokens


class BackendError(RuntimeError):
    pass


@dataclass
class ChatReply:
    text: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    estimated: bool


class LlamaSwap:
    """llama-swap's OpenAI-compatible API; the `model` name selects (and loads) a profile."""

    def __init__(self, client: httpx.AsyncClient):
        self._client = client

    async def chat(
        self, model: str, messages: list[dict[str, str]], *, max_tokens: int, temperature: float
    ) -> ChatReply:
        data = await self._request(
            "POST",
            "/v1/chat/completions",
            json={
                "model": model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "stream": False,
            },
        )
        text = data["choices"][0]["message"]["content"] or ""
        usage = data.get("usage") or {}
        if usage.get("completion_tokens") is not None:
            return ChatReply(
                text, data.get("model", model),
                usage.get("prompt_tokens", 0), usage["completion_tokens"], False,
            )
        prompt = "".join(m["content"] for m in messages)
        return ChatReply(text, model, estimate_tokens(prompt), estimate_tokens(text), True)

    async def models(self) -> list[str]:
        data = await self._request("GET", "/v1/models")
        return [m["id"] for m in data.get("data", [])]

    async def running(self) -> list[str]:
        data = await self._request("GET", "/running")
        return [r["model"] for r in data.get("running", [])]

    async def unload(self) -> None:
        await self._request("GET", "/unload")

    async def _request(self, method: str, path: str, **kw: Any) -> dict[str, Any]:
        try:
            r = await self._client.request(method, path, **kw)
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise BackendError(f"llama-swap {path}: {e!r}") from e
        try:
            return r.json()
        except ValueError:
            return {}


class ComfyClient:
    def __init__(self, client: httpx.AsyncClient, timeout: float, poll_interval: float = 0.5):
        self._client = client
        self._timeout = timeout
        self._poll = poll_interval

    async def free(self) -> None:
        await self._request("POST", "/free", json={"unload_models": True, "free_memory": True})

    async def generate(
        self,
        workflow: dict[str, Any],
        on_progress: Callable[[float], Awaitable[None]] | None = None,
    ) -> bytes:
        """Queue a workflow, wait for it, and return the first output image."""
        queued = await self._request(
            "POST", "/prompt", json={"prompt": workflow, "client_id": uuid.uuid4().hex}
        )
        prompt_id = queued["prompt_id"]
        deadline = time.monotonic() + self._timeout
        while time.monotonic() < deadline:
            entry = (await self._request("GET", f"/history/{prompt_id}")).get(prompt_id)
            if entry:
                status = entry.get("status", {})
                if status.get("status_str") == "error":
                    raise BackendError(f"ComfyUI workflow failed: {status.get('messages')}")
                for out in entry.get("outputs", {}).values():
                    for img in out.get("images", []):
                        return await self._view(img)
                raise BackendError("ComfyUI finished without producing an image")
            if on_progress:
                await on_progress(min(0.95, 1 - (deadline - time.monotonic()) / self._timeout))
            await asyncio.sleep(self._poll)
        raise BackendError(f"ComfyUI timed out after {self._timeout:.0f}s")

    async def _view(self, img: dict[str, str]) -> bytes:
        try:
            r = await self._client.get(
                "/view",
                params={k: img.get(k, "") for k in ("filename", "subfolder", "type")},
            )
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise BackendError(f"ComfyUI /view: {e!r}") from e
        return r.content

    async def _request(self, method: str, path: str, **kw: Any) -> dict[str, Any]:
        try:
            r = await self._client.request(method, path, **kw)
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise BackendError(f"ComfyUI {path}: {e!r}") from e
        try:
            return r.json()
        except ValueError:
            return {}

import asyncio
import io
import json

import httpx
import pytest
from mcp.server.fastmcp import FastMCP
from PIL import Image as PILImage

from local_gpu_mcp.arbiter import GpuLock
from local_gpu_mcp.config import Settings
from local_gpu_mcp.server import create_server
from local_gpu_mcp.usage import USAGE_PREFIX


def png_bytes() -> bytes:
    buf = io.BytesIO()
    PILImage.new("RGB", (1024, 1024), "red").save(buf, "PNG")
    return buf.getvalue()


class FakeBackends:
    """Stands in for llama-swap and ComfyUI; records the calls it receives."""

    def __init__(self, *, usage: bool = True):
        self.calls: list[str] = []
        self.usage = usage

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append(path)
        if path == "/v1/chat/completions":
            body = {"model": "qwen-coder", "choices": [{"message": {"content": "def f(): ..."}}]}
            if self.usage:
                body["usage"] = {"prompt_tokens": 40, "completion_tokens": 12}
            return httpx.Response(200, json=body)
        if path == "/prompt":
            return httpx.Response(200, json={"prompt_id": "abc"})
        if path == "/history/abc":
            return httpx.Response(200, json={"abc": {
                "status": {"status_str": "success"},
                "outputs": {"9": {"images": [{"filename": "a.png", "subfolder": "", "type": "output"}]}}}})
        if path == "/view":
            return httpx.Response(200, content=png_bytes())
        if path == "/running":
            return httpx.Response(200, json={"running": [{"model": "code", "state": "ready"}]})
        if path in ("/unload", "/free"):
            return httpx.Response(200, json={})
        return httpx.Response(404)


@pytest.fixture
def backends() -> FakeBackends:
    return FakeBackends()


def make(tmp_path, backends: FakeBackends, **kw) -> FastMCP:
    """One server per call stands in for one Claude Code session; they share the lock dir."""
    settings = Settings(output_dir=tmp_path, lock_dir=tmp_path / "lock", **kw)
    return create_server(settings, transport=httpx.MockTransport(backends))


async def call(mcp: FastMCP, name: str, args: dict):
    """Content blocks of a tool call (some SDK versions pair them with structured output)."""
    result = await mcp.call_tool(name, args)
    return result[0] if isinstance(result, tuple) else result


def usage_of(blocks) -> dict:
    text = blocks[-1].text
    assert text.startswith(USAGE_PREFIX)
    return json.loads(text[len(USAGE_PREFIX):])


async def test_local_code_reports_usage(tmp_path, backends):
    mcp = make(tmp_path, backends)
    blocks = await call(mcp, "local_code", {"task": "write f", "context": "x = 1"})
    assert blocks[0].text == "def f(): ..."
    u = usage_of(blocks)
    assert (u["tool"], u["backend"], u["prompt_tokens"], u["completion_tokens"]) == (
        "local_code", "local", 40, 12)
    assert u["estimated"] is False and u["mode_switched"] is True


async def test_usage_is_estimated_when_backend_omits_it(tmp_path):
    mcp = make(tmp_path, FakeBackends(usage=False))
    blocks = await call(mcp, "local_chat", {"prompt": "hello there"})
    u = usage_of(blocks)
    assert u["estimated"] is True and u["completion_tokens"] > 0


async def test_image_frees_llm_then_llm_frees_comfy(tmp_path, backends):
    mcp = make(tmp_path, backends)
    await call(mcp, "local_code", {"task": "t"})
    blocks = await call(mcp, "generate_image", {"prompt": "a cat"})
    assert "/unload" in backends.calls
    u = usage_of(blocks)
    assert (u["tool"], u["mode"], u["width"], u["steps"]) == ("generate_image", "image", 1024, 25)
    assert list(tmp_path.glob("*.png"))
    assert any(b.type == "image" for b in blocks)

    await call(mcp, "local_chat", {"prompt": "hi"})
    assert "/free" in backends.calls


async def test_same_mode_does_not_switch(tmp_path, backends):
    mcp = make(tmp_path, backends)
    await call(mcp, "local_code", {"task": "one"})
    blocks = await call(mcp, "local_code", {"task": "two"})
    assert usage_of(blocks)["mode_switched"] is False


async def test_gpu_status_and_bad_mode(tmp_path, backends):
    mcp = make(tmp_path, backends)
    status = json.loads((await call(mcp, "gpu_status", {}))[0].text)
    assert status == {"mode": "idle", "holder": None, "loaded_models": ["code"]}
    with pytest.raises(Exception, match="mode must be one of"):
        await call(mcp, "set_mode", {"mode": "turbo"})


def other_session_holds_gpu(tmp_path) -> GpuLock:
    lock = GpuLock(tmp_path / "lock")
    assert lock.try_acquire("generate_image")
    return lock


async def test_busy_gpu_is_reported_with_holder(tmp_path, backends):
    mcp = make(tmp_path, backends)
    held = other_session_holds_gpu(tmp_path)
    with pytest.raises(Exception, match=r"GPU busy: .*running generate_image.*if_busy='wait'"):
        await call(mcp, "local_chat", {"prompt": "hi"})
    assert "/v1/chat/completions" not in backends.calls
    status = json.loads((await call(mcp, "gpu_status", {}))[0].text)
    assert status["holder"]["tool"] == "generate_image"
    held.release()


async def test_crashed_holder_is_not_reported(tmp_path, backends):
    mcp = make(tmp_path, backends)
    (tmp_path / "lock" / "gpu.holder.json").write_text('{"pid": 1, "tool": "local_code", "since": 0}')
    status = json.loads((await call(mcp, "gpu_status", {}))[0].text)
    assert status["holder"] is None


async def test_wait_queues_until_gpu_is_free(tmp_path, backends):
    mcp = make(tmp_path, backends)
    held = other_session_holds_gpu(tmp_path)
    job = asyncio.create_task(call(mcp, "local_chat", {"prompt": "hi", "if_busy": "wait"}))
    await asyncio.sleep(0.2)
    assert not job.done()
    held.release()
    blocks = await asyncio.wait_for(job, 5)
    assert blocks[0].text == "def f(): ..."


async def test_wait_gives_up_after_queue_timeout(tmp_path, backends):
    mcp = make(tmp_path, backends, queue_timeout=0.1)
    held = other_session_holds_gpu(tmp_path)
    with pytest.raises(Exception, match="Gave up after queueing"):
        await call(mcp, "local_code", {"task": "t", "if_busy": "wait"})
    held.release()


async def test_mode_is_shared_between_sessions(tmp_path, backends):
    first, second = make(tmp_path, backends), make(tmp_path, backends)
    await call(first, "generate_image", {"prompt": "a cat"})
    await call(second, "local_chat", {"prompt": "hi"})
    assert "/free" in backends.calls

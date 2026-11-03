import io
import os
import time
from pathlib import Path

import httpx
from mcp.server.fastmcp import Context, FastMCP, Image
from PIL import Image as PILImage

from . import workflow
from .arbiter import MODES, GpuArbiter
from .backends import ComfyClient, LlamaSwap
from .config import Settings
from .usage import Usage

CODE_SYSTEM = (
    "You are a precise coding assistant. Complete the task using only the context "
    "given. Reply with code or a unified diff, plus at most a few lines of explanation."
)
THUMB_PX = 512


def create_server(settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None) -> FastMCP:
    """Build the MCP server. `transport` lets tests stand in for both backends."""
    llm = LlamaSwap(httpx.AsyncClient(
        base_url=settings.llama_swap_url, timeout=settings.llm_timeout, transport=transport))
    comfy = ComfyClient(
        httpx.AsyncClient(
            base_url=settings.comfy_url, timeout=settings.image_timeout, transport=transport),
        settings.image_timeout,
    )
    gpu = GpuArbiter(llm, comfy, settings.coexist)
    mcp = FastMCP("local-gpu")

    async def ask(tool: str, mode: str, model: str, prompt: str, system: str | None,
                  max_tokens: int, temperature: float) -> list:
        messages = ([{"role": "system", "content": system}] if system else []) + [
            {"role": "user", "content": prompt}]
        start = time.monotonic()
        async with gpu.use(mode) as switched:
            reply = await llm.chat(model, messages, max_tokens=max_tokens, temperature=temperature)
        usage = Usage(
            tool=tool, model=reply.model, mode=mode, seconds=round(time.monotonic() - start, 2),
            prompt_tokens=reply.prompt_tokens, completion_tokens=reply.completion_tokens,
            mode_switched=switched, estimated=reply.estimated)
        return [reply.text, usage.block()]

    @mcp.tool()
    async def local_chat(prompt: str, system: str | None = None,
                         max_tokens: int = 1024, temperature: float = 0.7) -> list:
        """Ask the local chat model. Good for drafts, summaries and bulk text that
        doesn't need Claude. May swap models, which takes a few seconds."""
        return await ask("local_chat", "chat", settings.chat_model, prompt, system,
                         max_tokens, temperature)

    @mcp.tool()
    async def local_code(task: str, context: str = "",
                         max_tokens: int = 2048, temperature: float = 0.2) -> list:
        """Delegate a self-contained coding task to the local code model. Include
        every file or snippet it needs in `context`: it cannot read your repo."""
        prompt = f"{task}\n\n--- context ---\n{context}" if context else task
        return await ask("local_code", "code", settings.code_model, prompt, CODE_SYSTEM,
                         max_tokens, temperature)

    @mcp.tool()
    async def generate_image(
        prompt: str, negative: str = "", width: int = 1024, height: int = 1024,
        steps: int = 25, cfg: float = 7.0, seed: int = -1,
        inline_preview: bool = True, ctx: Context | None = None,
    ) -> list:
        """Generate an image with ComfyUI. Frees the LLM's VRAM first. The full image
        is saved on the desktop; the inline preview is a small thumbnail."""
        seed = seed if seed >= 0 else int.from_bytes(os.urandom(4), "big")
        wf = workflow.render(settings.comfy_workflow, {
            "prompt": prompt, "negative": negative, "width": width, "height": height,
            "steps": steps, "cfg": cfg, "seed": seed, "checkpoint": settings.comfy_checkpoint})

        async def progress(fraction: float) -> None:
            if ctx:
                await ctx.report_progress(fraction, 1.0)

        start = time.monotonic()
        async with gpu.use("image") as switched:
            png = await comfy.generate(wf, progress)
        seconds = round(time.monotonic() - start, 2)

        settings.output_dir.mkdir(parents=True, exist_ok=True)
        path = settings.output_dir / f"{int(time.time())}-{seed}.png"
        path.write_bytes(png)
        usage = Usage(tool="generate_image", model=Path(settings.comfy_checkpoint).stem,
                      mode="image", seconds=seconds, mode_switched=switched,
                      width=width, height=height, steps=steps)
        out: list = [f"Saved {path.resolve()} (seed {seed})"]
        if inline_preview:
            out.append(_thumbnail(png))
        return [*out, usage.block()]

    @mcp.tool()
    async def gpu_status() -> dict:
        """Report the current GPU mode and which LLM profiles llama-swap has loaded."""
        return {"mode": gpu.mode, "loaded_models": await llm.running()}

    @mcp.tool()
    async def list_models() -> dict:
        """List the model profiles configured in llama-swap and the mode defaults."""
        return {"models": await llm.models(),
                "code_model": settings.code_model, "chat_model": settings.chat_model}

    @mcp.tool()
    async def set_mode(mode: str) -> dict:
        """Switch the GPU to 'code', 'chat', 'image' or 'idle', freeing VRAM as needed.
        Tools switch automatically, so use this only to pre-warm or release the GPU."""
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        async with gpu.use(mode) as switched:
            if mode in ("code", "chat"):
                model = settings.code_model if mode == "code" else settings.chat_model
                await llm.chat(model, [{"role": "user", "content": "hi"}],
                               max_tokens=1, temperature=0.0)
        return {"mode": mode, "switched": switched}

    return mcp


def _thumbnail(png: bytes) -> Image:
    img = PILImage.open(io.BytesIO(png)).convert("RGB")
    img.thumbnail((THUMB_PX, THUMB_PX))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=80)
    return Image(data=buf.getvalue(), format="jpeg")

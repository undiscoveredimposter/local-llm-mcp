import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from .backends import ComfyClient, LlamaSwap

MODES = ("idle", "code", "chat", "image")


class GpuArbiter:
    """Serialises GPU work and frees VRAM when the mode crosses LLM <-> image.

    code <-> chat needs no action here: llama-swap swaps on the model name.
    """

    def __init__(self, llm: LlamaSwap, comfy: ComfyClient, coexist: bool = False):
        self._llm = llm
        self._comfy = comfy
        self._coexist = coexist
        self._lock = asyncio.Lock()
        self.mode = "idle"

    @asynccontextmanager
    async def use(self, mode: str) -> AsyncIterator[bool]:
        """Hold the GPU in `mode`; yields whether the mode changed."""
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        async with self._lock:
            yield await self._switch(mode)

    async def _switch(self, mode: str) -> bool:
        if mode == self.mode:
            return False
        if not self._coexist:
            if mode in ("image", "idle") and self.mode in ("code", "chat"):
                await self._llm.unload()
            if mode in ("code", "chat", "idle") and self.mode == "image":
                await self._comfy.free()
        self.mode = mode
        return True

import asyncio
import json
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from .backends import ComfyClient, LlamaSwap

if os.name == "nt":
    import msvcrt

    def _lock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

    def _unlock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _lock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


MODES = ("idle", "code", "chat", "image")
IF_BUSY = ("report", "wait")


class GpuBusy(RuntimeError):
    pass


@dataclass(frozen=True)
class Slot:
    switched: bool
    wait_seconds: float


class GpuLock:
    """OS file lock shared by every local-gpu-mcp process on the machine.

    Each Claude Code session runs its own stdio server, so the lock, the current
    holder and the GPU mode live in files rather than in memory. The OS drops the
    lock if a holder dies, so a crashed session never leaves the GPU stuck.
    """

    def __init__(self, lock_dir: Path):
        lock_dir.mkdir(parents=True, exist_ok=True)
        self._lock_path = lock_dir / "gpu.lock"
        self._holder_path = lock_dir / "gpu.holder.json"
        self._mode_path = lock_dir / "gpu.mode"
        self._fd: int | None = None

    def try_acquire(self, tool: str) -> bool:
        fd = os.open(self._lock_path, os.O_RDWR | os.O_CREAT)
        try:
            _lock(fd)
        except OSError:
            os.close(fd)
            return False
        self._fd = fd
        self._holder_path.write_text(json.dumps({"pid": os.getpid(), "tool": tool, "since": time.time()}))
        return True

    def release(self) -> None:
        self._holder_path.unlink(missing_ok=True)
        _unlock(self._fd)
        os.close(self._fd)
        self._fd = None

    def holder(self) -> dict | None:
        """Who holds the GPU, or None when it is free (a crashed holder leaves its file behind)."""
        if self._fd is None:
            fd = os.open(self._lock_path, os.O_RDWR | os.O_CREAT)
            try:
                _lock(fd)
            except OSError:
                pass
            else:
                _unlock(fd)
                return None
            finally:
                os.close(fd)
        try:
            return json.loads(self._holder_path.read_text())
        except (OSError, ValueError):
            return None

    @property
    def mode(self) -> str:
        try:
            mode = self._mode_path.read_text().strip()
        except OSError:
            return "idle"
        return mode if mode in MODES else "idle"

    @mode.setter
    def mode(self, mode: str) -> None:
        self._mode_path.write_text(mode)


class GpuArbiter:
    """Serialises GPU work across sessions and frees VRAM when the mode crosses LLM <-> image.

    code <-> chat needs no action here: llama-swap swaps on the model name.
    Calls from the same session queue silently; a call that finds another session
    holding the GPU raises GpuBusy unless it asked to wait.
    """

    def __init__(self, llm: LlamaSwap, comfy: ComfyClient, lock: GpuLock, *,
                 coexist: bool = False, queue_timeout: float = 600.0, poll_interval: float = 0.5):
        self._llm = llm
        self._comfy = comfy
        self._gpu = lock
        self._coexist = coexist
        self._queue_timeout = queue_timeout
        self._poll = poll_interval
        self._local = asyncio.Lock()

    @property
    def mode(self) -> str:
        return self._gpu.mode

    def holder(self) -> dict | None:
        return self._gpu.holder()

    @asynccontextmanager
    async def use(self, mode: str, tool: str, if_busy: str = "report") -> AsyncIterator[Slot]:
        """Hold the GPU in `mode`; yields whether the mode changed and how long the call queued."""
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        if if_busy not in IF_BUSY:
            raise ValueError(f"if_busy must be one of {IF_BUSY}")
        queued = time.monotonic()
        async with self._local:
            await self._acquire(tool, if_busy)
            try:
                wait_seconds = round(time.monotonic() - queued, 2)
                yield Slot(await self._switch(mode), wait_seconds)
            finally:
                self._gpu.release()

    async def _acquire(self, tool: str, if_busy: str) -> None:
        if self._gpu.try_acquire(tool):
            return
        if if_busy == "report":
            raise GpuBusy(f"{self._busy_text()} Call again with if_busy='wait' to queue "
                          "behind it, or drop this request.")
        deadline = time.monotonic() + self._queue_timeout
        while not self._gpu.try_acquire(tool):
            if time.monotonic() > deadline:
                raise GpuBusy(f"Gave up after queueing {self._queue_timeout:.0f}s. {self._busy_text()}")
            await asyncio.sleep(self._poll)

    def _busy_text(self) -> str:
        h = self.holder()
        if not h:
            return "GPU busy: another Claude Code session holds it."
        return (f"GPU busy: another Claude Code session (pid {h['pid']}) is running "
                f"{h['tool']}, started {time.time() - h['since']:.0f}s ago.")

    async def _switch(self, mode: str) -> bool:
        current = self._gpu.mode
        if mode == current:
            return False
        if not self._coexist:
            if mode in ("image", "idle") and current in ("code", "chat"):
                await self._llm.unload()
            if mode in ("code", "chat", "idle") and current == "image":
                await self._comfy.free()
        self._gpu.mode = mode
        return True

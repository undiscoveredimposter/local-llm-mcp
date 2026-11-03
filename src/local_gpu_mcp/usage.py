import json
from dataclasses import asdict, dataclass

USAGE_PREFIX = "local-gpu-usage: "


@dataclass
class Usage:
    """What one local tool call cost, reported to the caller as the final content block."""

    tool: str
    model: str
    mode: str
    seconds: float
    prompt_tokens: int = 0
    completion_tokens: int = 0
    mode_switched: bool = False
    estimated: bool = False
    width: int | None = None
    height: int | None = None
    steps: int | None = None
    backend: str = "local"

    def block(self) -> str:
        return USAGE_PREFIX + json.dumps(asdict(self), separators=(",", ":"))


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)

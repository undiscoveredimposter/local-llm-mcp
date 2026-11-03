import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_WORKFLOW = Path(__file__).parent / "workflows" / "txt2img.json"


@dataclass(frozen=True)
class Settings:
    llama_swap_url: str = "http://127.0.0.1:8080"
    chat_model: str = "chat"
    code_model: str = "code"
    comfy_url: str = "http://127.0.0.1:8188"
    comfy_workflow: Path = DEFAULT_WORKFLOW
    comfy_checkpoint: str = "sd_xl_base_1.0.safetensors"
    output_dir: Path = Path("outputs")
    coexist: bool = False
    llm_timeout: float = 300.0
    image_timeout: float = 300.0
    queue_timeout: float = 600.0
    lock_dir: Path = Path.home() / ".local-gpu-mcp"
    token: str | None = None

    @classmethod
    def from_env(cls) -> "Settings":
        e = os.environ.get
        d = cls()
        return cls(
            llama_swap_url=e("LGM_LLAMA_SWAP_URL", d.llama_swap_url),
            chat_model=e("LGM_CHAT_MODEL", d.chat_model),
            code_model=e("LGM_CODE_MODEL", d.code_model),
            comfy_url=e("LGM_COMFY_URL", d.comfy_url),
            comfy_workflow=Path(e("LGM_COMFY_WORKFLOW", str(d.comfy_workflow))),
            comfy_checkpoint=e("LGM_COMFY_CHECKPOINT", d.comfy_checkpoint),
            output_dir=Path(e("LGM_OUTPUT_DIR", str(d.output_dir))),
            coexist=e("LGM_COEXIST", "0") == "1",
            llm_timeout=float(e("LGM_LLM_TIMEOUT", d.llm_timeout)),
            image_timeout=float(e("LGM_IMAGE_TIMEOUT", d.image_timeout)),
            queue_timeout=float(e("LGM_QUEUE_TIMEOUT", d.queue_timeout)),
            lock_dir=Path(e("LGM_LOCK_DIR", str(d.lock_dir))),
            token=e("LGM_TOKEN") or None,
        )

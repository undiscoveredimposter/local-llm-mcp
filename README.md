# local-gpu-mcp

An MCP server that lets Claude Code use the GPU on your desktop: local chat and code
models (via [llama-swap](https://github.com/mostlygeek/llama-swap)) and image generation
(via ComfyUI), with explicit GPU mode handling.

## Tools

| Tool | Purpose |
|---|---|
| `local_chat` | Ask the local chat model |
| `local_code` | Delegate a self-contained coding task (pass files in `context`; it can't read your repo) |
| `generate_image` | ComfyUI txt2img; full image saved on the desktop, small JPEG preview returned inline |
| `gpu_status` / `list_models` | Inspect the current mode and available profiles |
| `set_mode` | Pre-warm `code`/`chat`, or free the GPU with `idle` |

Tools switch mode automatically. Crossing LLM <-> image unloads the other side
(`llama-swap /unload`, ComfyUI `/free`) unless `LGM_COEXIST=1`. GPU jobs are serialised.

### Usage metadata

Every LLM and image result ends with one text block, `local-gpu-usage: {json}`:

```json
{"tool":"local_code","model":"qwen-coder","mode":"code","seconds":4.1,"prompt_tokens":40,
 "completion_tokens":12,"mode_switched":true,"estimated":false,"backend":"local"}
```

Image calls add `width`, `height`, `steps`. `estimated` is true when llama-swap returned
no token counts (characters / 4 is used instead). The planned Claude mod reads this block.

## Run

```bash
uv sync
uv run pytest
uv run local-gpu-mcp                                   # stdio
LGM_TOKEN=... uv run local-gpu-mcp --transport http --host 0.0.0.0 --port 8765
```

HTTP on a non-loopback host refuses to start without `LGM_TOKEN`. Expose it only over
Tailscale (or behind your Coolify proxy), never the open internet.

Add to Claude Code:

```bash
claude mcp add --transport http local-gpu http://<desktop-tailscale-name>:8765/mcp \
  --header "Authorization: Bearer $LGM_TOKEN"
```

## Windows desktop service

Install the prerequisites, then register the server as a logon task:

```powershell
winget install astral-sh.uv Tailscale.Tailscale
uv sync
.\deploy\windows\install.ps1 -CodeModel <code-profile> -ChatModel <chat-profile>
```

`install.ps1` writes `%USERPROFILE%\.local-gpu-mcp.env` (the generated bearer token and
settings), registers a `local-gpu-mcp` scheduled task that waits for Tailscale and binds to its
address, and adds a firewall rule limited to the Tailscale range when run from an admin
shell. It prints the token at the end. Remove it with
`Unregister-ScheduledTask local-gpu-mcp -Confirm:$false`.

It runs as a logon task, not a Windows service, so it needs your account logged in. That also
matches llama-swap and ComfyUI, which usually run in your session. These scripts have not
been run on Windows yet.

## Configuration (environment)

| Variable | Default |
|---|---|
| `LGM_LLAMA_SWAP_URL` | `http://127.0.0.1:8080` |
| `LGM_CHAT_MODEL` / `LGM_CODE_MODEL` | `chat` / `code` (names in your llama-swap config) |
| `LGM_COMFY_URL` | `http://127.0.0.1:8188` |
| `LGM_COMFY_WORKFLOW` | bundled `workflows/txt2img.json` |
| `LGM_COMFY_CHECKPOINT` | `sd_xl_base_1.0.safetensors` |
| `LGM_OUTPUT_DIR` | `outputs` |
| `LGM_COEXIST` | `0` (set `1` if VRAM fits both) |
| `LGM_LLM_TIMEOUT` / `LGM_IMAGE_TIMEOUT` | `300` seconds |
| `LGM_TOKEN` | unset |

### Custom image workflows

Export a workflow from ComfyUI with *Save (API Format)* and put `{{prompt}}`, `{{negative}}`,
`{{seed}}`, `{{width}}`, `{{height}}`, `{{steps}}`, `{{cfg}}` and `{{checkpoint}}` where the
values belong. A field that is exactly one placeholder keeps its type (ints stay ints).

## Not yet verified

Tested against mocked llama-swap and ComfyUI only. Run it against your real desktop and
check the `/running` and `/unload` endpoints match your llama-swap version.

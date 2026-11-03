# local-gpu (Claude Code mod)

A Claude Code mod that goes with the `local-gpu-mcp` server in this repo. It adds a per-session
switch for the server's tools, and a pane that splits the work between Claude and your local
models and estimates what that saved.

- **`/local on` / `/local off`**: the six local GPU tools are refused until you turn them on for
  the session. `/local stats` prints the session's savings, and `/local pane` opens the pane.
  While on, a short system-prompt section tells Claude when to delegate to the local models.
- **Pane**: generated tokens split Claude versus local, calls per tool, GPU seconds, estimated
  savings in dollars and as a rough share of the 5-hour rate-limit window, and the average saved
  per session. Savings are estimates (see the formula in `hooks/register.tsx`).
- It reads the `local-gpu-usage: {json}` block that the server appends to each result, plus
  Claude's own turn usage. It sends nothing anywhere.

## Install

The mod ships the MCP server in its own `.mcp.json`, so you do not also run `claude mcp add`.

1. Copy this folder to `~/.claude/skills/local-gpu` (on Windows,
   `%USERPROFILE%\.claude\skills\local-gpu`). Alternatives: start Claude Code with
   `--plugin-dir <path to this folder>`, or list the folder in `CLAUDE_CODE_PLUGIN_DIRS`
   (separate several with `:`, or `;` on Windows). A session the desktop app starts reads
   `CLAUDE_CODE_PLUGIN_DIRS` from the `env` block of `~/.claude/settings.json`.
2. Start a new Claude Code session. Edits to the mod reload by themselves in an interactive
   session after that.
3. Set the options below in the plugin's config menu. A change reloads the mod.

Requires a Claude Code build with mod support (function hooks); the mod was built against
2.1.309, and that API is early access and may change.

## Options (`userConfig`)

| Key | Meaning | Default |
|---|---|---|
| `url` | Streamable HTTP endpoint of the server, e.g. `http://<desktop-tailscale-name>:8765/mcp` | `http://desktop:8765/mcp` (a placeholder; change it) |
| `token` | The `LGM_TOKEN` the server was started with (stored as a secret) | none |
| `defaultOn` | Whether the local tools are usable from session start | `false` |
| `inputPricePerMTok` | $ per million input tokens of the Claude model you would otherwise use | `3` |
| `outputPricePerMTok` | $ per million output tokens of that model | `15` |

If the server shows as failed in `/mcp` with `ENOTFOUND`, the `url` host does not resolve from
this machine.

## Develop

```bash
claude plugin validate mod/local-gpu
claude plugin test mod/local-gpu
```

# Starts local-gpu-mcp bound to the Tailscale IPv4 address. Settings come from
# %USERPROFILE%\.local-gpu-mcp.env (KEY=VALUE lines, written by install.ps1).
$ErrorActionPreference = 'Stop'
$repo = Resolve-Path (Join-Path $PSScriptRoot '..\..')
$envFile = Join-Path $env:USERPROFILE '.local-gpu-mcp.env'

Get-Content $envFile | ForEach-Object {
    if ($_ -match '^\s*([A-Z_]+)\s*=\s*(.*?)\s*$') {
        [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2], 'Process')
    }
}
$port = if ($env:LGM_PORT) { $env:LGM_PORT } else { '8765' }

# Tailscale may not be up yet at logon: wait up to two minutes for its address.
$ip = $null
for ($i = 0; $i -lt 24 -and -not $ip; $i++) {
    $ip = (& tailscale ip -4 2>$null | Select-Object -First 1)
    if (-not $ip) { Start-Sleep -Seconds 5 }
}
if (-not $ip) { throw 'Tailscale has no IPv4 address; is it running and signed in?' }

Set-Location $repo
& uv run local-gpu-mcp --transport http --host $ip --port $port

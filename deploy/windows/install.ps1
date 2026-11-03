# Registers a per-user scheduled task that runs local-gpu-mcp at logon and restarts it
# on failure. Run from PowerShell in the repo; admin is only needed for the firewall rule.
param(
    [string]$CodeModel = 'code',
    [string]$ChatModel = 'chat',
    [int]$Port = 8765
)
$ErrorActionPreference = 'Stop'
$envFile = Join-Path $env:USERPROFILE '.local-gpu-mcp.env'

if (-not (Test-Path $envFile)) {
    $bytes = New-Object byte[] 32
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $token = ($bytes | ForEach-Object { $_.ToString('x2') }) -join ''
    @("LGM_TOKEN=$token", "LGM_CODE_MODEL=$CodeModel", "LGM_CHAT_MODEL=$ChatModel", "LGM_PORT=$Port") |
        Set-Content $envFile
    icacls $envFile /inheritance:r /grant:r "$($env:USERNAME):(R,W)" | Out-Null
    Write-Host "Created $envFile. It holds your bearer token and the model names; edit it to set other LGM_* values."
} else {
    $line = Get-Content $envFile | Where-Object { $_ -match '^LGM_PORT=(\d+)' } | Select-Object -First 1
    if ($line -match '(\d+)') { $Port = [int]$Matches[1] }
    Write-Host "Keeping existing $envFile"
}

$run = Join-Path $PSScriptRoot 'run.ps1'
$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$run`""
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName 'local-gpu-mcp' -Action $action -Trigger $trigger `
    -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName 'local-gpu-mcp'
Write-Host 'Task "local-gpu-mcp" registered and started.'

$rule = @{
    DisplayName = 'local-gpu-mcp (Tailscale)'; Direction = 'Inbound'; Action = 'Allow'
    Protocol = 'TCP'; LocalPort = $Port; RemoteAddress = '100.64.0.0/10'
}
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).
    IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if ($isAdmin) {
    Remove-NetFirewallRule -DisplayName $rule.DisplayName -ErrorAction SilentlyContinue
    New-NetFirewallRule @rule | Out-Null
    Write-Host "Firewall: allowed TCP $Port from the Tailscale range only."
} else {
    Write-Host "Not elevated, so no firewall rule was added. In an admin PowerShell run:"
    Write-Host "  New-NetFirewallRule -DisplayName '$($rule.DisplayName)' -Direction Inbound -Action Allow -Protocol TCP -LocalPort $Port -RemoteAddress 100.64.0.0/10"
}
Write-Host "Token: $((Get-Content $envFile | Where-Object { $_ -like 'LGM_TOKEN=*' }) -replace 'LGM_TOKEN=','')"

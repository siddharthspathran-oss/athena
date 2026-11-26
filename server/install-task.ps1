# Start Athena's server mode with Windows, with nobody signed in, on athena-server.
# Run once there as administrator (after setup-mini-pc.ps1 and server/send_keys.py).
$ErrorActionPreference = 'Stop'
$Name = 'Athena Server'
$Tailnet = '100.64.0.0/10', 'fd7a:115c:a1e0::/48'

$action = New-ScheduledTaskAction -Execute 'C:\Program Files\Python313\pythonw.exe' `
    -Argument 'run.py server' -WorkingDirectory 'C:\athena'
$trigger = New-ScheduledTaskTrigger -AtStartup
# S4U: runs with nobody signed in and no password stored. Its catch: no Credential
# Manager, which is why the keys live in data\api_keys.json (receive_keys.py).
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType S4U -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $Name -Action $action -Trigger $trigger -Principal $principal `
    -Settings $settings -Force | Out-Null

# The laptop asks for reminders on 8421: only your Tailscale devices may.
if (-not (Get-NetFirewallRule -Name Athena-Server-In -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -Name Athena-Server-In -DisplayName 'Athena server (reminders)' -Direction Inbound `
        -Protocol TCP -LocalPort 8421 -Action Allow -Profile Any -RemoteAddress $Tailnet | Out-Null
}

Start-ScheduledTask -TaskName $Name
"task: " + (Get-ScheduledTask -TaskName $Name).State

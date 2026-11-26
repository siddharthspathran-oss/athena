# Athena mini PC setup. Run once, in PowerShell opened with "Run as administrator":
#   powershell -ExecutionPolicy Bypass -File "$HOME\Downloads\setup-mini-pc.ps1"
#
# What it does:
#   - never sleeps or hibernates while plugged in
#   - turns on Windows' own SSH server, reachable only from your Tailscale devices
#   - only the laptop's key can log in (passwords are switched off for SSH)
#   - names the machine athena-server (Windows and Tailscale)
#   - keeps Tailscale connected even when nobody is signed in to Windows
$ErrorActionPreference = 'Stop'
$LaptopKey = 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKts3mS4saXWK57AQg9Tv4t/BjmZJKjckdME2gXGj1S+ athena-laptop'
$Tailnet = '100.64.0.0/10', 'fd7a:115c:a1e0::/48'
$Name = 'athena-server'

$me = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $me.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Open PowerShell with "Run as administrator" and run this again.'
}

Write-Host '1/6 Power: never sleep, no hibernate'
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
powercfg /hibernate off

Write-Host '2/6 Installing the SSH server (can take a few minutes)'
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0 | Out-Null
Start-Service sshd
Set-Service sshd -StartupType Automatic
if (-not (Test-Path HKLM:\SOFTWARE\OpenSSH)) { New-Item HKLM:\SOFTWARE\OpenSSH | Out-Null }
New-ItemProperty -Path HKLM:\SOFTWARE\OpenSSH -Name DefaultShell -PropertyType String -Force `
    -Value "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" | Out-Null

Write-Host '3/6 Firewall: SSH only from your Tailscale devices'
if (-not (Get-NetFirewallRule -Name OpenSSH-Server-In-TCP -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -Name OpenSSH-Server-In-TCP -DisplayName 'OpenSSH Server (sshd)' `
        -Direction Inbound -Protocol TCP -LocalPort 22 -Action Allow | Out-Null
}
Set-NetFirewallRule -Name OpenSSH-Server-In-TCP -Enabled True -Profile Any -RemoteAddress $Tailnet

Write-Host '4/6 Login: laptop key only, passwords off'
$Auth = "$env:ProgramData\ssh\administrators_authorized_keys"
Set-Content -Path $Auth -Value $LaptopKey -Encoding Ascii
icacls $Auth /inheritance:r /grant '*S-1-5-32-544:F' /grant '*S-1-5-18:F' | Out-Null
if ($LASTEXITCODE) { throw 'Could not lock down the key file (icacls failed).' }
$Conf = "$env:ProgramData\ssh\sshd_config"
$Lines = Get-Content $Conf | Where-Object { $_ -notmatch '^\s*#?\s*(PasswordAuthentication|KbdInteractiveAuthentication)\s' }
Set-Content -Path $Conf -Value (@('PasswordAuthentication no', 'KbdInteractiveAuthentication no') + $Lines) -Encoding Ascii
Restart-Service sshd

Write-Host "5/6 Tailscale: stay connected, name $Name"
$Ts = "$env:ProgramFiles\Tailscale\tailscale.exe"
if (Test-Path $Ts) {
    & $Ts set --hostname=$Name
    & $Ts set --unattended
    if ($LASTEXITCODE) { Write-Host '   Tick "Run unattended" in the Tailscale tray menu yourself.' -ForegroundColor Yellow }
} else {
    Write-Host '   Tailscale is not installed yet - install it, then run this again.' -ForegroundColor Yellow
}

Write-Host "6/6 Windows name: $Name"
if ($env:COMPUTERNAME -ne $Name.ToUpper()) { Rename-Computer -NewName $Name -Force }

Write-Host ''
Write-Host "Done. Tell Claude this username: $env:USERNAME" -ForegroundColor Green
Write-Host 'Then restart the PC once so the new name takes effect.' -ForegroundColor Green

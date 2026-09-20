# Run on the Windows Hyper-V host. Exposes only guest SSH on the host's Tailscale IPv4.
param(
    [string]$ListenAddress = '100.64.186.105',
    [int]$ListenPort = 2222,
    [string]$GuestAddress = '172.27.65.219'
)
$ErrorActionPreference = 'Stop'
foreach ($address in @($ListenAddress, $GuestAddress)) {
    $parsed = [Net.IPAddress]::Parse($address)
    if ($parsed.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetwork) {
        throw 'Only verified IPv4 addresses are supported.'
    }
}
if ($ListenPort -lt 1024 -or $ListenPort -gt 65535) { throw 'Choose a non-system TCP port.' }
if (-not (Get-NetIPAddress -IPAddress $ListenAddress -ErrorAction SilentlyContinue)) {
    throw 'The listening address is not assigned to this host.'
}
$registryPath = 'HKLM:\SYSTEM\CurrentControlSet\Services\PortProxy\v4tov4\tcp'
$property = "$ListenAddress/$ListenPort"
$existing = (Get-ItemProperty -LiteralPath $registryPath -Name $property -ErrorAction SilentlyContinue).$property
if ($existing -and $existing -ne "$GuestAddress/22") {
    throw 'This forwarding port already targets a different address; inspect it before changing.'
}
if (-not $existing -and (Get-NetTCPConnection -LocalAddress $ListenAddress -LocalPort $ListenPort -State Listen -ErrorAction SilentlyContinue)) {
    throw 'This listening port is already in use.'
}
Start-Service iphlpsvc
& netsh interface portproxy add v4tov4 "listenaddress=$ListenAddress" "listenport=$ListenPort" "connectaddress=$GuestAddress" connectport=22
if ($LASTEXITCODE -ne 0) { throw 'Unable to configure guest SSH forwarding.' }
$ruleName = 'KnowledgeManager-Ubuntu-SSH'
if (-not (Get-NetFirewallRule -Name $ruleName -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -Name $ruleName -DisplayName 'Knowledge Manager Ubuntu SSH over Tailscale' -Direction Inbound -Action Allow -Protocol TCP -LocalAddress $ListenAddress -LocalPort $ListenPort -RemoteAddress '100.64.0.0/10' -Profile Any | Out-Null
}
Write-Output "Guest SSH forwarding configured: ${ListenAddress}:$ListenPort -> ${GuestAddress}:22"

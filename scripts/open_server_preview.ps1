param(
    [switch]$Open,
    [string]$HostAddress = '100.64.186.105',
    [string]$HostUser = 'Administrator',
    [string]$GuestUser = 'knowledgeadmin',
    [int]$LocalPort = 18787,
    [string]$KnownHostsPath = '',
    [string]$HostKeyAlias = '100.64.186.105',
    [string]$GuestHostKeyAlias = '172.27.70.12',
    [string]$KeyPath = '',
    [string]$SshPath = 'ssh.exe'
)

$ErrorActionPreference = 'Stop'
$guestMac = '00155D031703'

function Get-RequiredFile([string]$Path, [string]$Description) {
    if ([string]::IsNullOrWhiteSpace($Path)) {
        throw "$Description path is required."
    }
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    if ($item.PSIsContainer) { throw "$Description must be a file: $Path" }
    return $item.FullName
}

function ConvertFrom-GuestDiscovery([string[]]$Lines) {
    $text = ($Lines -join "`n").Trim()
    if (!$text) { throw 'The host returned no guest address.' }
    try {
        $decoded = ConvertFrom-Json -InputObject $text -ErrorAction Stop
    } catch {
        throw "The host returned invalid guest discovery data: $text"
    }
    $addresses = @(
        @($decoded) | Where-Object { $_ -is [string] -and $_ } | Select-Object -Unique
    )
    if ($addresses.Count -ne 1) {
        throw "Expected exactly one guest IPv4 for MAC $guestMac; found $($addresses.Count)."
    }
    $parsed = $null
    if (-not [Net.IPAddress]::TryParse($addresses[0], [ref]$parsed) -or
        $parsed.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetwork) {
        throw "Host discovery returned a non-IPv4 address: $($addresses[0])"
    }
    return $addresses[0]
}

function Quote-ProxyToken([string]$Value) {
    if ($Value -notmatch '[\s"]') { return $Value }
    return '"' + $Value.Replace('"', '\"') + '"'
}

function Assert-SafeProxyToken([string]$Value, [string]$Description) {
    if ($Value -match '[&|<>^()%!\r\n]') {
        throw "$Description is unsafe for the nested SSH proxy command."
    }
}

function Format-SshPathOption([string]$Name, [string]$Path) {
    # OpenSSH parses -o values as configuration syntax even when PowerShell
    # supplied the value as one argv element. Windows PowerShell 5.1 removes
    # embedded quotes before native invocation, so use config-style escaped
    # spaces and forward slashes instead.
    if ($Path -match '["\r\n\t]') { throw "$Name path contains unsupported characters." }
    $escaped = $Path.Replace('\', '/').Replace(' ', '\ ')
    return '{0}={1}' -f $Name, $escaped
}

function Resolve-SshApplication([string]$CommandName) {
    $matches = @(Get-Command $CommandName -CommandType Application -ErrorAction Stop)
    if ($matches.Count -eq 0) { throw "SSH application not found: $CommandName" }
    return [string]$matches[0].Source
}

if ($LocalPort -lt 1024 -or $LocalPort -gt 65535) {
    throw 'LocalPort must be between 1024 and 65535.'
}
if (!$KeyPath) {
    $KeyPath = Join-Path $env:USERPROFILE '.ssh\knowledge_manager_ed25519'
}

if (!$Open) {
    Write-Host "Plan: open http://127.0.0.1:$LocalPort through the existing authenticated host and guest SSH route."
    Write-Host 'No connection was started. Supply -Open and -KnownHostsPath <file> to start the foreground tunnel.'
    Write-Host 'Close that PowerShell window, or press Ctrl+C, to stop the tunnel.'
    return
}

$key = Get-RequiredFile $KeyPath 'SSH private key'
$knownHostsSource = Get-RequiredFile $KnownHostsPath 'SSH known-hosts'
$ssh = Resolve-SshApplication $SshPath
$cacheDirectory = Join-Path $env:LOCALAPPDATA 'KnowledgeManagerPreview'
$knownHosts = Join-Path $cacheDirectory 'known_hosts'
if ($knownHosts -match '\s') {
    throw 'The user-local cache path contains whitespace; choose a Windows account with a plain profile path.'
}
[void][IO.Directory]::CreateDirectory($cacheDirectory)
[IO.File]::Copy($knownHostsSource, $knownHosts, $true)
$knownHostsOption = Format-SshPathOption 'UserKnownHostsFile' $knownHosts

# Bind and release once so an occupied local port fails before any remote connection.
$listener = $null
try {
    $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, $LocalPort)
    $listener.Start()
} catch {
    throw "Local preview port 127.0.0.1:$LocalPort is already in use. Choose another -LocalPort."
} finally {
    if ($listener) { $listener.Stop() }
}

$remoteDiscovery = @'
$ErrorActionPreference = 'Stop'
$mac = '00155D031703'
$addresses = @(
    Get-NetNeighbor -AddressFamily IPv4 -ErrorAction Stop |
        Where-Object { ($_.LinkLayerAddress -replace '[-:]', '').ToUpperInvariant() -eq $mac } |
        ForEach-Object { $_.IPAddress } |
        Sort-Object -Unique
)
ConvertTo-Json -InputObject $addresses -Compress
'@
$encodedDiscovery = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($remoteDiscovery))
$common = @(
    '-i', $key,
    '-o', 'IdentitiesOnly=yes',
    '-o', 'BatchMode=yes',
    '-o', 'StrictHostKeyChecking=yes',
    '-o', $knownHostsOption,
    '-o', 'ConnectTimeout=10',
    '-o', 'ServerAliveInterval=15',
    '-o', 'ServerAliveCountMax=3'
)
$discoveryArguments = $common + @(
    '-o', "HostKeyAlias=$HostKeyAlias",
    "$HostUser@$HostAddress",
    "powershell.exe -NoProfile -NonInteractive -EncodedCommand $encodedDiscovery"
)
$discoveryOutput = @(& $ssh @discoveryArguments)
if ($LASTEXITCODE -ne 0) { throw 'Read-only guest address discovery over SSH failed.' }
$guestAddress = ConvertFrom-GuestDiscovery $discoveryOutput

$proxyParts = @($ssh) + $common + @(
    '-o', "HostKeyAlias=$HostKeyAlias",
    '-W', '%h:%p',
    "$HostUser@$HostAddress"
)
foreach ($part in $proxyParts) {
    if ($part -ne '%h:%p') { Assert-SafeProxyToken $part 'SSH proxy argument' }
}
$proxyCommand = ($proxyParts | ForEach-Object { Quote-ProxyToken $_ }) -join ' '
$forward = '127.0.0.1:{0}:127.0.0.1:8787' -f $LocalPort
$tunnelArguments = $common + @(
    '-o', "HostKeyAlias=$GuestHostKeyAlias",
    '-o', 'ExitOnForwardFailure=yes',
    '-o', "ProxyCommand=$proxyCommand",
    '-N',
    '-L', $forward,
    "$GuestUser@$guestAddress"
)

Write-Host "Opening http://127.0.0.1:$LocalPort in a foreground SSH tunnel."
Write-Host "Guest address discovered from host neighbor data: $guestAddress"
Write-Host 'Press Ctrl+C or close this PowerShell window to stop.'
& $ssh @tunnelArguments
if ($LASTEXITCODE -ne 0) { throw "SSH tunnel exited with code $LASTEXITCODE." }

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$ConfigPath,
    [string]$SshPath = 'ssh.exe',
    [string[]]$SshPrefixArgument = @(),
    [string]$GuestAddress
)

$ErrorActionPreference = 'Stop'
$ArchivePattern = '^knowledge-\d{8}T\d{6}-[0-9a-f]{8}\.tar\.gz$'

function Quote-ProcessArgument([string]$Value) {
    if ($Value -notmatch '[\s"]') { return $Value }
    return '"' + ($Value -replace '(\\*)"', '$1$1\"' -replace '(\\+)$', '$1$1') + '"'
}

function Write-Status([string]$Destination, [hashtable]$Values) {
    $Values.timestampUtc = [DateTime]::UtcNow.ToString('o')
    $temporary = Join-Path $Destination ('.status.' + [Guid]::NewGuid().ToString('N') + '.json')
    $finalStatus = Join-Path $Destination 'status.json'
    if (Test-Path -LiteralPath $finalStatus) { Assert-RegularPath $finalStatus $false | Out-Null }
    [IO.File]::WriteAllText($temporary, ($Values | ConvertTo-Json -Compress), (New-Object Text.UTF8Encoding($false)))
    Move-Item -LiteralPath $temporary -Destination $finalStatus -Force
}

function Assert-NoReparsePath([string]$Path) {
    $current = Get-Item -Force -LiteralPath $Path
    while ($current) {
        if (($current.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Reparse points are not allowed in managed paths: $($current.FullName)"
        }
        if ($current.PSIsContainer) { $current = $current.Parent } else { $current = $current.Directory }
    }
}

function Assert-RegularPath([string]$Path, [bool]$Directory) {
    $item = Get-Item -Force -LiteralPath $Path
    if ($Directory -ne $item.PSIsContainer) { throw "Unexpected path type: $Path" }
    Assert-NoReparsePath $item.FullName
    return $item
}

function Get-Sha256([string]$Path) {
    $stream = [IO.File]::OpenRead($Path)
    $algorithm = [Security.Cryptography.SHA256]::Create()
    try {
        return ([BitConverter]::ToString($algorithm.ComputeHash($stream)) -replace '-', '').ToLowerInvariant()
    } finally {
        $algorithm.Dispose()
        $stream.Dispose()
    }
}

function Resolve-GuestAddress($Config) {
    if ($GuestAddress) {
        $parsed = [Net.IPAddress]::Parse($GuestAddress)
        if ($parsed.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetwork) {
            throw 'GuestAddress must be IPv4.'
        }
        return $GuestAddress
    }
    $wanted = ([string]$Config.guestMac -replace '[:-]', '').ToUpperInvariant()
    if ($wanted -notmatch '^[0-9A-F]{12}$') { throw 'guestMac is invalid.' }
    $adapter = Get-VMNetworkAdapter -VMName ([string]$Config.vmName) |
        Where-Object { ($_.MacAddress -replace '[:-]', '').ToUpperInvariant() -eq $wanted }
    if (@($adapter).Count -ne 1 -or -not $adapter.Connected) { throw 'Configured Hyper-V adapter was not found and connected.' }
    $alias = 'vEthernet (' + $adapter.SwitchName + ')'
    $addresses = Get-NetNeighbor -InterfaceAlias $alias -AddressFamily IPv4 |
        Where-Object {
            (($_.LinkLayerAddress -replace '[:-]', '').ToUpperInvariant() -eq $wanted) -and
            ($_.State -in @('Reachable', 'Stale', 'Delay', 'Probe', 'Permanent'))
        } | Select-Object -ExpandProperty IPAddress -Unique
    if (@($addresses).Count -ne 1) { throw 'Unable to resolve exactly one guest IPv4 address from the configured MAC.' }
    return [string]$addresses
}

function Invoke-SshBytes([string[]]$RemoteCommand, [string]$OutputPath, [Int64]$MaximumBytes) {
    $arguments = @()
    $arguments += $SshPrefixArgument
    $arguments += @(
        '-o', 'BatchMode=yes',
        '-o', 'StrictHostKeyChecking=yes',
        '-o', ('UserKnownHostsFile=' + $script:Config.knownHostsPath),
        '-o', ('HostKeyAlias=' + $script:Config.hostKeyAlias),
        '-o', 'ConnectTimeout=20',
        '-o', 'ConnectionAttempts=1',
        '-T', '-i', [string]$script:Config.keyPath,
        ([string]$script:Config.guestUser + '@' + $script:ResolvedGuest)
    )
    $arguments += $RemoteCommand
    $start = New-Object Diagnostics.ProcessStartInfo
    $start.FileName = $SshPath
    $start.Arguments = (($arguments | ForEach-Object { Quote-ProcessArgument ([string]$_) }) -join ' ')
    $start.UseShellExecute = $false
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    $start.CreateNoWindow = $true
    $process = New-Object Diagnostics.Process
    $process.StartInfo = $start
    $started = $false
    $memory = $null
    if ($OutputPath) {
        $output = New-Object IO.FileStream($OutputPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
    } else {
        $memory = New-Object IO.MemoryStream
        $output = $memory
    }
    try {
        if (-not $process.Start()) { throw 'Unable to start SSH.' }
        $started = $true
        $stderrTask = $process.StandardError.ReadToEndAsync()
        $deadline = [DateTime]::UtcNow.AddSeconds([int]$script:Config.timeoutSeconds)
        $buffer = New-Object byte[] 1048576
        [Int64]$total = 0
        while ($true) {
            $remainingTime = $deadline - [DateTime]::UtcNow
            if ($remainingTime.TotalMilliseconds -le 0) {
                $process.Kill()
                $process.WaitForExit()
                throw 'SSH operation timed out.'
            }
            $remaining = [int][Math]::Min([Int32]::MaxValue, [Math]::Ceiling($remainingTime.TotalMilliseconds))
            $readTask = $process.StandardOutput.BaseStream.ReadAsync($buffer, 0, $buffer.Length)
            if (-not $readTask.Wait($remaining)) {
                $process.Kill()
                $process.WaitForExit()
                throw 'SSH operation timed out.'
            }
            $read = $readTask.Result
            if ($read -eq 0) { break }
            $total += $read
            if ($total -gt $MaximumBytes) {
                $process.Kill()
                $process.WaitForExit()
                throw 'SSH output exceeded the configured byte limit.'
            }
            $output.Write($buffer, 0, $read)
        }
        $remainingTime = $deadline - [DateTime]::UtcNow
        if ($remainingTime.TotalMilliseconds -le 0) {
            $process.Kill()
            $process.WaitForExit()
            throw 'SSH operation timed out.'
        }
        $remaining = [int][Math]::Min([Int32]::MaxValue, [Math]::Ceiling($remainingTime.TotalMilliseconds))
        if (-not $process.WaitForExit($remaining)) {
            $process.Kill()
            $process.WaitForExit()
            throw 'SSH operation timed out.'
        }
        $stderr = $stderrTask.GetAwaiter().GetResult()
        if ($process.ExitCode -ne 0) { throw "SSH exporter failed with exit $($process.ExitCode): $stderr" }
        if ($memory) {
            return $memory.ToArray()
        }
    } finally {
        $output.Dispose()
        if ($started -and -not $process.HasExited) { $process.Kill(); $process.WaitForExit() }
        $process.Dispose()
    }
}

$ConfigPath = (Resolve-Path -LiteralPath $ConfigPath).Path
$script:Config = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
foreach ($field in @('destination', 'vmName', 'guestMac', 'guestUser', 'keyPath', 'knownHostsPath', 'hostKeyAlias')) {
    if (-not ([string]$script:Config.$field)) { throw "Missing config field: $field" }
}
if ([string]$script:Config.guestUser -notmatch '^[a-z_][a-z0-9_-]{0,31}$') { throw 'guestUser is invalid.' }
if ([string]$script:Config.hostKeyAlias -notmatch '^[A-Za-z0-9._-]+$') { throw 'hostKeyAlias is invalid.' }
if (-not $script:Config.timeoutSeconds) { $script:Config | Add-Member timeoutSeconds 300 }
if (-not $script:Config.maxArchiveBytes) { $script:Config | Add-Member maxArchiveBytes 107374182400 }
if ($null -eq $script:Config.minimumFreeBytes) { $script:Config | Add-Member minimumFreeBytes 5368709120 }
if ([Int64]$script:Config.timeoutSeconds -lt 5 -or [Int64]$script:Config.timeoutSeconds -gt 3600) { throw 'timeoutSeconds must be between 5 and 3600.' }
if ([Int64]$script:Config.maxArchiveBytes -le 0 -or [Int64]$script:Config.maxArchiveBytes -gt 1099511627776) { throw 'maxArchiveBytes is invalid.' }
if ([Int64]$script:Config.minimumFreeBytes -lt 5368709120) { throw 'minimumFreeBytes must reserve at least 5 GiB.' }
$destination = (Assert-RegularPath ([string]$script:Config.destination) $true).FullName
if (-not (Test-Path -LiteralPath (Join-Path $destination '.knowledge-manager-backup-root') -PathType Leaf)) {
    throw 'Destination is not a managed Knowledge Manager backup directory.'
}
Assert-RegularPath ([string]$script:Config.keyPath) $false | Out-Null
Assert-RegularPath ([string]$script:Config.knownHostsPath) $false | Out-Null

$lockPath = Join-Path $destination '.copy.lock'
$lock = $null
$partial = $null
try {
    if (Test-Path -LiteralPath $lockPath) { Assert-RegularPath $lockPath $false | Out-Null }
    try {
        $lock = New-Object IO.FileStream($lockPath, [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
    } catch [IO.IOException] {
        throw 'Another backup copy is already running.'
    }
    $script:ResolvedGuest = Resolve-GuestAddress $script:Config
    $manifestBytes = Invoke-SshBytes @('latest') $null 65536
    $manifestText = (New-Object Text.UTF8Encoding($false, $true)).GetString($manifestBytes)
    $manifest = $manifestText | ConvertFrom-Json
    $name = [string]$manifest.name
    $sha256 = ([string]$manifest.sha256).ToLowerInvariant()
    if (-not (($manifest.size -is [int]) -or ($manifest.size -is [long]))) { throw 'Exporter size must be a JSON integer.' }
    $size = [Int64]$manifest.size
    if ($name -notmatch $ArchivePattern -or $sha256 -notmatch '^[0-9a-f]{64}$') { throw 'Exporter returned an invalid manifest.' }
    if ($size -le 0 -or $size -gt [Int64]$script:Config.maxArchiveBytes) { throw 'Exporter size is outside the configured limit.' }
    $final = Join-Path $destination $name
    if ([IO.Path]::GetFullPath($final).Substring(0, $destination.Length + 1) -ne ($destination.TrimEnd('\') + '\')) {
        throw 'Archive path escaped the destination.'
    }
    if (Test-Path -LiteralPath $final) {
        Assert-RegularPath $final $false | Out-Null
        $existing = Get-Item -Force -LiteralPath $final
        $existingHash = Get-Sha256 $final
        if ($existing.Length -ne $size -or $existingHash -ne $sha256) { throw 'Existing final archive does not match the exporter manifest.' }
        Write-Status $destination @{ result = 'reused'; archive = $name; size = $size; sha256 = $sha256; guestAddress = $script:ResolvedGuest }
        exit 0
    }
    $drive = New-Object IO.DriveInfo([IO.Path]::GetPathRoot($destination))
    if (($drive.AvailableFreeSpace - $size) -lt [Int64]$script:Config.minimumFreeBytes) {
        throw 'Insufficient free space after the required reserve.'
    }
    $partial = Join-Path $destination ('.' + $name + '.' + [Guid]::NewGuid().ToString('N') + '.partial')
    Invoke-SshBytes @('get', $name) $partial $size | Out-Null
    $received = Get-Item -Force -LiteralPath $partial
    if ($received.Length -ne $size) { throw 'Downloaded archive size does not match the manifest.' }
    $receivedHash = Get-Sha256 $partial
    if ($receivedHash -ne $sha256) { throw 'Downloaded archive hash does not match the manifest.' }
    Move-Item -LiteralPath $partial -Destination $final
    $partial = $null
    Write-Status $destination @{ result = 'copied'; archive = $name; size = $size; sha256 = $sha256; guestAddress = $script:ResolvedGuest }
} catch {
    if ($destination -and (Test-Path -LiteralPath $destination -PathType Container)) {
        Write-Status $destination @{ result = 'failed'; error = $_.Exception.Message }
    }
    throw
} finally {
    if ($partial -and (Test-Path -LiteralPath $partial)) { Remove-Item -LiteralPath $partial -Force }
    if ($lock) { $lock.Dispose() }
}

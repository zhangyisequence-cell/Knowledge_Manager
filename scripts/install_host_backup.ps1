[CmdletBinding()]
param(
    [switch]$Install,
    [string]$Destination = 'D:\KnowledgeManagerBackups',
    [string]$ConfigDirectory = 'C:\ProgramData\KnowledgeManagerBackup',
    [string]$VMName = 'Ubuntu',
    [string]$GuestMac = '00155D031703',
    [string]$GuestUser = 'knowledge-backup',
    [Parameter(Mandatory = $true)][string]$KeyPath,
    [Parameter(Mandatory = $true)][string]$KnownHostsPath,
    [string]$HostKeyAlias = 'knowledge-manager-ubuntu',
    [string]$TaskName = 'KnowledgeManager-SecondDiskBackup'
)

$ErrorActionPreference = 'Stop'
if (-not $Install) { throw 'No changes made. Re-run with -Install to create the host backup task.' }
$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run from an elevated administrator token.'
}
if ([IO.Path]::GetPathRoot([IO.Path]::GetFullPath($Destination)) -ne 'D:\') {
    throw 'The approved second physical disk destination must be on D:.'
}

function Assert-SourceFile([string]$Path) {
    $item = Get-Item -Force -LiteralPath $Path
    if ($item.PSIsContainer) {
        throw "Expected a regular source file: $Path"
    }
    Assert-NoReparsePath $item.FullName
    return $item.FullName
}

function Assert-PrivateKeyAcl([string]$Path) {
    $allowed = @('S-1-5-18', 'S-1-5-32-544')
    foreach ($target in @((Get-Item -Force -LiteralPath $Path), (Get-Item -Force -LiteralPath (Split-Path -Parent $Path)))) {
        $acl = Get-Acl -LiteralPath $target.FullName
        try {
            $ownerSid = (New-Object Security.Principal.NTAccount($acl.Owner)).Translate([Security.Principal.SecurityIdentifier]).Value
        } catch {
            throw "Unable to resolve ACL owner for private key path: $($target.FullName)"
        }
        if (($ownerSid -notin $allowed) -and ($ownerSid -notmatch '-500$')) {
            throw "Private key owner must be SYSTEM, Administrators, or built-in Administrator: $($target.FullName)"
        }
        $rules = $acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier])
        foreach ($rule in $rules) {
            if ($rule.IdentityReference.Value -notin $allowed) {
                throw "Private key and its directory may grant access only to SYSTEM and Administrators; stage a fresh key in a protected directory: $($target.FullName)"
            }
        }
    }
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

function Initialize-ManagedDirectory([string]$Path, [string]$MarkerName) {
    if (Test-Path -LiteralPath $Path) {
        $item = Get-Item -Force -LiteralPath $Path
        if (-not $item.PSIsContainer) {
            throw "Managed path is not a regular directory: $Path"
        }
        Assert-NoReparsePath $item.FullName
        if (-not (Test-Path -LiteralPath (Join-Path $Path $MarkerName) -PathType Leaf)) {
            throw "Existing directory is not marked as Knowledge Manager state: $Path"
        }
    } else {
        $parent = Split-Path -Parent $Path
        if (-not (Test-Path -LiteralPath $parent -PathType Container)) { throw "Managed parent directory does not exist: $parent" }
        Assert-NoReparsePath $parent
        New-Item -ItemType Directory -Path $Path | Out-Null
        [IO.File]::WriteAllText((Join-Path $Path $MarkerName), "managed`r`n", (New-Object Text.UTF8Encoding($false)))
    }
    return (Get-Item -Force -LiteralPath $Path).FullName
}

function Protect-ManagedPath([string]$Path) {
    $acl = New-Object Security.AccessControl.DirectorySecurity
    $acl.SetAccessRuleProtection($true, $false)
    $inheritance = [Security.AccessControl.InheritanceFlags]::ContainerInherit -bor [Security.AccessControl.InheritanceFlags]::ObjectInherit
    $propagation = [Security.AccessControl.PropagationFlags]::None
    foreach ($sid in @('S-1-5-18', 'S-1-5-32-544')) {
        $identity = New-Object Security.Principal.SecurityIdentifier($sid)
        $rule = New-Object Security.AccessControl.FileSystemAccessRule($identity, [Security.AccessControl.FileSystemRights]::FullControl, $inheritance, $propagation, [Security.AccessControl.AccessControlType]::Allow)
        [void]$acl.AddAccessRule($rule)
    }
    Set-Acl -LiteralPath $Path -AclObject $acl
}

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    throw "Scheduled task already exists; inspect it instead of overwriting: $TaskName"
}
$sourceKey = Assert-SourceFile $KeyPath
$sourceHosts = Assert-SourceFile $KnownHostsPath
Assert-PrivateKeyAcl $sourceKey
$copierSource = Join-Path $PSScriptRoot 'copy_backup_to_host.ps1'
Assert-SourceFile $copierSource | Out-Null

# Complete every conflict and path-safety check before creating either managed directory.
foreach ($candidate in @(
    @{ Path = $Destination; Marker = '.knowledge-manager-backup-root' },
    @{ Path = $ConfigDirectory; Marker = '.knowledge-manager-backup-config' }
)) {
    if (Test-Path -LiteralPath $candidate.Path) {
        $item = Get-Item -Force -LiteralPath $candidate.Path
        if (-not $item.PSIsContainer) { throw "Managed path is not a directory: $($candidate.Path)" }
        Assert-NoReparsePath $item.FullName
        if (-not (Test-Path -LiteralPath (Join-Path $candidate.Path $candidate.Marker) -PathType Leaf)) {
            throw "Existing directory is not marked as Knowledge Manager state: $($candidate.Path)"
        }
    } else {
        $parent = Split-Path -Parent $candidate.Path
        if (-not (Test-Path -LiteralPath $parent -PathType Container)) { throw "Managed parent directory does not exist: $parent" }
        Assert-NoReparsePath $parent
    }
}
$prospectiveConfig = [IO.Path]::GetFullPath($ConfigDirectory)
foreach ($name in @('copy_backup_to_host.ps1', 'backup_ed25519', 'known_hosts', 'config.json')) {
    if (Test-Path -LiteralPath (Join-Path $prospectiveConfig $name)) {
        throw "Refusing to overwrite existing installed state: $(Join-Path $prospectiveConfig $name)"
    }
}

$destinationPath = Initialize-ManagedDirectory $Destination '.knowledge-manager-backup-root'
$configPath = Initialize-ManagedDirectory $ConfigDirectory '.knowledge-manager-backup-config'
Protect-ManagedPath $configPath
Protect-ManagedPath $destinationPath

$installedCopier = Join-Path $configPath 'copy_backup_to_host.ps1'
$installedKey = Join-Path $configPath 'backup_ed25519'
$installedHosts = Join-Path $configPath 'known_hosts'
$installedConfig = Join-Path $configPath 'config.json'
Copy-Item -LiteralPath $copierSource -Destination $installedCopier
Copy-Item -LiteralPath $sourceKey -Destination $installedKey
Copy-Item -LiteralPath $sourceHosts -Destination $installedHosts
$configuration = [ordered]@{
    destination = $destinationPath
    vmName = $VMName
    guestMac = $GuestMac
    guestUser = $GuestUser
    keyPath = $installedKey
    knownHostsPath = $installedHosts
    hostKeyAlias = $HostKeyAlias
    timeoutSeconds = 900
    maxArchiveBytes = 107374182400
    minimumFreeBytes = 5368709120
}
[IO.File]::WriteAllText($installedConfig, ($configuration | ConvertTo-Json), (New-Object Text.UTF8Encoding($false)))
$powerShell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$arguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + $installedCopier + '" -ConfigPath "' + $installedConfig + '"'
$action = New-ScheduledTaskAction -Execute $powerShell -Argument $arguments
$trigger = New-ScheduledTaskTrigger -Once -At ([DateTime]::Now.AddMinutes(5)) -RepetitionInterval (New-TimeSpan -Hours 1)
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
$task = New-ScheduledTask -Action $action -Trigger $trigger -Principal $principal -Settings $settings
Register-ScheduledTask -TaskName $TaskName -InputObject $task | Out-Null
Write-Output (ConvertTo-Json -Compress ([ordered]@{
    installed = $true
    taskName = $TaskName
    destination = $destinationPath
    configDirectory = $configPath
    interval = 'PT1H'
}))

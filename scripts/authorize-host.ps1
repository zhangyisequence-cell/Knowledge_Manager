param(
    [string]$TargetAddress = '100.64.186.105',
    [string]$UserName = 'Administrator'
)
$ErrorActionPreference = 'Stop'
$keyPath = Join-Path $env:USERPROFILE '.ssh\knowledge_manager_ed25519'
$publicKeyPath = "$keyPath.pub"
if (!(Test-Path -LiteralPath $publicKeyPath)) {
    throw "Management public key not found: $publicKeyPath"
}
$publicKey = (Get-Content -LiteralPath $publicKeyPath -Raw).Trim()
if ($publicKey -notmatch '^ssh-ed25519 [A-Za-z0-9+/=]+(?: .*)?$') {
    throw 'Expected an Ed25519 public key.'
}
# Only the public key is transmitted. SSH reads the password directly from its terminal.
$publicKeyLiteral = $publicKey.Replace("'", "''")
$remoteScript = @'
$ErrorActionPreference = 'Stop'
try {
    $pub = '__PUBLIC_KEY__'
    $authFile = Join-Path $env:ProgramData 'ssh\administrators_authorized_keys'
    if (!(Test-Path -LiteralPath (Split-Path -Parent $authFile))) {
        throw 'OpenSSH configuration directory not found.'
    }
    if (Test-Path -LiteralPath $authFile) {
        Copy-Item -LiteralPath $authFile -Destination ($authFile + '.backup-' + [guid]::NewGuid().ToString('N'))
    } else {
        New-Item -ItemType File -Path $authFile | Out-Null
    }
    if (@(Get-Content -LiteralPath $authFile) -notcontains $pub) {
        Add-Content -LiteralPath $authFile -Value "`r`n$pub" -Encoding ascii
    }
    & icacls.exe $authFile /inheritance:r /grant:r '*S-1-5-32-544:F' '*S-1-5-18:F'
    if ($LASTEXITCODE -ne 0) { throw 'Could not set authorized-key permissions.' }
    Write-Output 'Management public key installed.'
} catch {
    Write-Error $_
    exit 1
}
'@
$remoteScript = $remoteScript.Replace('__PUBLIC_KEY__', $publicKeyLiteral)
# EncodedCommand preserves quoting across the remote Windows shell.
$encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($remoteScript))
Write-Host "Enter the Windows password for $UserName on $TargetAddress when SSH prompts."
Write-Host 'The password will not appear as you type. Do not enter it into chat.'
& ssh -o StrictHostKeyChecking=yes -o ConnectTimeout=10 -o PubkeyAuthentication=no "$UserName@$TargetAddress" "powershell.exe -NoProfile -NonInteractive -EncodedCommand $encoded"
if ($LASTEXITCODE -ne 0) { throw 'SSH authorization did not complete.' }
& ssh -i $keyPath -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=10 "$UserName@$TargetAddress" hostname
if ($LASTEXITCODE -ne 0) { throw 'Public key was written, but login verification failed. Check the server SSH configuration.' }
Write-Host 'SUCCESS: management key login verified.'

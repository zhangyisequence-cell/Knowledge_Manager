param(
    [Parameter(Mandatory=$true)][string]$TextBase64,
    [string]$VMName = 'Ubuntu',
    [switch]$ClearLine,
    [switch]$Enter
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$text = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($TextBase64))
if ($text -match '[^\x20-\x7e]') { throw 'Only printable ASCII is supported.' }
$vm = Get-VM -Name $VMName
$keyboard = Get-CimInstance -Namespace root/virtualization/v2 -ClassName Msvm_Keyboard |
    Where-Object SystemName -eq $vm.Id.ToString()
function Send-Key([string]$method, [int]$code) {
    $result = Invoke-CimMethod -InputObject $keyboard -MethodName $method -Arguments @{KeyCode=[uint32]$code}
    if ($result.ReturnValue -ne 0) { throw "Keyboard returned $($result.ReturnValue)" }
}
if ($ClearLine) {
    Send-Key PressKey 17
    Send-Key TypeKey 85
    Send-Key ReleaseKey 17
}
$plain = '`1234567890-=[]\;'',./'
$shifted = '~!@#$%^&*()_+{}|:"<>?'
$codes = @(192,49,50,51,52,53,54,55,56,57,48,189,187,219,221,220,186,222,188,190,191)
foreach ($character in $text.ToCharArray()) {
    $shift = $false
    if ($character -cmatch '[a-z]') { $code = [int][char]([string]$character).ToUpperInvariant() }
    elseif ($character -cmatch '[A-Z]') { $code = [int]$character; $shift = $true }
    elseif ($character -eq ' ') { $code = 32 }
    elseif ($plain.IndexOf($character) -ge 0) { $code = $codes[$plain.IndexOf($character)] }
    else { $code = $codes[$shifted.IndexOf($character)]; $shift = $true }
    if ($shift) { Send-Key PressKey 16 }
    Send-Key TypeKey $code
    if ($shift) { Send-Key ReleaseKey 16 }
    Start-Sleep -Milliseconds 100
}
if ($Enter) { Send-Key TypeKey 13 }

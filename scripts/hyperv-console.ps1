# Read-only Hyper-V guest-console thumbnail, emitted as base64 PNG for remote diagnosis.
param([string]$VMName = 'Ubuntu')
$ProgressPreference = 'SilentlyContinue'
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
$id = (Get-VM -Name $VMName).Id.ToString()
$vm = Get-CimInstance -Namespace root/virtualization/v2 -ClassName Msvm_ComputerSystem | Where-Object Name -eq $id
$svc = Get-CimInstance -Namespace root/virtualization/v2 -ClassName Msvm_VirtualSystemManagementService
$r = Invoke-CimMethod -InputObject $svc -MethodName GetVirtualSystemThumbnailImage -Arguments @{TargetSystem=$vm;WidthPixels=[uint16]1024;HeightPixels=[uint16]768}
if ($r.ReturnValue -ne 0) { throw "Thumbnail failed: $($r.ReturnValue)" }
$bmp = New-Object System.Drawing.Bitmap(1024,768,[System.Drawing.Imaging.PixelFormat]::Format16bppRgb565)
$stream = New-Object IO.MemoryStream
try {
    $rect = New-Object System.Drawing.Rectangle(0,0,1024,768)
    $bits = $bmp.LockBits($rect,[System.Drawing.Imaging.ImageLockMode]::WriteOnly,$bmp.PixelFormat)
    try { [Runtime.InteropServices.Marshal]::Copy([byte[]]$r.ImageData,0,$bits.Scan0,1572864) }
    finally { $bmp.UnlockBits($bits) }
    $bmp.Save($stream,[System.Drawing.Imaging.ImageFormat]::Png)
    [Convert]::ToBase64String($stream.ToArray())
} finally { $stream.Dispose(); $bmp.Dispose() }

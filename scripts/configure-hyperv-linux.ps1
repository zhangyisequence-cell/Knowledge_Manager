# Run on the Hyper-V host only after checking the guest console and stopping the VM.
# Does not install an OS, change virtual disks, or disable Secure Boot.
param([string]$VMName = 'Ubuntu')
$ErrorActionPreference = 'Stop'
$vm = Get-VM -Name $VMName
if ($vm.State -ne 'Off') { throw 'VM must be off before changing its firmware.' }
if ($vm.Generation -ne 2) { throw 'This configuration is for Generation 2 VMs only.' }
$disk = @(Get-VMHardDiskDrive -VMName $VMName)
if ($disk.Count -ne 1) { throw 'Expected exactly one OS disk; inspect multiple disks manually.' }
$previous = [ordered]@{
    VMName = $VMName
    Firmware = Get-VMFirmware -VMName $VMName | Select-Object SecureBoot,SecureBootTemplate
    DVD = @(Get-VMDvdDrive -VMName $VMName | Select-Object ControllerNumber,ControllerLocation,Path)
    OSDrive = $disk[0].Path
}
$previous | ConvertTo-Json -Depth 5
Set-VMFirmware -VMName $VMName -EnableSecureBoot On -SecureBootTemplate MicrosoftUEFICertificateAuthority -FirstBootDevice $disk[0]
# Keep installer/autoinstall media from reinstalling when an existing disk fails to boot.
Get-VMDvdDrive -VMName $VMName | Set-VMDvdDrive -Path $null
Write-Output 'Linux Secure Boot configured; installation media detached; disks unchanged.'

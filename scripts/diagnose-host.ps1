# Run on the intended Windows laptop. Read-only; does not change firewall or services.
$ErrorActionPreference = 'Continue'
Write-Output 'Windows host'
Get-CimInstance Win32_OperatingSystem | Select-Object Caption,Version,OSArchitecture
Get-CimInstance Win32_ComputerSystem | Select-Object @{Name='MemoryGB';Expression={[math]::Round($_.TotalPhysicalMemory / 1GB,1)}}
Write-Output 'SSH service'
Get-Service sshd -ErrorAction SilentlyContinue | Select-Object Name,Status,StartType
Write-Output 'SSH listening port'
Get-NetTCPConnection -State Listen -LocalPort 22 -ErrorAction SilentlyContinue | Select-Object LocalAddress,LocalPort,OwningProcess
Write-Output 'SSH firewall rules'
Get-NetFirewallRule -ErrorAction SilentlyContinue | Where-Object { $_.Name -like '*SSH*' -or $_.DisplayName -like '*SSH*' } | Select-Object Name,Enabled,Direction,Action,Profile
Write-Output 'Available runtimes'
Get-Command python,py,git,ffmpeg,tesseract -ErrorAction SilentlyContinue | Select-Object Name,Source

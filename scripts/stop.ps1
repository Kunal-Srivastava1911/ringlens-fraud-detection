<#  Stops the API, the CDC worker, the Vite dev server and both databases.  #>
$Root = Split-Path -Parent $PSScriptRoot
Get-CimInstance Win32_Process -Filter "Name='ringlens.exe' OR Name='python.exe'" |
    Where-Object { $_.CommandLine -like '*ringlens*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; Write-Host "stopped $($_.Name) $($_.ProcessId)" }
Get-CimInstance Win32_Process -Filter "Name='node.exe'" |
    Where-Object { $_.CommandLine -like "*$Root\frontend*vite*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; Write-Host "stopped vite $($_.ProcessId)" }
& (Join-Path $PSScriptRoot 'db.ps1') stop

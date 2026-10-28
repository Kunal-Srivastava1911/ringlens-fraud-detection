<#
  One command to run RingLens on Windows:

    .\scripts\start.ps1            build the console if needed, start everything, open the browser
    .\scripts\start.ps1 -Dev       same, but run the Vite dev server (hot reload) on :5180
    .\scripts\start.ps1 -Reseed    regenerate the synthetic dataset first
    .\scripts\stop.ps1             stop everything

  First run installs what is missing (portable databases, Python venv, node modules).
#>
param([switch]$Dev, [switch]$Reseed, [switch]$Rebuild, [switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Backend = Join-Path $Root 'backend'
$Frontend = Join-Path $Root 'frontend'
$Logs = Join-Path $Root '.runtime\logs'
$Ringlens = Join-Path $Backend '.venv\Scripts\ringlens.exe'
$Python = Join-Path $Backend '.venv\Scripts\python.exe'

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Test-Port($Port) {
    $c = New-Object Net.Sockets.TcpClient
    try { $c.Connect('127.0.0.1', $Port); $true } catch { $false } finally { $c.Close() }
}
# Launch a detached, hidden process with its output in a log file (redirected inside cmd,
# so the child holds no handles of this console).
function Start-Detached($Name, $Exe, $ArgLine, $Dir) {
    New-Item -ItemType Directory -Force $Logs | Out-Null
    Start-Process -FilePath 'cmd.exe' -WindowStyle Hidden -WorkingDirectory $Dir `
        -ArgumentList '/c', "`"`"$Exe`" $ArgLine > `"$Logs\$Name.log`" 2>&1`""
}

if (-not (Test-Path (Join-Path $Root '.runtime\mongodb'))) {
    Step 'Installing portable MongoDB, Neo4j, GDS and APOC (one time, ~700 MB)'
    & (Join-Path $PSScriptRoot 'setup-native.ps1')
}
if (-not (Test-Path $Ringlens)) {
    Step 'Creating the Python environment (one time)'
    python -m venv (Join-Path $Backend '.venv')
    & $Python -m pip install -q --upgrade pip
    & $Python -m pip install -q -e "$Backend[dev]"
}
if (-not (Test-Path (Join-Path $Frontend 'node_modules'))) {
    Step 'Installing frontend packages (one time)'
    Push-Location $Frontend; pnpm install; Pop-Location
}

Step 'Starting MongoDB (replica set rs0) and Neo4j'
& (Join-Path $PSScriptRoot 'db.ps1') start

$claims = & $Python -c "from ringlens.db import mongo_db; print(mongo_db().claims.estimated_document_count())"
if ($Reseed -or [int]$claims -eq 0) {
    Step 'Generating the synthetic world (demo profile, seed 1)'
    & $Ringlens seed --profile demo --seed 1
}

if (-not (Get-CimInstance Win32_Process -Filter "Name='ringlens.exe'" | Where-Object { $_.CommandLine -like '* cdc*' })) {
    Step 'Starting the CDC worker (supervised)'
    Start-Detached 'cdc' $Ringlens 'cdc' $Backend
}

if (-not $Dev -and ($Rebuild -or -not (Test-Path (Join-Path $Frontend 'dist\index.html')))) {
    Step 'Building the console'
    Push-Location $Frontend; pnpm build; Pop-Location
}

if (-not (Test-Port 8000)) {
    Step 'Starting the API on http://localhost:8000'
    Start-Detached 'api' $Ringlens 'api' $Backend
    for ($i = 0; $i -lt 60 -and -not (Test-Port 8000); $i++) { Start-Sleep 1 }
}

Step 'Waiting for the CDC snapshot'
& $Python -c @"
import time
from ringlens.cdc import state
from ringlens.db import mongo_db
for _ in range(600):
    s = state()
    if s.get('state') == 'streaming' and s.get('snapshot'):
        break
    time.sleep(1)
print('graph in sync:', s.get('snapshot'))
if mongo_db().provider_scores.estimated_document_count() == 0:
    from ringlens.jobs import analyze
    print('first analytics run:', analyze('start-script')['seconds'], 's')
"@

$url = 'http://localhost:8000'
if ($Dev) {
    Step 'Starting the Vite dev server on http://localhost:5180'
    Start-Detached 'vite' 'pnpm.cmd' 'dev' $Frontend
    $url = 'http://localhost:5180'
    for ($i = 0; $i -lt 60 -and -not (Test-Port 5180); $i++) { Start-Sleep 1 }
}
Write-Host "`nRingLens is running: $url   (Neo4j Browser: http://localhost:7474, neo4j / ringlens-dev)" -ForegroundColor Green
Write-Host "Logs: $Logs   ·   Stop with .\scripts\stop.ps1"
if (-not $NoBrowser) { Start-Process $url }

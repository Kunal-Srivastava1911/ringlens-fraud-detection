<#
  Starts, stops and reports on the native databases installed by setup-native.ps1.
    .\scripts\db.ps1 start | stop | status | reset
  MongoDB runs as a single-member replica set (rs0) on 127.0.0.1:27017, because
  change streams need the oplog. Neo4j runs on bolt://127.0.0.1:7687.
#>
param([ValidateSet('start', 'stop', 'status', 'reset')][string]$Action = 'status')
$ErrorActionPreference = 'Stop'

$Root      = Split-Path -Parent $PSScriptRoot
$Runtime   = Join-Path $Root '.runtime'
$Mongod    = Join-Path $Runtime 'mongodb\bin\mongod.exe'
$Mongosh   = Join-Path $Runtime 'mongosh\bin\mongosh.exe'
$Neo4jHome = Join-Path $Runtime 'neo4j'
$MongoData = Join-Path $Runtime 'data\mongo'
$Logs      = Join-Path $Runtime 'logs'

if (-not (Test-Path $Mongod)) { throw "Run .\scripts\setup-native.ps1 first." }

function Test-Port($Port) {
    $c = New-Object Net.Sockets.TcpClient
    try { $c.Connect('127.0.0.1', $Port); $true } catch { $false } finally { $c.Close() }
}

function Wait-Until($What, $Seconds, [scriptblock]$Check) {
    for ($i = 0; $i -lt $Seconds; $i++) { if (& $Check) { return } ; Start-Sleep 1 }
    throw "$What did not come up within $Seconds s (see $Logs)"
}

function Start-Mongo {
    if (Test-Port 27017) { Write-Host "MongoDB already running"; return }
    New-Item -ItemType Directory -Force $MongoData, $Logs | Out-Null
    Start-Process -FilePath $Mongod -WindowStyle Hidden -ArgumentList @(
        '--replSet', 'rs0', '--port', '27017', '--bind_ip', '127.0.0.1',
        '--dbpath', "`"$MongoData`"", '--oplogSize', '2048', '--wiredTigerCacheSizeGB', '2',
        '--logpath', "`"$Logs\mongod.log`"", '--logappend')
    Wait-Until 'MongoDB' 60 { Test-Port 27017 }
    # Initiate the replica set on first boot, then wait for this member to become primary.
    & $Mongosh --quiet --port 27017 --eval @"
try { rs.status().ok } catch (e) { rs.initiate({_id: 'rs0', members: [{_id: 0, host: '127.0.0.1:27017'}]}) }
for (let i = 0; i < 60 && !db.hello().isWritablePrimary; i++) sleep(500);
print('MongoDB primary: ' + db.hello().isWritablePrimary + ' (version ' + db.version() + ')');
"@
}

function Start-Neo4j {
    if (Test-Port 7687) { Write-Host "Neo4j already running"; return }
    $env:JAVA_HOME = Join-Path $Runtime 'jdk'
    $env:NEO4J_HOME = $Neo4jHome
    New-Item -ItemType Directory -Force $Logs | Out-Null
    # Redirect inside cmd, so the detached process holds no handles of this shell.
    $bat = Join-Path $Neo4jHome 'bin\neo4j.bat'
    Start-Process -FilePath 'cmd.exe' -WindowStyle Hidden -ArgumentList '/c', "`"`"$bat`" console > `"$Logs\neo4j.log`" 2>&1`""
    Wait-Until 'Neo4j' 120 { Test-Port 7687 }
    Write-Host "Neo4j listening on bolt://127.0.0.1:7687 (browser http://localhost:7474)"
}

function Stop-All {
    if (Test-Port 27017) {
        # The server drops the connection as it shuts down, so mongosh's error is expected.
        cmd /c "`"$Mongosh`" --quiet --port 27017 --eval `"db.getSiblingDB('admin').shutdownServer({force: true})`" >nul 2>&1"
        Wait-Until 'MongoDB shutdown' 30 { -not (Test-Port 27017) }
        Write-Host "MongoDB stopped"
    }
    # Neo4j's console process is a java.exe launched from our private JDK.
    $jdk = Join-Path $Runtime 'jdk'
    Get-CimInstance Win32_Process -Filter "Name = 'java.exe'" |
        Where-Object { $_.ExecutablePath -like "$jdk*" } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force; Write-Host "Neo4j stopped" }
}

switch ($Action) {
    'start'  { Start-Mongo; Start-Neo4j }
    'stop'   { Stop-All }
    'status' {
        "MongoDB 27017: " + $(if (Test-Port 27017) { 'up' } else { 'down' })
        "Neo4j   7687:  " + $(if (Test-Port 7687) { 'up' } else { 'down' })
    }
    'reset'  {
        Stop-All; Start-Sleep 2
        Remove-Item -Recurse -Force $MongoData -ErrorAction SilentlyContinue
        Remove-Item -Recurse -Force (Join-Path $Neo4jHome 'data\databases\neo4j'), (Join-Path $Neo4jHome 'data\transactions\neo4j') -ErrorAction SilentlyContinue
        Write-Host "Data wiped. Run: .\scripts\db.ps1 start"
    }
}

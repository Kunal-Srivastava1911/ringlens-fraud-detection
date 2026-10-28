<#
  Downloads portable builds of MongoDB, mongosh, a private Java 21, Neo4j and the
  GDS/APOC plugins into .runtime\ (git-ignored). No admin rights, no installers,
  nothing outside the project folder. Safe to re-run: finished steps are skipped.
#>
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$MongoVersion  = '8.0.15'
$MongoshVersion = '2.5.8'
$Neo4jVersion  = '5.26.12'
$GdsVersion    = '2.13.7'
$ApocVersion   = '5.26.12'
$Neo4jPassword = if ($env:RINGLENS_NEO4J_PASSWORD) { $env:RINGLENS_NEO4J_PASSWORD } else { 'ringlens-dev' }

$Root    = Split-Path -Parent $PSScriptRoot
$Runtime = Join-Path $Root '.runtime'
$Cache   = Join-Path $Runtime 'downloads'
New-Item -ItemType Directory -Force $Cache | Out-Null

function Get-File($Url, $Name) {
    $dest = Join-Path $Cache $Name
    if (-not (Test-Path $dest)) {
        Write-Host "  downloading $Name"
        Invoke-WebRequest -Uri $Url -OutFile "$dest.part" -UseBasicParsing
        Move-Item "$dest.part" $dest
    }
    return $dest
}

# Expands a zip whose single top-level folder becomes $Target.
function Expand-Into($Zip, $Target) {
    if (Test-Path $Target) { return }
    $tmp = Join-Path $Runtime ('_x' + [guid]::NewGuid().ToString('N').Substring(0, 8))
    Expand-Archive -Path $Zip -DestinationPath $tmp
    $inner = Get-ChildItem $tmp | Select-Object -First 1
    Move-Item $inner.FullName $Target
    Remove-Item -Recurse -Force $tmp
}

Write-Host "MongoDB $MongoVersion"
Expand-Into (Get-File "https://fastdl.mongodb.org/windows/mongodb-windows-x86_64-$MongoVersion.zip" "mongodb-$MongoVersion.zip") (Join-Path $Runtime 'mongodb')

Write-Host "mongosh $MongoshVersion"
Expand-Into (Get-File "https://downloads.mongodb.com/compass/mongosh-$MongoshVersion-win32-x64.zip" "mongosh-$MongoshVersion.zip") (Join-Path $Runtime 'mongosh')

Write-Host "Java 21 (private to Neo4j; your system Java is untouched)"
Expand-Into (Get-File "https://api.adoptium.net/v3/binary/latest/21/ga/windows/x64/jdk/hotspot/normal/eclipse" "temurin-21.zip") (Join-Path $Runtime 'jdk')

Write-Host "Neo4j $Neo4jVersion"
$neo4jHome = Join-Path $Runtime 'neo4j'
$freshNeo4j = -not (Test-Path $neo4jHome)
Expand-Into (Get-File "https://dist.neo4j.org/neo4j-community-$Neo4jVersion-windows.zip" "neo4j-$Neo4jVersion.zip") $neo4jHome

Write-Host "Neo4j plugins: GDS $GdsVersion, APOC $ApocVersion"
$plugins = Join-Path $neo4jHome 'plugins'
foreach ($p in @(
        @("https://graphdatascience.ninja/neo4j-graph-data-science-$GdsVersion.jar", "neo4j-graph-data-science-$GdsVersion.jar"),
        @("https://github.com/neo4j/apoc/releases/download/$ApocVersion/apoc-$ApocVersion-core.jar", "apoc-$ApocVersion-core.jar"))) {
    $jar = Get-File $p[0] $p[1]
    if (-not (Test-Path (Join-Path $plugins $p[1]))) { Copy-Item $jar $plugins }
}

if ($freshNeo4j) {
    Write-Host "Configuring Neo4j"
    $conf = Join-Path $neo4jHome 'conf\neo4j.conf'
    Add-Content -Path $conf -Encoding ascii -Value @"

# ---- RingLens ----
dbms.security.procedures.unrestricted=gds.*,apoc.*
dbms.security.procedures.allowlist=gds.*,apoc.*
server.memory.heap.initial_size=1g
server.memory.heap.max_size=3g
server.memory.pagecache.size=1g
server.default_listen_address=127.0.0.1
"@
    $env:JAVA_HOME = Join-Path $Runtime 'jdk'
    & (Join-Path $neo4jHome 'bin\neo4j-admin.bat') dbms set-initial-password $Neo4jPassword | Out-Null
}

New-Item -ItemType Directory -Force (Join-Path $Runtime 'data\mongo'), (Join-Path $Runtime 'logs') | Out-Null
Write-Host "`nDone. Start the databases with: .\scripts\db.ps1 start"

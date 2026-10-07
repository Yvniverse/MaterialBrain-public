param([Parameter(Mandatory=$true)][string]$DatabaseBackup)
$ErrorActionPreference = "Stop"
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot "../.."))
$config = @{}
$envFile = Join-Path $root ".env"
if (Test-Path -LiteralPath $envFile) { Get-Content -LiteralPath $envFile | Where-Object { $_ -match '^[A-Za-z_][A-Za-z0-9_]*=' } | ForEach-Object { $key,$value = $_ -split '=',2; $config[$key]=$value } }
$storageSetting = if ($env:MATERIALBRAIN_STORAGE_ROOT) { $env:MATERIALBRAIN_STORAGE_ROOT } elseif ($config.MATERIALBRAIN_STORAGE_ROOT) { $config.MATERIALBRAIN_STORAGE_ROOT } else { "./storage" }
$storageRoot = [IO.Path]::GetFullPath($(if ([IO.Path]::IsPathRooted($storageSetting)) { $storageSetting } else { Join-Path $root $storageSetting }))
$backupRoot = [IO.Path]::GetFullPath((Join-Path $storageRoot "backups"))
$dbFile = [IO.Path]::GetFullPath($(if ([IO.Path]::IsPathRooted($DatabaseBackup)) { $DatabaseBackup } else { Join-Path $root $DatabaseBackup }))
if (-not $dbFile.StartsWith($backupRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase) -or -not (Test-Path -LiteralPath $dbFile -PathType Leaf) -or [IO.Path]::GetFileName($dbFile) -notmatch '^db_.+\.dump$') { throw "Restore file must be an existing db_TIMESTAMP.dump under the configured backups directory." }
$databaseUser = if ($env:POSTGRES_USER) { $env:POSTGRES_USER } elseif ($config.POSTGRES_USER) { $config.POSTGRES_USER } else { "materialbrain_public" }
$databaseName = if ($env:POSTGRES_DB) { $env:POSTGRES_DB } elseif ($config.POSTGRES_DB) { $config.POSTGRES_DB } else { "materialbrain_public" }
$project = if ($env:COMPOSE_PROJECT_NAME) { $env:COMPOSE_PROJECT_NAME } elseif ($config.COMPOSE_PROJECT_NAME) { $config.COMPOSE_PROJECT_NAME } else { "materialbrain_public_v02" }
Set-Location -LiteralPath $root
& (Join-Path $root "deploy/backup/backup.ps1") -BackupDirectory $backupRoot -AttachmentDirectory (Join-Path $storageRoot 'attachments') -EvidenceDirectory (Join-Path $storageRoot 'evidence')
docker compose -p $project stop backend
if ($LASTEXITCODE -ne 0) { throw "Backend stop failed" }
& python -c 'import subprocess,sys; stream=open(sys.argv[1],"rb"); result=subprocess.run(sys.argv[2:],stdin=stream); sys.exit(result.returncode)' $dbFile docker compose -p $project exec -T db pg_restore -U $databaseUser -d $databaseName --clean --if-exists --no-owner
if ($LASTEXITCODE -ne 0) { throw "Database restore failed; backend remains stopped" }
$stamp = [IO.Path]::GetFileNameWithoutExtension($dbFile).Substring(3)
$dumpDirectory = Split-Path -Parent $dbFile
$attachmentFile = Join-Path $dumpDirectory "attachments_$stamp.tar.gz"
if (Test-Path -LiteralPath $attachmentFile) {
  $attachmentRoot = Join-Path $storageRoot "attachments"
  New-Item -ItemType Directory -Force -Path $attachmentRoot | Out-Null
  tar -xzf $attachmentFile -C $attachmentRoot
  if ($LASTEXITCODE -ne 0) { throw "Attachment restore failed" }
}
$evidenceFile = Join-Path $dumpDirectory "evidence_$stamp.tar.gz"
if (Test-Path -LiteralPath $evidenceFile) {
  $evidenceRoot = Join-Path $storageRoot "evidence"
  New-Item -ItemType Directory -Force -Path $evidenceRoot | Out-Null
  tar -xzf $evidenceFile -C $evidenceRoot
  if ($LASTEXITCODE -ne 0) { throw "Evidence restore failed" }
}
docker compose -p $project start backend
if ($LASTEXITCODE -ne 0) { throw "Backend restart failed" }
Write-Host "Restore completed. Run docker compose logs backend to verify migrations and startup."

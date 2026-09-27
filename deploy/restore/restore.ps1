param([Parameter(Mandatory=$true)][string]$DatabaseBackup)
$ErrorActionPreference = "Stop"
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot "../.."))
$backupRoot = [IO.Path]::GetFullPath((Join-Path $root "storage/backups"))
$dbFile = [IO.Path]::GetFullPath((Join-Path $root $DatabaseBackup))
if (-not $dbFile.StartsWith($backupRoot, [StringComparison]::OrdinalIgnoreCase) -or -not (Test-Path -LiteralPath $dbFile)) { throw "Restore file must be an existing .dump file under storage/backups." }
$config = @{}
$envFile = Join-Path $root ".env"
if (Test-Path -LiteralPath $envFile) { Get-Content -LiteralPath $envFile | Where-Object { $_ -match '^[A-Za-z_][A-Za-z0-9_]*=' } | ForEach-Object { $key,$value = $_ -split '=',2; $config[$key]=$value } }
$databaseUser = if ($env:POSTGRES_USER) { $env:POSTGRES_USER } elseif ($config.POSTGRES_USER) { $config.POSTGRES_USER } else { "pengka" }
$databaseName = if ($env:POSTGRES_DB) { $env:POSTGRES_DB } elseif ($config.POSTGRES_DB) { $config.POSTGRES_DB } else { "pengka_material" }
& (Join-Path $root "deploy/backup/backup.ps1")
docker compose stop backend
Get-Content -AsByteStream -Raw -LiteralPath $dbFile | docker compose exec -T db pg_restore -U $databaseUser -d $databaseName --clean --if-exists --no-owner
$attachmentFile = $dbFile.Replace("db_", "attachments_").Replace(".dump", ".tar.gz")
if (Test-Path -LiteralPath $attachmentFile) { tar -xzf $attachmentFile -C (Join-Path $root "storage/attachments") }
$evidenceFile = $dbFile.Replace("db_", "evidence_").Replace(".dump", ".tar.gz")
if (Test-Path -LiteralPath $evidenceFile) {
  $evidenceRoot = Join-Path $root "storage/evidence"
  New-Item -ItemType Directory -Force -Path $evidenceRoot | Out-Null
  tar -xzf $evidenceFile -C $evidenceRoot
}
docker compose start backend
Write-Host "Restore completed. Run docker compose logs backend to verify migrations and startup."

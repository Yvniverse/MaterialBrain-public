param(
    [string]$BackupDirectory,
    [string]$AttachmentDirectory,
    [string]$EvidenceDirectory
)
$ErrorActionPreference = "Stop"
$repoRoot = & git rev-parse --show-toplevel
if ($LASTEXITCODE -ne 0) { throw "Run backup from the Git repository" }
$arguments = @((Join-Path $repoRoot 'tools/verified_backup.py'))
if ($BackupDirectory) { $arguments += @('--backup-directory', $BackupDirectory) }
if ($AttachmentDirectory) { $arguments += @('--attachment-directory', $AttachmentDirectory) }
if ($EvidenceDirectory) { $arguments += @('--evidence-directory', $EvidenceDirectory) }
& python @arguments
if ($LASTEXITCODE -ne 0) { throw "Backup failed; no verified manifest produced" }

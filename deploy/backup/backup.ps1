param(
    [string]$BackupDirectory = "storage/backups",
    [string]$AttachmentDirectory = "storage/attachments",
    [string]$EvidenceDirectory = "storage/evidence"
)
$ErrorActionPreference = "Stop"
$repoRoot = & git rev-parse --show-toplevel
if ($LASTEXITCODE -ne 0) { throw "Run backup from the Git repository" }
& python (Join-Path $repoRoot 'tools/verified_backup.py') --backup-directory $BackupDirectory --attachment-directory $AttachmentDirectory --evidence-directory $EvidenceDirectory
if ($LASTEXITCODE -ne 0) { throw "Backup failed; no verified manifest produced" }

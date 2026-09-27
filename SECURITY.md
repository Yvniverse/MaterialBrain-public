# Security Policy

## Reporting a vulnerability

Please **do not open a public GitHub issue** for a suspected vulnerability.

Preferred reporting path:

1. Use GitHub's private vulnerability reporting / Security Advisory interface if it is enabled for this repository.
2. Include affected version/commit, reproduction steps, impact, and any proposed mitigation.
3. Avoid including real credentials, customer data, or unnecessary sensitive evidence in the report.

## Security-sensitive areas

MaterialBrain includes authentication/RBAC, inventory transactions, file uploads, engineering evidence, agent tool execution, and deployment tooling. Changes to these areas should preserve least privilege, idempotency, auditability, and server-owned business truth.

## Supported versions

Until tagged releases are established, security fixes target the latest public default branch. Once release tags exist, this file should be updated with an explicit support table.

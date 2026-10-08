# Security Policy

## Reporting a vulnerability

Use the repository's GitHub Security Advisory reporting interface when available. Include the affected version or commit, reproduction steps, expected permissions, impact, and any proposed mitigation. Keep credentials, customer data, and sensitive evidence out of the report.

If confidential reporting is unavailable, request a reporting channel through a minimal issue without exploit details or sensitive attachments.

## Security-sensitive components

Authentication, RBAC, inventory transactions, uploads, engineering evidence, agent tools, robot mission controls, and deployment scripts require explicit permission checks and bounded inputs. Model-generated instructions must not bypass these controls.

Robot simulation endpoints report `hardware_control=false`. Deployments that connect additional transports must define authentication, network access, cancellation, and physical safety requirements before enabling them.

## Updates

Security fixes target the current default branch and are included in subsequent releases. Deployments should track dependency and container updates, use HTTPS, and retain tested backups. Operational guidance is in [docs/SECURITY.md](docs/SECURITY.md).

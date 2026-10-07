# Secure operation

## Configuration

Store database and model credentials in local environment configuration or a secret manager. `.env` is excluded from source control. Model keys are consumed by backend providers; frontend bundles must not contain them.

The default web listener uses port `18080`. Put an HTTPS reverse proxy in front of remote deployments, enable `COOKIE_SECURE=true`, and restrict access to intended users. Keep the database and robot bridge off public networks. The development test database binds only to loopback.

Use a distinct Compose project, database, named volume, storage directory, and exposed port for every installation or test runtime. Verify these values before running migrations, backups, restores, or fixture loaders.

## Authentication and permissions

Passwords are hashed with Argon2. Session cookies are HttpOnly and use SameSite protection. Write requests require the matching CSRF token. Backend endpoint permissions control material management, inventory operations, map changes, and other sensitive actions.

Agent proposals remain separate from approved transactions. Approval must revalidate permissions, current stock, and idempotency within the transaction. A browser display or model answer cannot authorize a write.

## Files and model output

Uploads use bounded file sizes, allowlisted extensions, managed paths, and content hashes. The application does not include a malware scanning engine; add an appropriate scanning step for untrusted uploaded files.

Treat retrieved documents and provider output as untrusted content. Tool arguments must pass typed validation, and tools must be registered with explicit permissions. Output must not disclose credentials, session state, or hidden instructions.

## Robot execution

The supplied ROS2/Nav2 transport runs simulation. Inspect `transport`, `hardware_control`, navigation health, and active mission identifiers before starting or cancelling a mission. Keep simulation networks and ports scoped to the installation.

Arrival, scan, and handoff represent distinct observations. They do not automatically prove physical handling or authorize inventory mutation. See [Robotics](ROBOTICS.md) for the execution contract.

## Maintenance

Update dependencies and base images, retain off-host backups, and exercise restoration into a separate runtime. Vulnerability reporting is described in the repository [security policy](../SECURITY.md).

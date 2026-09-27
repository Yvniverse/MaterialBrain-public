# Contributing to MaterialBrain

Thanks for your interest in MaterialBrain.

## Before opening a pull request

1. Open an issue for larger behavior or data-contract changes.
2. Keep business truth deterministic: do not move inventory, BOM, selection, provenance, or approval truth into model prose.
3. Do not add production credentials, real customer/company data, private evidence archives, or proprietary vendor documents.
4. Keep public tests reproducible without the maintainer's private self-hosted runner.

## Development checks

Backend:

```bash
cd backend
python -m ruff check app scripts tests evals
python -m pytest
```

Frontend:

```bash
cd frontend
pnpm install --frozen-lockfile
pnpm lint
pnpm test
pnpm build
```

## Pull request expectations

A good PR should include:

- a concise problem statement;
- the smallest reasonable implementation;
- tests for changed behavior;
- migration notes when persisted schemas change;
- screenshots for user-visible changes;
- explicit confirmation that no secrets or private evidence were added.

If a change affects inventory mutation, BOM write paths, selection truth, permissions, release qualification, or security, describe the invariants being protected.

## Commit scope

Avoid bundling unrelated formatting or generated runtime artifacts with product changes. Do not commit `.env`, local backups, evaluation telemetry containing private content, or browser-session credentials.

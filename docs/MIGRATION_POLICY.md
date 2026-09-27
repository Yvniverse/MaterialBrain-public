# Alembic Migration Immutability Policy

## Rule

A migration that has shipped or been used by a qualified candidate is immutable.

Do not repair an old deployment problem by editing `0001...0012`. Add a new Alembic revision.

Phase 2.7 freezes history through:

`0012_picking_core`

The exact hashes are stored in:

`backend/alembic/MIGRATION_HISTORY_SHA256.json`

and checked by:

`python tools/check_migration_history.py`

CI runs this check before the Alembic upgrade/test stage.

## Why

Early MaterialBrain migrations import current ORM metadata, so historical revisions were vulnerable to changing behavior when new models were added. Phase 2.7 handled that compatibility debt, but future phases must stop rewriting migration history.

## Future revisions

- New Phase 2.8 schema work should create `0013_...` or later.
- New files are allowed by the frozen-history checker.
- After a new revision is released/qualified, deliberately extend the freeze manifest in its own reviewed change.
- Do not regenerate checksums to hide an accidental historical edit.

## Rollback

Application rollback and database downgrade are different operations. Prefer rolling back application images while retaining a forward-compatible additive schema. Never downgrade a migration that contains real production data without a specific recovery plan and backup verification.

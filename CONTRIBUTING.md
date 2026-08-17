# Contributing

This repository is a **public showcase** derived from a private investment decision system. The `main` branch is a curated, desensitized snapshot — **stability of `main` is the top priority**. Changes land here through two distinct flows (see below), and nothing reaches `main` without passing the release gate.

## Branching model

| Branch | Purpose | Direct push |
|---|---|---|
| `main` | Stable, curated snapshot | ❌ Forbidden (branch-protected) |
| `feat/<topic>` | New feature / showcase-layer content (README, docs, demo, MCP, …) | ✅ on the branch only |
| `feat/sync-<release-hash>` | Desensitized sync from the internal release | ✅ on the branch only |
| `bugfix/<topic>` | Fixes for defects already on `main` | ✅ on the branch only |

Two kinds of change, two flows:

- **Showcase-layer** — README, docs, demos, MCP wrappers, and anything authored directly in this repo → `feat/<topic>`.
- **Internal sync** — code pulled from the private release, desensitized, and dropped in as a batch snapshot → `feat/sync-<release-hash>`. This is a *replace*, not an incremental edit, so it carries an extra desensitization step and often a large diff.

## Workflow

1. **Never push directly to `main`.** Every change lands via a pull request.
2. Branch off `main` with the appropriate prefix (`feat/…`, `feat/sync-…`, or `bugfix/…`).
3. Keep your branch up to date with `main` using **rebase** (linear history — do not merge):
   ```bash
   git fetch origin && git rebase origin/main
   ```
4. Before merging into `main`, every change MUST pass the **release gate**:
   - **Desensitization scan** — no secrets, account amounts, holdings, or absolute paths.
   - **Smoke test** — clean venv install + fresh clone + full import coverage + `python -m` run.
   - **Release check report** — human review and explicit approval.
5. Merge into `main` only when the feature is **stable**. There is no fixed cadence — merge when ready, not on a schedule.

## Why the gate matters

This repository is public, and pushing is irreversible once merged. The gate exists so that a bad sync or an accidental secret never reaches the public `main` branch.

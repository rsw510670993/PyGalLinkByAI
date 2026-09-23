---
name: egs-115-organizer
description: Inspect, reconcile, and organize pyGal EGS resources after they are submitted to 115. Use for 115 download checks, directory naming or movement, duplicate and collection ownership, GAL.old reconciliation, and AI organizer jobs. Do not use to approve EGS magnet candidates before submission.
---

# EGS 115 Organizer

Handle only the post-submission portion of the pyGal workflow. EGS magnet selection remains a human review step.

Read [docs/AI_115_ORGANIZER.md](../../../docs/AI_115_ORGANIZER.md) for the agent contract and [docs/EGS_115_ORGANIZATION_PLAYBOOK.md](../../../docs/EGS_115_ORGANIZATION_PLAYBOOK.md) when historical or collection reconciliation is involved.

## Operating contract

1. Inspect the current branch and dirty worktree. Preserve unrelated edits.
2. Identify the exact EGS rows, magnet InfoHash, 115 CID/pick code, current parent path, and intended destination before changing state.
3. Prefer existing pyGal CLI and organizer functions. Do not write ad-hoc SQL when a domain operation exists.
4. Make a database backup before the first mutation in a job. Record the backup and each externally visible action.
5. Execute routine rename, move, wrapping, downloaded-state reconciliation, and previously documented collection ownership autonomously when evidence is complete.
6. Verify the resulting CID, parent path, name, database mapping, and database integrity after mutation.
7. Return structured evidence and actions. If evidence conflicts or is incomplete, stop that item and set `requires_human=true`; continue safe independent items.

## Hard boundaries

- Never change or approve an EGS magnet candidate as part of 115 organization.
- Never infer identical content from similar titles alone. Require matching InfoHash, verified file comparison, or an explicit documented manual relationship.
- Never treat `downloaded=1` as proof that the current magnet is correct.
- Never delete a non-empty directory merely because another directory has a similar name. Merge unique saves, patches, and bonuses first.
- Do not search `/GAL.old`, `/我的下载`, or the whole drive as an ordinary download check. Historical audits must be explicitly requested.
- Do not mutate `getchu.db` to make EGS data fit.
- Do not edit project source, run Git operations, or broaden the requested years while executing an AI organizer job.

## Completion

A task is complete only when every attempted mutation is verified. Report completed, partial, blocked, or failed; list evidence, actions, remaining items, and whether human judgment is required.

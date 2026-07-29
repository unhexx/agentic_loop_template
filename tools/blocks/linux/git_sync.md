# Linux git sync (single repo default)

```bash
./scripts/sync-worktree.sh --verify-only
# after merge on feature:
./scripts/sync-worktree.sh
```

Look for marker line `SYNC_DONE` in script output. Record hashes in `git_sync_status`.

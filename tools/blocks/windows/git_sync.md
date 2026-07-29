# Windows git sync

```powershell
powershell -File .\scripts\sync-worktree.ps1 -VerifyOnly
# full sync if script present in consumer
```

Expect `SYNC_DONE` marker. Prefer `git -C` for cross-clone paths.

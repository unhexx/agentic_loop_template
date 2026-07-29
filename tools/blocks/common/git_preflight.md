# Git preflight (cheap, single script)

Prefer one script over five interactive `gh` blocks:

```bash
./scripts/preflight_git.sh
# strict multi-repo (only if project_config.git.strict_multi_repo=true or template files dirty):
STRICT_MULTI_REPO=1 ./scripts/preflight_git.sh
```

Put script exit code + key lines into `git_sync_status.commands_run`.
Do not paste full `gh pr list` JSON into the chat unless debugging auth.

# TOOLS INSTRUCTIONS (thin entrypoint)

Full copy-paste blocks live under `tools/blocks/{common,linux,windows}/`.

```bash
# Show only what you need
python tools/select.py --intent memory
python tools/select.py --intent git --os linux
python tools/select.py --intent state
```

Legacy long monologue TOOLS files in consumer forks should be replaced by this progressive layout when syncing from SSOT (`scripts/sync_template_from_ssot.sh`).

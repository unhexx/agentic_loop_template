utc: 2026-10-25T12:13:00Z

commands:
- .venv/bin/python -m memory doctor --strict --json exit 0
- .venv/bin/python -m memory.proxy health --strict --json exit 0
- .venv/bin/python -m memory.stack check exit 0

class: pass
ok: true
finding_codes: none
proxy.ok: true

ports:
- pxpipe 8100: true
- pxpipe_agy_optional 8101: true
- gateway 8110: true
- control_plane 8112: false
- searxng 8080: true
- ldr_research 5000: false

gemini_api_key_set: false
google_gemini_base_url_set: false
p10_safe: true

version_file: 3.13.0
pushed: false
proof_slice_exit: 0

pytest green: .venv/bin/python -m pytest -q memory/test_adapters_by_role.py memory/test_doctor.py memory/test_packaging.py::test_console_scripts_include_doctor_and_local_mcp memory/test_init_project.py::test_full_scaffold_fills_files_and_symlink memory/test_ci_parity.py memory/test_performance_ledger.py::test_append_compaction_keeps_total_cycles_in_sync memory/test_performance_ledger.py::test_append_omits_null_optional_keys memory/test_meta_lock.py::test_update_performance_ledger_does_not_insert_cycle_zero memory/test_playbooks_lock.py::test_save_index_skips_timestamp_when_bodies_unchanged memory/test_playbooks_lock.py::test_human_views_use_tmp_replace memory/test_stream_lease.py::test_runtime_lock_files_are_gitignored memory/test_pxpipe_agy_proxy.py::test_pxpipe_agy_proxy_pins_upstream memory/test_local_mcp.py::test_tools_list_matches_catalog_and_cursor memory/test_local_mcp.py::test_list_tools_stdout_is_one_object memory/test_local_mcp.py::test_knowledge_read_does_not_commit memory/test_local_mcp.py::test_state_snapshot_does_not_rewrite_small_markdown memory/test_local_mcp.py::test_redact_text_and_local_mcp_logger_drop_github_tokens

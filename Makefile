PYTHON ?= .venv/bin/python
CONFIG ?= config.json
ARCHIVE_SOURCE ?= state/history-archives
ARCHIVE_TARGET ?=
MULTI_NODE_DAA_LAG_WARNING ?= 120
MULTI_NODE_BLOCK_LAG_WARNING ?= 120
MULTI_NODE_STALE_MINUTES ?= 10
MULTI_NODE_PEER_LAG_WARNING ?= 2
MULTI_NODE_PROCESSED_AGE_LAG_WARNING ?= 60

.PHONY: help onboard bootstrap proto-check version status stream summary sync-report diagnostics-summary incident-report json alert smoke ci integrations simulate-exporter-failure ensure-exporter diagnostics diagnostics-archive daily-report weekly-report weekly-archive benchmark benchmark-report prometheus export-history history-report history-multi-node history-archive upload-archive package prune validate recover-dry-run recover force-recover-dry-run

help:
	@printf 'Kaspa Node Watchtower operator commands\n'
	@printf '\n'
	@printf '  make onboard             Run guided local onboarding checks\n'
	@printf '  make bootstrap           Create venv, install deps, generate protobuf\n'
	@printf '  make proto-check         Verify generated protobuf files are current\n'
	@printf '  make version             Print watchtower version\n'
	@printf '  make status              Run the cron-style health check\n'
	@printf '  make stream              Generate the 1080p OBS/YouTube stream page\n'
	@printf '  make summary             Print a one-shot health summary\n'
	@printf '  make sync-report         Print focused mainnet sync progress\n'
	@printf '  make diagnostics-summary Print sanitized incident summary\n'
	@printf '  make incident-report     Print sanitized Markdown incident report\n'
	@printf '  make json                Print the raw JSON health report\n'
	@printf '  make smoke               Run the local smoke test suite\n'
	@printf '  make ci                  Check latest GitHub Actions smoke run\n'
	@printf '  make integrations        Check exporter, Prometheus, Grafana, and CI\n'
	@printf '  make simulate-exporter-failure Verify exporter failure detection\n'
	@printf '  make ensure-exporter     Install/restart the Prometheus exporter LaunchAgent\n'
	@printf '  make diagnostics         Collect diagnostic report\n'
	@printf '  make diagnostics-archive Collect diagnostic report and tar archive\n'
	@printf '  make daily-report        Print the daily operator report\n'
	@printf '  make weekly-report       Print the weekly operator report\n'
	@printf '  make weekly-archive      Print weekly report and write history archive\n'
	@printf '  make benchmark           Save a benchmark snapshot\n'
	@printf '  make benchmark-report    Print benchmark trend report\n'
	@printf '  make prometheus          Write Prometheus textfile metrics\n'
	@printf '  make export-history      Export JSONL history to SQLite\n'
	@printf '  make history-report      Export and summarize SQLite history\n'
	@printf '  make history-multi-node  Export and compare per-node SQLite history\n'
	@printf '  make history-archive     Export portable SQLite/JSONL history archive\n'
	@printf '  make upload-archive      Upload/copy archive; set ARCHIVE_SOURCE/TARGET\n'
	@printf '  make package             Build a portable release tarball\n'
	@printf '  make prune               Apply retention limits\n'
	@printf '  make validate            Validate config\n'
	@printf '  make recover-dry-run     Show manual recovery command without restart\n'
	@printf '  make recover             Run approved manual recovery when unhealthy\n'

onboard:
	@scripts/onboard_local.sh

bootstrap:
	@scripts/bootstrap_env.sh

proto-check:
	@scripts/check_generated_proto.sh

version:
	@$(PYTHON) watchtower.py --version

status:
	@./run_watchtower.sh

stream:
	@$(PYTHON) watchtower.py -c $(CONFIG) --stream-page

summary:
	@$(PYTHON) watchtower.py -c $(CONFIG) --summary

sync-report:
	@$(PYTHON) watchtower.py -c $(CONFIG) --sync-report

diagnostics-summary:
	@$(PYTHON) watchtower.py -c $(CONFIG) --diagnostics-summary

incident-report:
	@$(PYTHON) watchtower.py -c $(CONFIG) --incident-report

json:
	@$(PYTHON) watchtower.py -c $(CONFIG) --json

alert:
	@$(PYTHON) watchtower.py -c $(CONFIG) --alert

smoke:
	@scripts/smoke_test.sh

ci:
	@scripts/check_ci_status.sh

integrations:
	@scripts/check_integrations.sh

simulate-exporter-failure:
	@scripts/simulate_exporter_failure.sh

ensure-exporter:
	@scripts/ensure_prometheus_exporter.sh

diagnostics:
	@scripts/collect_diagnostics.sh

diagnostics-archive:
	@scripts/collect_diagnostics.sh --archive

daily-report:
	@./run_daily_report.sh

weekly-report:
	@./run_weekly_report.sh

weekly-archive:
	@./run_weekly_report.sh
	@scripts/export_history_sqlite.py --archive-dir state/history-archives --archive-label weekly-$$(date +%Y-%m-%d)

benchmark:
	@./run_benchmark_snapshot.sh

benchmark-report:
	@$(PYTHON) watchtower.py -c $(CONFIG) --benchmark-report

prometheus:
	@$(PYTHON) watchtower.py -c $(CONFIG) --prometheus

export-history:
	@scripts/export_history_sqlite.py

history-report:
	@scripts/export_history_sqlite.py --summary

history-multi-node:
	@scripts/export_history_sqlite.py --multi-node-summary \
		--daa-lag-warning "$(MULTI_NODE_DAA_LAG_WARNING)" \
		--block-lag-warning "$(MULTI_NODE_BLOCK_LAG_WARNING)" \
		--stale-node-minutes "$(MULTI_NODE_STALE_MINUTES)" \
		--peer-lag-warning "$(MULTI_NODE_PEER_LAG_WARNING)" \
		--processed-age-lag-warning "$(MULTI_NODE_PROCESSED_AGE_LAG_WARNING)"

history-archive:
	@scripts/export_history_sqlite.py --archive-dir state/history-archives

upload-archive:
	@if [ -z "$(ARCHIVE_TARGET)" ]; then printf 'Set ARCHIVE_TARGET=/path, file:///path, s3://bucket/prefix, or remote:path\n' >&2; exit 2; fi
	@scripts/upload_archive.sh --source "$(ARCHIVE_SOURCE)" --target "$(ARCHIVE_TARGET)"

package:
	@scripts/package_release.sh

prune:
	@$(PYTHON) watchtower.py -c $(CONFIG) --prune-state

validate:
	@$(PYTHON) watchtower.py -c $(CONFIG) --validate-config

recover-dry-run:
	@$(PYTHON) watchtower.py -c $(CONFIG) --recover --dry-run

recover:
	@$(PYTHON) watchtower.py -c $(CONFIG) --recover

force-recover-dry-run:
	@$(PYTHON) watchtower.py -c $(CONFIG) --recover --force-recover --dry-run

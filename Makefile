PYTHON ?= .venv/bin/python
CONFIG ?= config.json

.PHONY: help version status summary json alert smoke ci integrations diagnostics diagnostics-archive daily-report benchmark benchmark-report prometheus export-history prune validate recover-dry-run recover force-recover-dry-run

help:
	@printf 'Kaspa Node Watchtower operator commands\n'
	@printf '\n'
	@printf '  make version             Print watchtower version\n'
	@printf '  make status              Run the cron-style health check\n'
	@printf '  make summary             Print a one-shot health summary\n'
	@printf '  make json                Print the raw JSON health report\n'
	@printf '  make smoke               Run the local smoke test suite\n'
	@printf '  make ci                  Check latest GitHub Actions smoke run\n'
	@printf '  make integrations        Check exporter, Prometheus, Grafana, and CI\n'
	@printf '  make diagnostics         Collect diagnostic report\n'
	@printf '  make diagnostics-archive Collect diagnostic report and tar archive\n'
	@printf '  make daily-report        Print the daily operator report\n'
	@printf '  make benchmark           Save a benchmark snapshot\n'
	@printf '  make benchmark-report    Print benchmark trend report\n'
	@printf '  make prometheus          Write Prometheus textfile metrics\n'
	@printf '  make export-history      Export JSONL history to SQLite\n'
	@printf '  make prune               Apply retention limits\n'
	@printf '  make validate            Validate config\n'
	@printf '  make recover-dry-run     Show manual recovery command without restart\n'
	@printf '  make recover             Run approved manual recovery when unhealthy\n'

version:
	@$(PYTHON) watchtower.py --version

status:
	@./run_watchtower.sh

summary:
	@$(PYTHON) watchtower.py -c $(CONFIG) --summary

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

diagnostics:
	@scripts/collect_diagnostics.sh

diagnostics-archive:
	@scripts/collect_diagnostics.sh --archive

daily-report:
	@./run_daily_report.sh

benchmark:
	@./run_benchmark_snapshot.sh

benchmark-report:
	@$(PYTHON) watchtower.py -c $(CONFIG) --benchmark-report

prometheus:
	@$(PYTHON) watchtower.py -c $(CONFIG) --prometheus

export-history:
	@scripts/export_history_sqlite.py

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

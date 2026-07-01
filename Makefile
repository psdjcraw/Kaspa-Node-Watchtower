PYTHON ?= .venv/bin/python
CONFIG ?= config.json
ARCHIVE_SOURCE ?= state/history-archives
ARCHIVE_TARGET ?=
MULTI_NODE_DAA_LAG_WARNING ?= 120
MULTI_NODE_BLOCK_LAG_WARNING ?= 120
MULTI_NODE_STALE_MINUTES ?= 10
MULTI_NODE_PEER_LAG_WARNING ?= 2
MULTI_NODE_PROCESSED_AGE_LAG_WARNING ?= 60
MUTE_MINUTES ?= 30
MUTE_REASON ?= planned maintenance
MINING_ADDRESS ?=
TX_ID ?=
ADDRESS ?=
QUERY ?=
COMPOSE ?= docker compose
INDEXER_COMPOSE ?= integrations/simply-kaspa-indexer/docker-compose.yml
INDEXER_API ?= http://127.0.0.1:8500
DOCKER_IMAGE ?= psdjc/kaspa-node-watchtower
DOCKER_TAG ?= latest
SNS_QUERY ?= Kaspa KAS
AMOUNT_KAS ?= 0
MARKET_RISK_SCORE ?= 4
MARKET_RISK_REASON ?= market_risk_drill
MARKET_RISK_DIRECTION ?= mixed

.PHONY: help onboard bootstrap proto-check version status stream summary sync-report diagnostics-summary incident-report timeline json alert discord-status discord-incidents discord-timeline discord-wallet discord-wallet-txs discord-mining discord-whales discord-tx discord-address discord-balance discord-utxos discord-search discord-market discord-market-risk discord-market-drill discord-watch-list discord-watch-check discord-watch-drill discord-watch-add discord-watch-remove discord-watch-test market-risk-drill indexer-up indexer-down indexer-logs indexer-smoke mining-set-address mining-clear-address discord-maintenance discord-mute discord-mute-all discord-unmute maintenance-status mute mute-all unmute smoke ci integrations simulate-exporter-failure ensure-exporter launchd-status launchd-install launchd-restart launchd-uninstall diagnostics diagnostics-archive daily-report weekly-report weekly-archive benchmark benchmark-report prometheus export-history history-report history-multi-node history-archive upload-archive sns-refresh package docker-build docker-smoke docker-push prune validate recover-dry-run recover force-recover-dry-run

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
	@printf '  make timeline            Print unified operator event timeline\n'
	@printf '  make json                Print the raw JSON health report\n'
	@printf '  make discord-status      Print Discord-friendly node status\n'
	@printf '  make discord-incidents   Print Discord-friendly incident state\n'
	@printf '  make discord-timeline    Print Discord-friendly operator timeline\n'
	@printf '  make discord-wallet      Print Discord-friendly watch-only wallet balances\n'
	@printf '  make discord-wallet-txs  Print pending wallet txs and recorded wallet events\n'
	@printf '  make discord-mining      Print Discord-friendly miner monitor state\n'
	@printf '  make discord-whales      Print Discord-friendly 1M+ KAS whale events\n'
	@printf '  make discord-tx          Query indexer tx; set TX_ID=...\n'
	@printf '  make discord-address     Query indexer address; set ADDRESS=kaspa:...\n'
	@printf '  make discord-balance     Query indexer address balance; set ADDRESS=kaspa:...\n'
	@printf '  make discord-utxos       Query indexer address UTXOs; set ADDRESS=kaspa:...\n'
	@printf '  make discord-search      Search indexer; set QUERY=...\n'
	@printf '  make discord-market      Print Discord-friendly market snapshot\n'
	@printf '  make discord-market-risk Print Discord-friendly market risk state\n'
	@printf '  make discord-market-drill Inject synthetic Discord market risk drill\n'
	@printf '  make discord-watch-list  Print indexer watchlist\n'
	@printf '  make discord-watch-check Check live watch readiness\n'
	@printf '  make discord-watch-drill Inject synthetic watch event; optional ADDRESS/LABEL/TX_ID/AMOUNT_KAS\n'
	@printf '  make discord-watch-add   Add watch address; set ADDRESS=kaspa:... LABEL=...\n'
	@printf '  make discord-watch-remove Remove watch address; set ADDRESS=kaspa:...\n'
	@printf '  make discord-watch-test  Test watch address reads; set ADDRESS=kaspa:...\n'
	@printf '  make market-risk-drill   Inject synthetic market positioning risk metrics\n'
	@printf '  make indexer-up          Start local indexer stack; requires CONFIRM_INDEXER_UP=1\n'
	@printf '  make indexer-down        Stop local indexer compose stack\n'
	@printf '  make indexer-logs        Tail local indexer compose logs\n'
	@printf '  make indexer-smoke       Check local indexer API/admin endpoints\n'
	@printf '  make mining-set-address  Store payout address; set MINING_ADDRESS=kaspa:...\n'
	@printf '  make mining-clear-address Clear stored mining payout address\n'
	@printf '  make discord-mute        Discord bridge mute; set MUTE_MINUTES/REASON\n'
	@printf '  make maintenance-status  Print alert mute state\n'
	@printf '  make mute                Mute non-critical alerts; set MUTE_MINUTES/REASON\n'
	@printf '  make mute-all            Mute all alerts; set MUTE_MINUTES/REASON\n'
	@printf '  make unmute              Clear alert mute state\n'
	@printf '  make smoke               Run the local smoke test suite\n'
	@printf '  make ci                  Check latest GitHub Actions smoke run\n'
	@printf '  make integrations        Check exporter, Prometheus, Grafana, and CI\n'
	@printf '  make simulate-exporter-failure Verify exporter failure detection\n'
	@printf '  make ensure-exporter     Install/restart the Prometheus exporter LaunchAgent\n'
	@printf '  make launchd-status      Print managed LaunchAgent state\n'
	@printf '  make launchd-install     Install/restart managed LaunchAgents\n'
	@printf '  make launchd-restart     Reload managed LaunchAgents\n'
	@printf '  make launchd-uninstall   Remove managed LaunchAgents\n'
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
	@printf '  make sns-refresh         Fetch recent X/YouTube Kaspa social snapshot\n'
	@printf '  make package             Build a portable release tarball\n'
	@printf '  make docker-build        Build Docker image; set DOCKER_IMAGE/DOCKER_TAG\n'
	@printf '  make docker-smoke        Build image and run container version smoke\n'
	@printf '  make docker-push         Push Docker image to Docker Hub\n'
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

timeline:
	@$(PYTHON) watchtower.py -c $(CONFIG) --timeline

json:
	@$(PYTHON) watchtower.py -c $(CONFIG) --json

alert:
	@$(PYTHON) watchtower.py -c $(CONFIG) --alert

discord-status:
	@$(PYTHON) scripts/discord_command_handler.py -c $(CONFIG) status

discord-incidents:
	@$(PYTHON) scripts/discord_command_handler.py -c $(CONFIG) incidents

discord-timeline:
	@$(PYTHON) scripts/discord_command_handler.py -c $(CONFIG) timeline

discord-wallet:
	@$(PYTHON) scripts/discord_command_handler.py -c $(CONFIG) wallet

discord-wallet-txs:
	@$(PYTHON) scripts/discord_command_handler.py -c $(CONFIG) wallet-txs

discord-mining:
	@$(PYTHON) scripts/discord_command_handler.py -c $(CONFIG) mining

discord-whales:
	@$(PYTHON) scripts/discord_command_handler.py -c $(CONFIG) whales

discord-tx:
	@test -n "$(TX_ID)" || (printf 'TX_ID is required\n' >&2; exit 2)
	@$(PYTHON) scripts/discord_command_handler.py -c $(CONFIG) tx --query "$(TX_ID)"

discord-address:
	@test -n "$(ADDRESS)" || (printf 'ADDRESS is required\n' >&2; exit 2)
	@$(PYTHON) scripts/discord_command_handler.py -c $(CONFIG) address --query "$(ADDRESS)"

discord-balance:
	@test -n "$(ADDRESS)" || (printf 'ADDRESS is required\n' >&2; exit 2)
	@$(PYTHON) scripts/discord_command_handler.py -c $(CONFIG) balance --query "$(ADDRESS)"

discord-utxos:
	@test -n "$(ADDRESS)" || (printf 'ADDRESS is required\n' >&2; exit 2)
	@$(PYTHON) scripts/discord_command_handler.py -c $(CONFIG) utxos --query "$(ADDRESS)"

discord-search:
	@test -n "$(QUERY)" || (printf 'QUERY is required\n' >&2; exit 2)
	@$(PYTHON) scripts/discord_command_handler.py -c $(CONFIG) search --query "$(QUERY)"

discord-market:
	@$(PYTHON) scripts/discord_command_handler.py -c $(CONFIG) market

discord-market-risk:
	@$(PYTHON) scripts/discord_command_handler.py -c $(CONFIG) market-risk

discord-market-drill:
	@$(PYTHON) scripts/discord_command_handler.py -c $(CONFIG) market-drill --reason "$(MARKET_RISK_REASON)" --risk-score "$(MARKET_RISK_SCORE)" --direction "$(MARKET_RISK_DIRECTION)"

discord-watch-list:
	@$(PYTHON) scripts/discord_command_handler.py -c $(CONFIG) watch-list

discord-watch-check:
	@$(PYTHON) scripts/discord_command_handler.py -c $(CONFIG) watch-check

discord-watch-drill:
	@$(PYTHON) scripts/discord_command_handler.py -c $(CONFIG) watch-drill --query "$(ADDRESS)" --reason "$(LABEL)" --tx-id "$(TX_ID)" --amount-kas "$(AMOUNT_KAS)"

discord-watch-add:
	@test -n "$(ADDRESS)" || (printf 'ADDRESS is required\n' >&2; exit 2)
	@$(PYTHON) scripts/discord_command_handler.py -c $(CONFIG) watch-add --query "$(ADDRESS)" --reason "$(LABEL)"

discord-watch-remove:
	@test -n "$(ADDRESS)" || (printf 'ADDRESS is required\n' >&2; exit 2)
	@$(PYTHON) scripts/discord_command_handler.py -c $(CONFIG) watch-remove --query "$(ADDRESS)"

discord-watch-test:
	@test -n "$(ADDRESS)" || (printf 'ADDRESS is required\n' >&2; exit 2)
	@$(PYTHON) scripts/discord_command_handler.py -c $(CONFIG) watch-test --query "$(ADDRESS)" --reason "$(LABEL)"

market-risk-drill:
	@$(PYTHON) watchtower.py -c $(CONFIG) --market-risk-drill --market-risk-score "$(MARKET_RISK_SCORE)" --market-risk-reason "$(MARKET_RISK_REASON)" --market-risk-direction "$(MARKET_RISK_DIRECTION)"

indexer-up:
	@if [ "$(CONFIRM_INDEXER_UP)" != "1" ]; then printf 'Indexer is disabled in the lightweight operating mode. Re-run with CONFIRM_INDEXER_UP=1 only after reviewing docs/lightweight-indexer-mode.md\n' >&2; exit 2; fi
	@$(COMPOSE) -f $(INDEXER_COMPOSE) up -d --build

indexer-down:
	@$(COMPOSE) -f $(INDEXER_COMPOSE) down

indexer-logs:
	@$(COMPOSE) -f $(INDEXER_COMPOSE) logs -f --tail=200 kaspa_indexer

indexer-smoke:
	@scripts/check_indexer_api.sh "$(INDEXER_API)"

mining-set-address:
	@test -n "$(MINING_ADDRESS)" || (printf 'MINING_ADDRESS is required\n' >&2; exit 2)
	@$(PYTHON) watchtower.py -c $(CONFIG) --set-mining-address "$(MINING_ADDRESS)"

mining-clear-address:
	@$(PYTHON) watchtower.py -c $(CONFIG) --clear-mining-address

discord-maintenance:
	@$(PYTHON) scripts/discord_command_handler.py -c $(CONFIG) maintenance

discord-mute:
	@$(PYTHON) scripts/discord_command_handler.py -c $(CONFIG) mute --minutes "$(MUTE_MINUTES)" --reason "$(MUTE_REASON)"

discord-mute-all:
	@$(PYTHON) scripts/discord_command_handler.py -c $(CONFIG) mute-all --minutes "$(MUTE_MINUTES)" --reason "$(MUTE_REASON)"

discord-unmute:
	@$(PYTHON) scripts/discord_command_handler.py -c $(CONFIG) unmute

maintenance-status:
	@$(PYTHON) watchtower.py -c $(CONFIG) --maintenance-status

mute:
	@$(PYTHON) watchtower.py -c $(CONFIG) --mute-for "$(MUTE_MINUTES)" --maintenance-reason "$(MUTE_REASON)"

mute-all:
	@$(PYTHON) watchtower.py -c $(CONFIG) --mute-for "$(MUTE_MINUTES)" --maintenance-reason "$(MUTE_REASON)" --mute-all

unmute:
	@$(PYTHON) watchtower.py -c $(CONFIG) --unmute

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

launchd-status:
	@scripts/manage_launchd.sh status

launchd-install:
	@scripts/manage_launchd.sh --apply install

launchd-restart:
	@scripts/manage_launchd.sh --apply restart

launchd-uninstall:
	@scripts/manage_launchd.sh --apply uninstall

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

sns-refresh:
	@scripts/fetch_social_snapshot.py --query "$(SNS_QUERY)"

package:
	@scripts/package_release.sh

docker-build:
	@docker build -t "$(DOCKER_IMAGE):$(DOCKER_TAG)" .

docker-smoke: docker-build
	@docker run --rm "$(DOCKER_IMAGE):$(DOCKER_TAG)" --version

docker-push:
	@docker push "$(DOCKER_IMAGE):$(DOCKER_TAG)"

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

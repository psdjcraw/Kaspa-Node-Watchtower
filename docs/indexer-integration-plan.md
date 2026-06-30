# Indexer Integration Plan

This plan extends Kaspa Node Watchtower from a local node operations toolkit
into a Watchtower plus Indexer stack. The integration target is
`supertypo/simply-kaspa-indexer`, observed at upstream HEAD
`d97b9f486aa53a8c5ff5a7310cd8c46ffa7df23e`.

## Direction

Keep the current Python Watchtower as the operator layer:

- local `kaspad` health checks
- gRPC metric inspection
- log-derived progress and throughput signals
- Prometheus textfile export
- Grafana dashboard support
- Discord/OpenClaw command outputs
- local status HTML pages
- watch-only wallet, mining, whale, market, and incident reporting

Add `simply-kaspa-indexer` as the chain data layer:

- durable PostgreSQL block, transaction, address, script, and acceptance tables
- checkpointed chain indexing
- pruning and retention controls
- REST API expansion for explorer-like reads
- optional derived balance and UTXO tables
- indexer health, lag, and data freshness signals for Watchtower

The project should not replace `kaspad`, custody funds, sign transactions, or
require hosted explorers for core node and indexer status.

## Target Architecture

```text
kaspad
  | gRPC / logs / process / disk
  v
Kaspa Node Watchtower (Python operator layer)
  | status, alerts, Discord/OpenClaw, Prometheus, Grafana, local HTML
  |
  +--> simply-kaspa-indexer REST API
  |       health, metrics, blocks, transactions, addresses, balances, UTXOs
  |
  +--> PostgreSQL
          raw indexed chain tables
          derived watchtower tables
          optional retention/pruning
```

## Source Roles

| Component | Owns | Should Avoid |
| --- | --- | --- |
| Python Watchtower | operator summaries, alert policy, local dashboards, command bridge, incident state | full historical chain indexing |
| Rust indexer | high-throughput chain ingest, PostgreSQL schema, REST API, query performance | Discord-specific alert policy |
| PostgreSQL | durable chain and derived state | private keys, secrets, operator-only config |
| Grafana/Prometheus | visual and alerting surfaces | becoming the only source of truth |

## Milestones

### v0.9 - Indexer Awareness

Goal: Watchtower can see and report whether the Rust indexer is healthy.

Deliverables:

- Add optional `indexer` config block:
  - `enabled`
  - `base_url`
  - `postgres_url` or metrics-only mode
  - warning thresholds for lag, stale data, and API failures
- Poll indexer `/api/health` and `/api/metrics`.
- Export Prometheus metrics for indexer availability, lag, indexed block age,
  queue capacity, and PostgreSQL schema/version status when available.
- Normalize post-Toccata capability signals from `/api/metrics` when exposed:
  tx v1, `storageMass`, `computeBudget`, output covenant binding, UTXO
  covenant ID, user-lane `subnetwork_id`, gas commitments,
  `GetBlockRewardInfo`, and `GetSeqCommitLaneProof`. Missing metrics render as
  `unknown` so Watchtower can be deployed before the indexer schema catches up.
- Normalize post-Toccata fee/mass metrics when exposed: minimum relay fee in
  sompi/gram, tx v1 count, covenant output count, user-lane tx count, total gas,
  max/average `storageMass`, max compute/transient mass, and low-fee rejection
  count. The Watchtower baseline expects `100` sompi/gram after Toccata.
- Normalize post-Toccata activity counters when exposed: tx v1 count, block v2
  count, covenant tx/input/output/UTXO/ID counts, active user lanes, user-lane tx
  count, SeqCommit block count, and ZK precompile/Groth16/RISC0 tx counts.
- Add status page and summary sections for indexer health.
- Add alert rules for indexer API down, chain lag, stale checkpoint, and
  PostgreSQL unavailable.
- Add tests using mocked indexer responses.

Validation:

- `make validate`
- `make smoke`
- Prometheus rule tests
- mocked healthy, stale, and unavailable indexer unit tests

### v1.0 - Explorer API Baseline

Goal: The Rust indexer exposes the minimum read API needed by Watchtower,
Discord commands, and a small explorer UI.

Rust indexer API targets:

- `GET /api/blocks/recent`
- `GET /api/blocks/{hash}`
- `GET /api/transactions/{transaction_id}`
- `GET /api/addresses/{address}/transactions`
- `GET /api/search?q=...`
- `GET /api/status`

Watchtower integration targets:

- `make discord-tx TX_ID=...`
- `make discord-address ADDRESS=...`
- status page deep links to local tx, block, and address pages
- Prometheus metrics for API latency and error rate

Validation:

- Rust `cargo fmt`
- Rust `cargo test`
- endpoint tests against a small seeded PostgreSQL fixture
- Watchtower mocked command tests

### v1.1 - Watchlist and Alert Events

Goal: Watchtower can register durable watch targets backed by indexed data.

Deliverables:

- Add PostgreSQL tables for watch targets and watch events:
  - address watches
  - transaction watches
  - block watches
  - large transaction rules
  - indexer lag rules
- Add idempotent event creation so alerts do not duplicate across restarts.
- Add event history API and Watchtower summary rendering.
- Keep alert policy in Watchtower, not in the Rust ingest loop.

Validation:

- migration upgrade tests
- duplicate event suppression tests
- Discord/OpenClaw output snapshot tests

### v1.2 - Balance and UTXO Layer

Goal: Watchtower and explorer views can answer balance and spendable-output
questions without scanning transaction history at request time.

Deliverables:

- Add optional derived tables:
  - `address_balances`
  - `address_utxos`
  - `address_balance_events`
- Add APIs:
  - `GET /api/addresses/{address}/balance`
  - `GET /api/addresses/{address}/utxos`
  - `GET /api/addresses/{address}/events`
- Add reorg-safe update behavior and reconciliation checks.
- Keep this feature behind an explicit enable flag because it affects write
  load and storage growth.

Validation:

- balance reconciliation from seeded transactions
- reorg/acceptance change tests
- storage growth notes in operations docs

### v1.3 - Admin UI

Goal: Provide a local, operator-first UI for the combined Watchtower plus
Indexer stack.

Views:

- Overview
- Node health
- Indexer health
- PostgreSQL/storage
- Watchlists
- Alert history
- Recent blocks and transactions
- Address/transaction search
- Operations/runbook links

Implementation notes:

- Start as static HTML generated or served by Watchtower if possible.
- Move to a separate frontend only when static pages become a drag.
- Keep UI useful on a local network without third-party hosted dependencies.

### v1.4 - Explorer UI

Goal: Add a compact local explorer experience after the API and data model are
stable.

Views:

- block detail
- transaction detail
- address detail
- search results
- network stats
- recent activity

## First Implementation Slice

Start with v0.9 because it connects the existing Watchtower to the new indexer
without disturbing current node monitoring.

Tasks:

1. Add `indexer` defaults to `DEFAULT_CONFIG` and `config.example.json`.
2. Implement a small indexer HTTP client in `watchtower.py`.
3. Fold indexer checks into `report["checks"]`, `report["indexer"]`, and
   severity calculation.
4. Export Prometheus textfile metrics for indexer health and lag.
5. Render indexer state in `status.html` and `--summary`.
6. Add alert rules and unit tests.

Status: implemented in Watchtower. The current tree includes optional indexer
health/metrics polling, report checks, summary/status-page output, Prometheus
metrics, alert rules, config validation, mocked unit tests, and smoke coverage.
The tree also includes an `indexer_watch` layer that polls configured watched
addresses through the companion indexer address-transactions API, records
idempotent local events in Watchtower state, emits Watchtower alerts for new
events, and exports Prometheus watchlist metrics.

Local stack packaging is available through:

```bash
make indexer-up
make indexer-smoke
make indexer-logs
make indexer-down
```

The compose file lives at
`integrations/simply-kaspa-indexer/docker-compose.yml` and starts PostgreSQL
plus the sibling `simply-kaspa-indexer` checkout. By default it uses the
existing local mainnet kaspad wRPC Borsh endpoint
`ws://host.docker.internal:17110`; the bundled kaspad service is only started
when the `local-kaspad` profile is selected.

## Current Watchtower Commands

When `indexer.enabled=true`, Watchtower can query the companion indexer API:

```bash
make discord-tx TX_ID=...
make discord-address ADDRESS=kaspa:...
make discord-balance ADDRESS=kaspa:...
make discord-utxos ADDRESS=kaspa:...
make discord-search QUERY=...
make discord-watch-list
make discord-watch-add ADDRESS=kaspa:... LABEL=treasury
make discord-watch-remove ADDRESS=kaspa:...
make discord-watch-test ADDRESS=kaspa:... LABEL=treasury
```

Equivalent direct CLI options:

```bash
.venv/bin/python watchtower.py -c config.json --indexer-tx ...
.venv/bin/python watchtower.py -c config.json --indexer-address kaspa:...
.venv/bin/python watchtower.py -c config.json --indexer-balance kaspa:...
.venv/bin/python watchtower.py -c config.json --indexer-utxos kaspa:...
.venv/bin/python watchtower.py -c config.json --indexer-search ...
.venv/bin/python watchtower.py -c config.json --indexer-watch-list
.venv/bin/python watchtower.py -c config.json --indexer-watch-add kaspa:... --indexer-watch-label treasury
.venv/bin/python watchtower.py -c config.json --indexer-watch-remove kaspa:...
.venv/bin/python watchtower.py -c config.json --indexer-watch-test kaspa:... --indexer-watch-label treasury
```

The path templates live under the `indexer` config block so the Rust API can
evolve without changing the Watchtower command surface.

Enable address watch events with:

```json
"indexer_watch": {
  "enabled": true,
  "alert_enabled": true,
  "event_history_entries": 100,
  "watch_addresses": [
    {"label": "mining", "address": "kaspa:q..."}
  ]
}
```

Watchtower stores only public watch metadata and transaction ids; no signing,
private keys, wallet files, or seed phrases are used.

## Open Decisions

- Whether the Rust indexer should live as a git submodule, a documented
  companion service, or a vendored fork.
- Whether Watchtower should read PostgreSQL directly for some status checks or
  only talk to the Rust indexer API.
- Which address balance semantics are required first: accepted-only,
  mempool-aware, or both.
- Whether public explorer compatibility matters or the first target stays
  strictly local-operator use.

Default recommendation: keep `simply-kaspa-indexer` as a companion service
first, integrate over HTTP metrics/status, and only fork or vendor once the
needed API changes are clear.

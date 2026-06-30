# Watchtower Indexer API Spec

This document defines the REST fields Kaspa Node Watchtower expects from the
optional indexer integration. Watchtower is tolerant of older payloads and marks
missing fields as `unknown`, but new indexer work should prefer the canonical
camelCase names below.

## Endpoints

### `GET /api/health`

Purpose: fast liveness and readiness check.

Recommended response:

```json
{
  "status": "healthy",
  "version": "0.1.0",
  "kaspad": {
    "status": "up",
    "isSynced": true
  },
  "indexer": {
    "status": "ready",
    "checkpointDaaScore": 474165565,
    "details": []
  }
}
```

Watchtower treats `status` values `ok`, `healthy`, `ready`, and `up` as healthy.
It treats `alert`, `critical`, `down`, `error`, `failed`, and `unhealthy` as
unhealthy.

### `GET /api/metrics`

Purpose: operational metrics, indexer freshness, schema readiness, and
post-Toccata activity counters.

Recommended top-level response:

```json
{
  "version": "0.1.0",
  "schemaVersion": 1,
  "indexerLagSeconds": 0,
  "checkpoint": {
    "timestamp": "2026-06-30T16:20:00Z",
    "daaScore": 474165565
  },
  "toccata": {
    "txVersion1": true,
    "storageMass": true,
    "computeBudget": true,
    "covenantBinding": true,
    "utxoCovenantId": true,
    "subnetworkId": true,
    "gas": true,
    "getBlockRewardInfo": true,
    "getSeqCommitLaneProof": true,
    "minimumRelayFeeSompiPerGram": 100,
    "txV1Count": 0,
    "blockV2Count": 0,
    "covenantTxCount": 0,
    "covenantInputCount": 0,
    "covenantOutputCount": 0,
    "covenantUtxoCount": 0,
    "covenantIdCount": 0,
    "activeUserLanes": 0,
    "userLaneTxCount": 0,
    "gasTotal": 0,
    "seqCommitBlockCount": 0,
    "storageMassMax": 0,
    "storageMassAvg": 0,
    "computeMassMax": 0,
    "transientMassMax": 0,
    "lowFeeRejections": 0,
    "zkPrecompileTxCount": 0,
    "groth16TxCount": 0,
    "risc0TxCount": 0,
    "tokenCandidateCount": 0,
    "nftCandidateCount": 0,
    "topCovenants": [
      {
        "covenantId": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
        "txCount": 0,
        "utxoCount": 0,
        "inputCount": 0,
        "outputCount": 0,
        "tokenLike": false,
        "nftLike": false,
        "latestTxId": null
      }
    ]
  }
}
```

## Freshness Fields

- `version`: indexer application version.
- `schemaVersion`: PostgreSQL/API schema version.
- `indexerLagSeconds`: lag between indexed chain data and the node tip.
- `checkpoint.timestamp`: timestamp of the latest indexed checkpoint or block.

Watchtower also accepts `lagSeconds`, `lag_seconds`, `schema_version`,
`blockTime`, and `block_time` for compatibility.

## Toccata Schema Capability Fields

These booleans tell Watchtower whether the indexer preserves and exposes the new
post-Toccata fields.

- `txVersion1`
- `storageMass`
- `computeBudget`
- `covenantBinding`
- `utxoCovenantId`
- `subnetworkId`
- `gas`
- `getBlockRewardInfo`
- `getSeqCommitLaneProof`

Boolean `true` means supported, `false` means missing, and omitted means
unknown.

## Fee And Mass Fields

These counters feed the Watchtower Toccata Fee/Mass Monitor.

- `minimumRelayFeeSompiPerGram`: expected to be `100` after Toccata.
- `txV1Count`: indexed version 1 transaction count in the chosen metrics window.
- `covenantOutputCount`: indexed covenant-bound output count.
- `userLaneTxCount`: indexed user-lane transaction count.
- `gasTotal`: total gas committed by user-lane transactions.
- `storageMassMax`: maximum observed transaction `storageMass`.
- `storageMassAvg`: average observed transaction `storageMass`.
- `computeMassMax`: maximum observed compute mass.
- `transientMassMax`: maximum observed transient mass.
- `lowFeeRejections`: transactions rejected by the indexer/node policy because
  fees were below the relay policy.

Use `0` for a supported metric with no activity. Omit only when the indexer does
not yet know how to compute the metric.

## Post-Toccata Activity Fields

These counters feed the Watchtower Post-Toccata Tx Activity panel.

- `txV1Count`
- `blockV2Count`
- `covenantTxCount`
- `covenantInputCount`
- `covenantOutputCount`
- `covenantUtxoCount`
- `covenantIdCount`
- `activeUserLanes`
- `userLaneTxCount`
- `seqCommitBlockCount`
- `zkPrecompileTxCount`
- `groth16TxCount`
- `risc0TxCount`

The recommended window is "since indexer start or current retention window" for
simple counters. If the indexer later exposes time-windowed counters, keep these
canonical names for the default aggregate and add explicit suffixes such as
`txV1Count24h`.

## Covenant Explorer Fields

These fields feed the Watchtower Covenant Explorer baseline. They are observer
signals only; Watchtower does not infer an official token standard from them.

- `tokenCandidateCount`: number of covenant IDs the indexer marks as
  fungible-token-like.
- `nftCandidateCount`: number of covenant IDs the indexer marks as NFT-like.
- `topCovenants`: ordered list of the most active covenant IDs.

Each `topCovenants` item should use:

- `covenantId`: 32-byte covenant ID as hex.
- `txCount`: indexed transaction count involving the covenant.
- `utxoCount`: current or indexed UTXO count for the covenant.
- `inputCount`: indexed covenant input count.
- `outputCount`: indexed covenant output count.
- `tokenLike`: boolean heuristic for fungible-token-like behavior.
- `nftLike`: boolean heuristic for NFT-like behavior.
- `latestTxId`: latest transaction ID observed for this covenant, or `null`.

Recommended ordering is descending `txCount`, then descending `utxoCount`.
Limit the list to a small top-N set such as 20 items.

## Watchtower Interpretation

- Missing fields render as `unknown`.
- Present numeric `0` renders as observed but inactive.
- Positive numeric values render as active.
- `minimumRelayFeeSompiPerGram < 100` marks the fee/mass monitor as warning.
- `lowFeeRejections > 0` marks the fee/mass monitor as warning.

## Compatibility

Watchtower accepts several snake_case aliases to avoid breaking older prototypes,
but new producers should emit the canonical camelCase fields in this document.

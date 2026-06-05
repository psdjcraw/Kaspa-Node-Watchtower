# Development Context

Kaspa Node Watchtower started from a local `rusty-kaspa` testnet node run.

Observed node context:

- Binary: `kaspad`
- Network: testnet 10
- RPC: `127.0.0.1:16210`
- P2P: `0.0.0.0:16211`
- Data directory: `/Users/psdjc/kaspa/rusty-kaspa-tn10-data/kaspa-testnet-10/datadir`
- Log file: `/Users/psdjc/kaspa/rusty-kaspa-tn10-data/kaspa-testnet-10/logs/rusty-kaspa.log`

Initial sync observations:

- Sustained session started around `2026-06-04 19:11:22 +09:00`
- First major IBD completed at `2026-06-05 01:43:34 +09:00`
- Latest relay mode was reached around `2026-06-05 04:49:10 +09:00`
- IBD/catch-up block body total from completion logs: `1,696,756`
- Trusted blocks processed separately: `26,446`
- Data directory size observed around `68G`

Important interpretation:

The node did not download every historical block body from genesis. It used
pruning point proof, SMT state, and UTXO set sync, then caught up from the
pruning point. This is why processed IBD block bodies can be much smaller than
total blocks ever produced by the network.

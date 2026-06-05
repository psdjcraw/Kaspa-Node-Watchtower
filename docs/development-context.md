# Development Context

Kaspa Node Watchtower started from a local `rusty-kaspa` testnet node run and
was later switched to the mainnet Toccata release.

Current node context:

- Binary: `/Users/psdjc/kaspa/rusty-kaspa-v2.0.0/bin/kaspad`
- Version: `kaspad 2.0.0`
- Network: mainnet
- RPC: `127.0.0.1:16110`
- P2P: `0.0.0.0:16111`
- Data directory: `/Users/psdjc/kaspa/rusty-kaspa-mainnet-data/kaspa-mainnet/datadir`
- Log file: `/Users/psdjc/kaspa/rusty-kaspa-mainnet-data/kaspa-mainnet/logs/rusty-kaspa.log`
- Launchd label: `com.openclaw.kaspad-mainnet`

Mainnet switch:

- Release: `Mainnet Toccata Release - v2.0.0`
- Started locally at `2026-06-05 21:36:46 +09:00`
- Testnet launchd label `com.openclaw.kaspad-tn10` was stopped, but testnet
  data was preserved.

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

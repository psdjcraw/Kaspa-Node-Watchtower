## Summary

- 

## Area

- [ ] Health checks
- [ ] gRPC metrics
- [ ] Prometheus/Grafana
- [ ] Alerts
- [ ] Recovery
- [ ] Benchmarks/history
- [ ] Documentation
- [ ] Repository operations

## Checks

- [ ] `python3 -m unittest discover -s tests`
- [ ] `prometheus/run_rule_tests.sh`
- [ ] `make smoke`
- [ ] `make integrations`

## Operator Impact

Describe any alert, recovery, dashboard, cron, launchd, or local configuration
impact. If this changes behavior on a live node, include the exact manual
verification performed.

## Sensitive Data

- [ ] No credentials, wallet data, webhook URLs, SSH keys, private hostnames, or
      unsanitized logs are included.

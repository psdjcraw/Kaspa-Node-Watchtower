# Integrations

## ASUS Traffic Monitor Stack

The local `asus-traffic-monitor` Docker stack is the current Prometheus/Grafana
consumer for Kaspa Watchtower metrics.

Current local endpoints:

- Watchtower exporter: `http://127.0.0.1:9660/metrics`
- Prometheus: `http://127.0.0.1:9090`
- Grafana dashboard: `http://127.0.0.1:3000/d/kaspa-watchtower/kaspa-watchtower`

## Prometheus Scrape

Add the scrape job from:

```text
integrations/asus-traffic-monitor/prometheus-scrape.yml
```

The active local target is:

```text
host.docker.internal:9660
```

## Prometheus Rules

Add the rule file stanza from:

```text
integrations/asus-traffic-monitor/prometheus-rule-files.yml
```

Mount the rules directory as shown in:

```text
integrations/asus-traffic-monitor/docker-compose-prometheus-rules.yml
```

Copy the rule file into the mounted rules directory:

```bash
mkdir -p /Users/psdjc/.openclaw/workspace/asus-traffic-monitor/prometheus-rules
cp prometheus/kaspa-watchtower-rules.yml \
  /Users/psdjc/.openclaw/workspace/asus-traffic-monitor/prometheus-rules/kaspa-watchtower-rules.yml
```

Validate inside the Prometheus container:

```bash
cd /Users/psdjc/.openclaw/workspace/asus-traffic-monitor
docker compose exec -T prometheus promtool check config /etc/prometheus/prometheus.yml
docker compose exec -T prometheus promtool check rules /etc/prometheus/rules/kaspa-watchtower-rules.yml
```

## Grafana Dashboard

Dashboard JSON:

```text
grafana/kaspa-watchtower.json
```

For the current local stack, copy it to:

```text
/Users/psdjc/.openclaw/workspace/asus-traffic-monitor/grafana/dashboards/kaspa-watchtower.json
```

Then restart Grafana:

```bash
cd /Users/psdjc/.openclaw/workspace/asus-traffic-monitor
docker compose restart grafana
```

## Verification

Run the integration check:

```bash
scripts/check_integrations.sh
```

It verifies:

- Watchtower exporter `/metrics`
- Prometheus scrape/query
- Prometheus target health
- Prometheus alert rules
- Grafana dashboard URL

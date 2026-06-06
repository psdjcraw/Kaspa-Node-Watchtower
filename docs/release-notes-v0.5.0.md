# Kaspa Node Watchtower v0.5.0 Release Notes

Release date: 2026-06-06

v0.5.0 focuses on distribution follow-up and a richer local operator
dashboard: install planning, portable archive upload paths, multi-node history
comparison, incident reports, and live market context for KAS/USDT.

## Highlights

- Homebrew formula draft for macOS operator install planning.
- Optional archive copy/upload helper for local, S3, and rclone-backed flows.
- Multi-node SQLite history comparison for operators running more than one
  `kaspad` host.
- Sanitized Markdown incident report export for escalation and review.
- `config_version` validation for future config migration checks.
- Live KAS/USDT market watch with Bybit spot data and 15-minute, 4-hour, daily,
  weekly, and monthly candle charts.
- Normalized daily KAS/USDT vs BTC/USDT cross chart with red KAS and blue BTC
  lines, plus latest normalized change values.
- Market API timeout/retry handling with browser cache fallback.
- Status dashboard visualizations for block processing, relay intake, and
  mempool activity.

## Operator Commands

```bash
make upload-archive
make history-multi-node
make incident-report
make package
```

## Verification Checklist

Before tagging v0.5.0, verify:

```bash
make version
python3 -m unittest discover -s tests
make smoke
make integrations
make package
```

GitHub Actions smoke and CodeQL checks are expected to pass on the release tag.

## Known Limitations

- Market data is dashboard context only; core node health remains local-first
  and does not depend on Bybit or hosted market APIs.
- Browser cache fallback is client-side and per-browser.
- Homebrew packaging is still a formula draft and does not replace host-specific
  `config.json` setup.
- Archive uploads are operator-triggered copy/upload helpers, not a managed
  retention service.

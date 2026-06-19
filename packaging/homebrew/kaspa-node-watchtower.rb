# frozen_string_literal: true

# Homebrew formula for Kaspa Node Watchtower.
class KaspaNodeWatchtower < Formula
  desc "Local-first operator toolkit for monitoring self-hosted Kaspa nodes"
  homepage "https://github.com/psdjcraw/Kaspa-Node-Watchtower"
  url "https://github.com/psdjcraw/Kaspa-Node-Watchtower/releases/download/v0.8.0/kaspa-node-watchtower-0.8.0-aba3228.tar.gz"
  version "0.8.0"
  sha256 "eb8213f4eab9ba94ec161681ad065c13301a62e9aa94183638562f3699c6061e"
  license "Apache-2.0"

  depends_on "python@3.12"

  def install
    libexec.install Dir["*"]
    (bin/"kaspa-watchtower").write <<~EOS
      #!/bin/bash
      exec "#{Formula["python@3.12"].opt_bin}/python3.12" "#{libexec}/watchtower.py" "$@"
    EOS
    chmod 0755, bin/"kaspa-watchtower"
  end

  def caveats
    <<~EOS
      Check the installed CLI:
        kaspa-watchtower --version

      Create a local config before running the watchtower:
        cp #{libexec}/config.example.json ./config.json
        kaspa-watchtower -c ./config.json --validate-config

      For full operator smoke, launchd service management, Prometheus/Grafana
      files, and wrapper scripts, use a source checkout:
        git clone #{homepage}
        cd Kaspa-Node-Watchtower
        make bootstrap
        make onboard
        make validate
        make smoke
    EOS
  end

  test do
    assert_match version.to_s, shell_output("#{bin}/kaspa-watchtower --version")
  end
end

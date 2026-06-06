class KaspaNodeWatchtower < Formula
  desc "Local-first operator toolkit for monitoring self-hosted Kaspa nodes"
  homepage "https://github.com/psdjcraw/Kaspa-Node-Watchtower"
  url "https://github.com/psdjcraw/Kaspa-Node-Watchtower/releases/download/v0.6.0/kaspa-node-watchtower-0.6.0-88e4873.tar.gz"
  version "0.6.0"
  sha256 "d980766a6b8b4198ab90a725fd2d427a092e924c6bf58e4954d3250a14bfe102"
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
      Create a local config before running the watchtower:
        cp #{libexec}/config.example.json ./config.json
        kaspa-watchtower -c ./config.json --validate-config

      Source checkout remains recommended when using bundled launchd,
      Prometheus, Grafana, or recovery wrapper scripts directly.
    EOS
  end

  test do
    assert_match version.to_s, shell_output("#{bin}/kaspa-watchtower --version")
  end
end

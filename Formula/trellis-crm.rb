class TrellisCrm < Formula
  desc "Run Trellis, the Sage.is CRM, on this Mac: server mode, backups and dev mode"
  homepage "https://github.com/Sage-is/homebrew-apps"
  url "https://github.com/Sage-is/homebrew-apps/archive/refs/tags/trellis-crm-v0.1.0.tar.gz"
  version "0.1.0"
  sha256 "0000000000000000000000000000000000000000000000000000000000000000"
  license "MIT"

  head "https://github.com/Sage-is/homebrew-apps.git", branch: "develop"

  depends_on "colima"
  depends_on "docker"
  depends_on "docker-credential-helper"
  depends_on :macos

  def install
    # A checkout runs the latest image; an install runs the image of its own version.
    inreplace "trellis-crm", 'DEFAULT_TAG="latest"', "DEFAULT_TAG=\"#{version}\""
    bin.install "trellis-crm"
  end

  def caveats
    <<~EOS
      The Trellis image is private. Sign in to GitHub's registry once, with a
      token that can only read packages (scope read:packages):
        docker login ghcr.io

      Then start it (settings and secrets go to ~/.sage-is/trellis-crm.env):
        trellis-crm start

      On a Mac that stays on, start Colima at power-on:
        trellis-crm boot

      Dev mode runs a checkout beside the real Trellis, on synthetic data,
      reachable from this Mac only. It clones the private repo over SSH, so
      the Mac needs a read-only deploy key for Sage-is/trellis:
        trellis-crm dev
    EOS
  end

  test do
    assert_match(/^trellis-crm #{version} /, shell_output("#{bin}/trellis-crm version"))
    assert_match "dev    [--dir DIR]", shell_output("#{bin}/trellis-crm --help")
  end
end

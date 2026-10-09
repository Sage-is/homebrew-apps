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

  # krunkit, the default Colima VM on Apple Silicon, refuses Intel. Homebrew 7
  # neither taps nor trusts a dependency's tap, and krunkit's own dependencies
  # come from that tap too: tap and trust libkrun/krun before installing.
  on_arm do
    depends_on "libkrun/krun/krunkit"
  end

  def install
    # A checkout runs the latest image; an install runs the image of its own
    # version. A --HEAD install runs latest too: HEAD-<commit> tags no image.
    inreplace "trellis-crm", 'DEFAULT_TAG="latest"', "DEFAULT_TAG=\"#{version}\"" unless build.head?
    # trellis-crm sources the runtime code it shares with sage-runtime and
    # ai-ui from lib/ beside it.
    libexec.install "trellis-crm"
    (libexec/"lib").install "lib/sage-runtime.sh"
    bin.write_exec_script libexec/"trellis-crm"
  end

  def caveats
    <<~EOS
      The Trellis image is private. Sign in to GitHub's registry once, with a
      token that can only read packages (scope read:packages):
        docker login ghcr.io

      Then start it (settings and secrets go to ~/.sage-is/trellis-crm.env):
        trellis-crm start

      On Apple Silicon, trellis-crm depends on krunkit. krunkit and its
      libraries come from the libkrun/krun tap, and Homebrew installs them
      only after you tap and trust it:
        brew tap libkrun/krun && brew trust libkrun/krun && brew install krunkit
      A first start with no Colima VM makes a krunkit VM, which gives the
      memory it frees back to macOS. That start adds two settings to
      Lima's override file and prints its path,
      ~/.colima/_lima/_config/override.yaml by default:
      "mountType: virtiofs" (abiosoft/colima#1607), and a boot step that
      mounts the VM's data disk, where Docker keeps Trellis's image and
      record (abiosoft/colima#1614). Every Colima VM on this Mac reads that
      file, so a QEMU Colima profile will not start. While Docker runs on
      the VM's root disk instead, trellis-crm stops and prints the repair.

      On a Mac that stays on, start Colima at power-on:
        trellis-crm boot

      Dev mode runs a checkout beside the real Trellis, on synthetic data,
      reachable from this Mac only. It clones the private repo over SSH, so
      the Mac needs a read-only deploy key for Sage-is/trellis:
        trellis-crm dev
    EOS
  end

  test do
    tag = build.head? ? "latest" : version
    assert_match(/^trellis-crm #{tag} \(image \S+:#{tag}\)$/, shell_output("#{bin}/trellis-crm version"))
    assert_match "dev    [--dir DIR]", shell_output("#{bin}/trellis-crm --help")
  end
end

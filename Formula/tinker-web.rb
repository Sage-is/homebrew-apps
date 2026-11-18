class TinkerWeb < Formula
  desc "Run web Tinker on this Mac: type a sentence in a browser tab and an app appears"
  homepage "https://github.com/Sage-is/tinker-web"
  url "https://github.com/Sage-is/homebrew-apps/archive/refs/tags/tinker-web-v0.1.0.tar.gz"
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
    inreplace "tinker-web", 'DEFAULT_TAG="latest"', "DEFAULT_TAG=\"#{version}\"" unless build.head?
    # tinker-web sources the runtime code it shares with sage-runtime and
    # ai-ui from lib/ beside it.
    libexec.install "tinker-web"
    (libexec/"lib").install "lib/sage-runtime.sh"
    bin.write_exec_script libexec/"tinker-web"
  end

  def caveats
    <<~EOS
      Web Tinker listens on this Mac's loopback only: upstream says never to
      put it on a public address. From another machine, tunnel in:
        tinker-web tunnel HOST

      Start it, then make invite codes. Nobody signs up without one, and
      they print only once:
        tinker-web start
        tinker-web invite

      Without a node token the page serves, but nothing new can be built.
      Settings, the token among them, go to ~/.sage-is/tinker-web.env.
      To make an AI-UI its node:
        tinker-web link-ai-ui URL

      On Apple Silicon, tinker-web depends on krunkit. krunkit and its
      libraries come from the libkrun/krun tap, and Homebrew installs them
      only after you tap and trust it:
        brew tap libkrun/krun && brew trust libkrun/krun && brew install krunkit

      The image is public and AGPL-3.0-only, built unmodified from a pinned
      upstream commit; its source is https://github.com/Sage-is/tinker-web.
    EOS
  end

  test do
    # The tool's own version, then the image tag: the formula's version once
    # installed, latest from a --HEAD install.
    tag = build.head? ? "latest" : version
    out = shell_output("#{bin}/tinker-web version")
    assert_match(/^tinker-web \d+\.\d+\.\d+ \(image \S+:#{tag}\)$/, out)
    assert_match "tinker-web #{version} ", out unless build.head?
    assert_match "tunnel HOST", shell_output("#{bin}/tinker-web --help")
  end
end

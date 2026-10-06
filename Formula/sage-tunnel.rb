class SageTunnel < Formula
  include Language::Python::Shebang

  desc "Cloudflare tunnels with fixed hostnames; connector tokens never reach the screen"
  homepage "https://github.com/Sage-is/homebrew-apps"
  url "https://github.com/Sage-is/homebrew-apps/archive/refs/tags/sage-tunnel-v0.1.0.tar.gz"
  version "0.1.0"
  sha256 "0000000000000000000000000000000000000000000000000000000000000000"
  license "MIT"

  head "https://github.com/Sage-is/homebrew-apps.git", branch: "develop"

  depends_on "python@3.13"

  def install
    rewrite_shebang detected_python_shebang, "sage-tunnel"
    bin.install "sage-tunnel"
    man1.install "sage-tunnel.1"
  end

  test do
    assert_match(/^sage-tunnel \d+\.\d+\.\d+$/, shell_output("#{bin}/sage-tunnel version").strip)
    assert_path_exists man1/"sage-tunnel.1"
  end
end

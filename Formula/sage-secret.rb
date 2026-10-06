class SageSecret < Formula
  include Language::Python::Shebang

  desc "Run commands with secrets from Bitwarden without writing or showing them"
  homepage "https://github.com/Sage-is/homebrew-apps"
  url "https://github.com/Sage-is/homebrew-apps/archive/refs/tags/sage-secret-v0.1.0.tar.gz"
  version "0.1.0"
  sha256 "0000000000000000000000000000000000000000000000000000000000000000"
  license "MIT"

  head "https://github.com/Sage-is/homebrew-apps.git", branch: "develop"

  depends_on "bitwarden-cli"
  depends_on "python@3.13"

  def install
    rewrite_shebang detected_python_shebang, "sage-secret"
    bin.install "sage-secret"
    man1.install "sage-secret.1"
  end

  test do
    assert_match(/^sage-secret \d+\.\d+\.\d+$/, shell_output("#{bin}/sage-secret version").strip)
    assert_path_exists man1/"sage-secret.1"
  end
end

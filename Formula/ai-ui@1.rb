class AiUiAT1 < Formula
  desc "One-command local deployment of Sage AI UI via Docker"
  homepage "https://github.com/Sage-is/AI-UI"
  url "https://github.com/Sage-is/homebrew-apps/archive/refs/tags/v1.0.0.tar.gz"
  sha256 "ae048a51ad9ff3c9797921aa0973dee7bddd123215fed3cf0c636f1325f43fbb"
  license "MIT"
  keg_only :versioned_formula

  depends_on "docker"
  depends_on "ollama"

  def install
    bin.install "ai-ui"
  end

  def caveats
    <<~EOS
      Start Sage AI UI:
        ai-ui start

      For local LLM inference, start the Ollama service:
        brew services start ollama

      Configure LLM backends in the admin UI:
        ai-ui open → Admin > Settings > Connections
    EOS
  end

  test do
    assert_match "Usage:", shell_output("#{bin}/ai-ui --help")
  end
end

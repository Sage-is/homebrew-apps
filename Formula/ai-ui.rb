class AiUi < Formula
  desc "One-command local deployment of Sage AI UI via Docker"
  homepage "https://github.com/Sage-is/AI-UI"
  url "https://github.com/Sage-is/homebrew-apps/archive/refs/tags/v0.1.2.tar.gz"
  sha256 "cb10b5ab67b09357069c3764e282ee972a36c4b4c2f7566f5c699889f6f94ac7"
  license "MIT"

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

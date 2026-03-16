class AiUi < Formula
  desc "One-command local deployment of Sage AI UI via Podman"
  homepage "https://github.com/Sage-is/AI-UI"
  url "https://github.com/Sage-is/homebrew-apps/archive/refs/tags/v0.1.0.tar.gz"
  sha256 "" # TODO: fill after first release tag
  license "MIT"

  depends_on "podman"
  depends_on "ollama"

  def install
    bin.install "ai-ui"
  end

  def post_install
    system Formula["ollama"].opt_bin/"ollama", "pull", "phi"
  end

  def caveats
    <<~EOS
      To start Sage AI UI:
        ai-ui start

      To configure LLM backends (Ollama, OpenAI, etc.), open the admin UI:
        ai-ui open
      Then go to Admin > Settings > Connections.

      Ollama is installed as a dependency for local LLM inference, along with the 'phi' model.
    EOS
  end

  test do
    assert_match "Usage:", shell_output("#{bin}/ai-ui --help")
  end
end

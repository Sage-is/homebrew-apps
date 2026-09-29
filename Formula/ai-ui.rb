class AiUi < Formula
  desc "One-command local deployment of Sage AI UI via Docker"
  homepage "https://github.com/Sage-is/AI-UI"
  url "https://github.com/Sage-is/homebrew-apps/archive/refs/tags/v1.0.4.tar.gz"
  sha256 "69da3959ae37d910fddd49f46c8ca6622f8490e743dd08f3f3d2677434550e03"
  license "MIT"

  depends_on "docker"
  depends_on "ollama"

  def install
    # The distribution.env beside the script pins the server version this
    # release was tested with (its SERVER_TAG); `ai-ui version` shows it.
    libexec.install "ai-ui", "distribution.env"
    bin.write_exec_script libexec/"ai-ui"
  end

  def caveats
    <<~EOS
      Start Sage AI UI (pulls the server version this release pins; `ai-ui version` shows it):
        ai-ui start

      Pin a specific server version:
        ai-ui start --tag 2.3.1

      Or boot a try.sage trial (seeded personas, hidden LLM, 24h auto-reset):
        ai-ui try
      First run prompts for a Groq API key (free tier at https://console.groq.com).
      Saved chmod 600 to ~/.sage-is/try.env.

      Move to a new AI-UI release:
        brew upgrade ai-ui   # each AI-UI release ships a CLI release that pins it
        ai-ui update         # pulls that server version and restarts; data stays in the volume

      For local LLM inference, start the Ollama service:
        brew services start ollama

      Configure LLM backends in the admin UI:
        ai-ui open → Admin > Settings > Connections

      Data lives in the `sage-ai-data` Docker volume (`sage-try-data` for trial).
      Nothing leaves the volume unless you back it up.
    EOS
  end

  test do
    assert_match "Usage:", shell_output("#{bin}/ai-ui --help")
    # The pinned server version came from distribution.env, not the `latest` fallback.
    assert_match(/server \d+\.\d+\.\d+/, shell_output("#{bin}/ai-ui version"))
  end
end

class AiUiAT3 < Formula
  desc "One-command local deployment of Sage AI UI via Docker"
  homepage "https://github.com/Sage-is/AI-UI"
  url "https://github.com/Sage-is/homebrew-apps/archive/refs/tags/v3.2.0.tar.gz"
  sha256 "69da3959ae37d910fddd49f46c8ca6622f8490e743dd08f3f3d2677434550e03"
  license "MIT"
  keg_only :versioned_formula

  depends_on "docker"
  depends_on "ollama"

  def install
    libexec.install "ai-ui"
    # From 3.x the CLI's version is the AI-UI version it pins, read from the
    # distribution.env beside it. A 1.x tarball's copy names an older server.
    if version.major.to_i >= 3
      server_tag = File.read("distribution.env", encoding: "UTF-8")[/^SERVER_TAG=(\S+)$/, 1]
      odie "distribution.env pins AI-UI #{server_tag}, not #{version}" if server_tag != version.to_s
      libexec.install "distribution.env"
    end
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
    assert_match "(server #{version})", shell_output("#{bin}/ai-ui version") if version.major.to_i >= 3
  end
end

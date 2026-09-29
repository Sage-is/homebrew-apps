class AiUiAT3 < Formula
  desc "One-command local deployment of Sage AI UI via Docker"
  homepage "https://github.com/Sage-is/AI-UI"
  url "https://github.com/Sage-is/homebrew-apps/archive/refs/tags/v3.2.0_1.tar.gz"
  version "3.2.0"
  sha256 "c26f93cf8058e5706d86ffe1711c1470b2ca331b0aa4d915990a5103fccec0eb"
  license "MIT"
  revision 1
  keg_only :versioned_formula

  depends_on "docker"
  depends_on "ollama"

  on_macos do
    depends_on "colima"
  end

  def install
    libexec.install "ai-ui"
    # `ai-ui nuke` runs the nuke-sage beside it, in scripts/.
    (libexec/"scripts").install "scripts/nuke-sage"
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
      Start Sage AI UI at the server version this CLI pins (`ai-ui version` shows it).
      On a Mac it runs in Colima: no window, no sign-in.
        ai-ui start

      Or keep a runtime this Mac already uses; ai-ui remembers the choice:
        ai-ui start --runtime docker-desktop
        ai-ui start --runtime orbstack

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

      Data lives in the `sage-ai-data` volume of the runtime you use (`sage-try-data` for trial).
      Nothing leaves the volume unless you back it up.
    EOS
  end

  test do
    help = shell_output("#{bin}/ai-ui --help")
    assert_match "Usage:", help
    assert_match "--runtime NAME", help
    assert_match "Usage: nuke-sage", shell_output("#{bin}/ai-ui nuke --help")
    assert_match "(server #{version})", shell_output("#{bin}/ai-ui version") if version.major.to_i >= 3
  end
end

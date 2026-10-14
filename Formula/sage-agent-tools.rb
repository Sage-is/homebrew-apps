class SageAgentTools < Formula
  desc "Sage.is agent skills and rules for Claude Code, OpenCode and Codex"
  homepage "https://github.com/Sage-is/agent-tools"
  url "https://github.com/Sage-is/agent-tools/archive/refs/tags/v0.1.0.tar.gz"
  version "0.1.0"
  sha256 "a31671762c8e8d50b3060c3bb946bebf4a6796066d06d14bda913f3f30b8111b"
  license "MIT"

  head "https://github.com/Sage-is/agent-tools.git", branch: "develop"

  depends_on "jq" # the installer edits opencode.json and settings.json with it

  def install
    bin.install "bin/sage-agent-tools"
    pkgshare.install "skills", "rules"
  end

  def caveats
    <<~EOS
      Link the skills into ~/.claude/skills and ~/.agents/skills and register
      them with OpenCode, then assemble the shared rules block in ~/.claude/CLAUDE.md:
        sage-agent-tools install
        sage-agent-tools rules install

      One route per machine: this installer or the repo's Claude Code plugin,
      not both. `sage-agent-tools install` stops while the plugin is active.

      Before `brew uninstall sage-agent-tools`, put your own copies back:
        sage-agent-tools uninstall

      Check the links, the prefix and the tools on PATH:
        sage-agent-tools doctor
    EOS
  end

  test do
    assert_match "sage-agent-tools v#{version}", shell_output("#{bin}/sage-agent-tools version")
    assert_path_exists pkgshare/"skills/for-humanity/SKILL.md"
  end
end

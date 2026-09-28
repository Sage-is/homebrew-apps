class WorkDelegation < Formula
  desc "Hand file edits to cheaper models and review the diff: delegate-edit"
  homepage "https://github.com/Sage-is/work-delegation"
  url "https://github.com/Sage-is/work-delegation/archive/refs/tags/v0.1.0.tar.gz"
  version "0.1.0"
  sha256 "0000000000000000000000000000000000000000000000000000000000000000"
  license "AGPL-3.0-only"

  head "https://github.com/Sage-is/work-delegation.git", branch: "develop"

  depends_on "coreutils" # GNU timeout for the opencode and claude lanes
  depends_on "python@3.13"

  def install
    rewrite_shebang detected_python_shebang, "skill/scripts/delegate-agent"
    bin.install "skill/scripts/delegate-edit", "skill/scripts/delegate-agent", "skill/scripts/delegate-stall-verdict"
    pkgshare.install "skill"
  end

  def caveats
    <<~EOS
      Give Claude Code the /delegate-edit skill:
        mkdir -p ~/.claude/skills && ln -sfn #{opt_pkgshare}/skill ~/.claude/skills/delegate-edit

      Check the machine end to end:
        delegate-edit --doctor
    EOS
  end

  test do
    assert_match "usage: delegate-edit", shell_output("#{bin}/delegate-edit --help")
  end
end

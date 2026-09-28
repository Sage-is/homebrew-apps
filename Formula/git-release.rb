class GitRelease < Formula
  desc "One-command git-flow release for repos with the standard release targets"
  homepage "https://github.com/Sage-is/homebrew-apps"
  url "https://github.com/Sage-is/homebrew-apps/archive/refs/tags/git-release-v1.0.0.tar.gz"
  version "1.0.0"
  sha256 "0000000000000000000000000000000000000000000000000000000000000000"
  license "MIT"

  head "https://github.com/Sage-is/homebrew-apps.git", branch: "develop"

  depends_on "git-flow-next"

  def install
    bin.install "git-release"
  end

  def caveats
    <<~EOS
      Run it in a repo whose Makefile has patch_release, minor_release,
      major_release, hotfix, custom_release, release_finish and hotfix_finish:
        git-release            # asks which kind
        git-release patch
        git-release custom 2.1.0
    EOS
  end

  test do
    assert_match "git-release v#{version}", shell_output("#{bin}/git-release version")
  end
end

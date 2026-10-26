class SageBox < Formula
  desc "Set up a Mac as a Sage.is box over SSH, from one file per box"
  homepage "https://github.com/Sage-is/homebrew-apps"
  url "https://github.com/Sage-is/homebrew-apps/archive/refs/tags/sage-box-v0.1.1.tar.gz"
  version "0.1.1"
  sha256 "a1c31072031fcc47b3c321bad8d4eeb4588c9aa9130bb1e0fb530b8196c2b866"
  license "MIT"

  head "https://github.com/Sage-is/homebrew-apps.git", branch: "develop"

  depends_on :macos
  depends_on "rsync"
  depends_on "sage-is/apps/sage-secret"

  def install
    # sage-box sources the runtime code it shares with sage-runtime and
    # trellis-crm from lib/ beside it.
    libexec.install "sage-box"
    (libexec/"lib").install "lib/sage-runtime.sh"
    bin.write_exec_script libexec/"sage-box"
    man1.install "sage-box.1"
    (pkgshare/"boxes").install "docs/boxes/example.env"
  end

  def caveats
    <<~EOS
      One file per box names it and the vault items that unlock it:
        mkdir -p ~/.sage-is/boxes
        cp #{opt_pkgshare}/boxes/example.env ~/.sage-is/boxes/<name>.env
        chmod 600 ~/.sage-is/boxes/<name>.env
      Then see what the box has and what would change, and change it:
        sage-box <name>
        sage-box <name> --apply
      The vault is sage-secret's: run sage-secret setup once on this Mac.
    EOS
  end

  test do
    assert_match(/^sage-box \d+\.\d+\.\d+$/, shell_output("#{bin}/sage-box version").strip)
    assert_match "--apply", shell_output("#{bin}/sage-box --help")
    assert_path_exists man1/"sage-box.1"
  end
end

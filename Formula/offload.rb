class Offload < Formula
  desc "Poka-yoke disk-offload tool for macOS — symlink user data to externals safely"
  homepage "https://github.com/Sage-is/homebrew-apps"
  url "https://github.com/Sage-is/homebrew-apps/archive/refs/tags/offload-v0.2.1.tar.gz"
  version "0.2.1"
  sha256 "282c88e30f570244f68c52940293efec0c2059daf7c8a4fc463ca94109493d0a"
  license "MIT"

  head "https://github.com/Sage-is/homebrew-apps.git", branch: "develop"

  depends_on :macos
  depends_on arch: :arm64

  uses_from_macos "rsync"

  def install
    bin.install "offload"
    man1.install "offload.1"
  end

  def caveats
    <<~EOS
      First-time setup — point offload at your drives:
        mkdir -p ~/.config/offload
        cat > ~/.config/offload/offload.conf <<EOF
        OFFLOAD_HOT=/Volumes/YourFastSSD
        OFFLOAD_COLD=/Volumes/YourArchiveHDD
        EOF

      Inspect:
        offload status
        offload list
        offload checklist

      Safe first sweep (build cruft — rebuildable, no symlinks):
        offload move tier-c-cruft           # dry-run
        offload move tier-c-cruft --apply

      Guarded app launch (refuses if data drive is unmounted):
        offload launch Signal

      Admin running on another user's home (separate macOS Admin
      account with Full Disk Access on Terminal):
        offload protected --target-home /Users/alice list
        offload protected --target-home /Users/alice messages --apply

      Full reference:
        man offload
    EOS
  end

  test do
    # Help subcommand prints the embedded design narrative.
    assert_match "DESIGN", shell_output("#{bin}/offload help")
    # Version subcommand reports a semver-shaped string.
    assert_match(/^offload v\d+\.\d+\.\d+$/, shell_output("#{bin}/offload version").strip)
    # Manpage installed and readable.
    assert_predicate man1/"offload.1", :exist?
  end
end

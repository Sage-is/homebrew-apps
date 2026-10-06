class SageRuntime < Formula
  desc "Switch docker between Colima, Docker Desktop and OrbStack, and copy data across"
  homepage "https://github.com/Sage-is/homebrew-apps"
  url "https://github.com/Sage-is/homebrew-apps/archive/refs/tags/sage-runtime-v0.1.0.tar.gz"
  version "0.1.0"
  sha256 "0000000000000000000000000000000000000000000000000000000000000000"
  license "MIT"

  head "https://github.com/Sage-is/homebrew-apps.git", branch: "develop"

  depends_on "colima"
  depends_on "docker"
  depends_on "docker-buildx"
  depends_on "docker-compose"
  depends_on "docker-credential-helper"
  depends_on :macos

  def install
    bin.install "sage-runtime"
    man1.install "sage-runtime.1"
  end

  def caveats
    <<~EOS
      Docker Desktop is optional. Switch docker to Colima (the first time
      creates a development VM sized for this Mac):
        sage-runtime use colima

      Back to Docker Desktop or OrbStack whenever you like:
        sage-runtime use docker-desktop

      Each runtime keeps its own volumes and images. Bring one across:
        sage-runtime copy-volume NAME
        sage-runtime copy-image REF

      Check for problems:
        sage-runtime status
    EOS
  end

  test do
    assert_match(/^sage-runtime \d+\.\d+\.\d+$/, shell_output("#{bin}/sage-runtime version").strip)
    assert_match "copy-volume NAME", shell_output("#{bin}/sage-runtime --help")
    assert_path_exists man1/"sage-runtime.1"
  end
end

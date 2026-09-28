class Comicreel < Formula
  desc "Make any movie a comic. Make any comic a movie"
  homepage "https://github.com/Sage-is/comicreel"
  url "https://github.com/Sage-is/homebrew-apps/archive/refs/tags/comicreel-v0.1.0.tar.gz"
  version "0.1.0"
  sha256 "0000000000000000000000000000000000000000000000000000000000000000"
  license "AGPL-3.0-or-later"

  head "https://github.com/Sage-is/homebrew-apps.git", branch: "develop"

  depends_on "ffmpeg" # frame extraction, scene detection, encoding
  depends_on "uv"     # runs the packaged app from its pinned tag (cached after first launch)
  depends_on "yt-dlp" # paste-a-link ingest

  def install
    bin.install "comicreel"
  end

  def caveats
    <<~EOS
      Launch (opens your browser at http://127.0.0.1:5000):
        comicreel

      First launch resolves the app from its pinned release via uv and caches
      it, so later launches are instant. Make comics from a file (drag-drop in
      the browser) or paste a video URL; export the edit to DaVinci Resolve /
      Premiere / FCPX.

      Pin a different app build:
        COMICREEL_REF=v2.0.0-alpha.3 comicreel
      Skip the browser auto-open (headless):
        COMICREEL_NO_BROWSER=1 comicreel

      The app's repository is private for now: launching needs GitHub access to it.

      This is a 2.0 alpha — the FCP7 export's audio lanes are still under
      investigation (video / import / relink are verified).
    EOS
  end

  test do
    assert_match "launcher v", shell_output("#{bin}/comicreel version")
  end
end

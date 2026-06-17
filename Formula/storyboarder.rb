class Storyboarder < Formula
  desc "Make any movie a comic. Make any comic a movie"
  homepage "https://github.com/opencoca/MEDIA-Storyboarder"
  url "https://github.com/Sage-is/homebrew-apps/archive/refs/tags/storyboarder-v0.1.0.tar.gz"
  version "0.1.0"
  sha256 "0000000000000000000000000000000000000000000000000000000000000000" # `make bump_formula_url` fills this after the storyboarder-v0.1.0 tag exists
  license "AGPL-3.0-or-later"

  head "https://github.com/Sage-is/homebrew-apps.git", branch: "develop"

  depends_on "uv"     # runs the packaged app from its pinned tag (cached after first launch)
  depends_on "ffmpeg" # frame extraction, scene detection, encoding
  depends_on "yt-dlp" # paste-a-link ingest

  def install
    bin.install "storyboarder"
  end

  def caveats
    <<~EOS
      Launch (opens your browser at http://127.0.0.1:5000):
        storyboarder

      First launch resolves the app from its pinned release via uv and caches
      it, so later launches are instant. Make comics from a file (drag-drop in
      the browser) or paste a video URL; export the edit to DaVinci Resolve /
      Premiere / FCPX.

      Pin a different app build:
        STORYBOARDER_REF=v2.0.0-alpha.2 storyboarder
      Skip the browser auto-open (headless):
        STORYBOARDER_NO_BROWSER=1 storyboarder

      This is a 2.0 alpha — the FCP7 export's audio lanes are still under
      investigation (video / import / relink are verified).
    EOS
  end

  test do
    assert_match "launcher v", shell_output("#{bin}/storyboarder version")
  end
end

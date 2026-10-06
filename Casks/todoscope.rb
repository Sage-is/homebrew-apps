cask "todoscope" do
  version "1.1.0"
  sha256 "3c6bb3aa877bb9ace655aafca214e2b4191ed71580f934257d4d4abb5c831e61"

  url "https://github.com/Startr/TodoScope/releases/download/v#{version}/TodoScope-v#{version}.dmg"
  name "TodoScope"
  desc "See every TODO across your repos as a kanban board"
  homepage "https://github.com/Startr/TodoScope"

  livecheck do
    url :url
    strategy :github_latest
  end

  depends_on arch: :arm64
  depends_on macos: :monterey

  app "TodoScope.app"

  # The app ships unsigned until Apple Developer ID credentials land. macOS
  # Sequoia removed the right-click-Open bypass, so a quarantined unsigned app
  # shows only "damaged app". Stripping the quarantine bit at install time is
  # the difference between "opens first try" and a dead end.
  # Remove this block once releases are signed and notarized.
  postflight_steps do
    run "/usr/bin/xattr", args: ["-dr", "com.apple.quarantine", "{{appdir}}/TodoScope.app"]
  end

  zap trash: [
    "~/.todoscope",
    "~/Library/Caches/com.startr.todoscope",
    "~/Library/Saved Application State/com.startr.todoscope.savedState",
    "~/Library/WebKit/com.startr.todoscope",
  ]
end

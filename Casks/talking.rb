cask "talking" do
  version "1.2.3"
  sha256 "95af5a91fce6f3a48a425b1f9696657e282bd2c513c387d769db7aa68b877510"

  url "https://github.com/Sage-is/talking/releases/download/v#{version}/Talking-#{version}.dmg"
  name "Sage.is Talking"
  desc "Offline two-way voice: transcription and read-along speech via WhisperKit"
  homepage "https://github.com/Sage-is/talking"

  depends_on arch: :arm64
  depends_on macos: :sonoma

  app "Talking.app"

  zap trash: [
    "~/Library/Application Support/is.sage.talking",
    "~/Library/Logs/Talking.log",
    "~/Library/Preferences/is.sage.talking.plist",
  ]
end

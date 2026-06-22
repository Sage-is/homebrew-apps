cask "talking" do
  version "1.2.3"
  sha256 "95af5a91fce6f3a48a425b1f9696657e282bd2c513c387d769db7aa68b877510"

  url "https://github.com/opencoca/local-whisper/releases/download/v#{version}/Talking-#{version}.dmg"
  name "Sage.is Talking"
  desc "100% offline two-way voice for macOS — transcription + read-along TTS, powered by WhisperKit"
  homepage "https://github.com/opencoca/local-whisper"

  depends_on macos: :sonoma
  depends_on arch: :arm64

  app "Talking.app"

  zap trash: [
    "~/Library/Preferences/is.sage.talking.plist",
    "~/Library/Logs/Talking.log",
    "~/Library/Application Support/is.sage.talking",
  ]
end

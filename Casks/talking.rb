cask "talking" do
  version "1.2.2"
  sha256 "06390972e200ab0fa6a76c74714baf06affe1f266bf62c88a6451c8463ea0165"

  url "https://github.com/opencoca/local-whisper/releases/download/v#{version}/Talking-#{version}.dmg"
  name "Sage.is Talking"
  desc "100% offline two-way voice for macOS — transcription + read-along TTS, powered by WhisperKit"
  homepage "https://github.com/opencoca/local-whisper"

  depends_on macos: ">= :sonoma"
  depends_on arch: :arm64

  app "Talking.app"

  zap trash: [
    "~/Library/Preferences/is.sage.talking.plist",
    "~/Library/Logs/Talking.log",
    "~/Library/Application Support/is.sage.talking",
  ]
end

cask "talking" do
  version "1.2.1"
  sha256 "1a613ea525c459fc9b1e28492164ee4698d4691d241f078c7303250ae342257d"

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

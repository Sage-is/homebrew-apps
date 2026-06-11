cask "talking" do
  version "1.2.3"
  sha256 "417f4e06bab459b75eb3e9309662c7d366b68136c56f74aac9f456e302c681c2"

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

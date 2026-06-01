cask "talking" do
  version "1.1.0"
  sha256 "30ec3fe2353e06b497130d8028d198a517dd80bd188f4823e246c1f81d3db309"

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

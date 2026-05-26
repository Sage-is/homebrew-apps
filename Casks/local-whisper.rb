cask "local-whisper" do
  version "1.1.0"
  sha256 "30ec3fe2353e06b497130d8028d198a517dd80bd188f4823e246c1f81d3db309"

  url "https://github.com/opencoca/local-whisper/releases/download/v#{version}/LocalWhisper-#{version}.dmg"
  name "LocalWhisper"
  desc "100% offline voice-to-text for macOS, powered by WhisperKit"
  homepage "https://github.com/opencoca/local-whisper"

  depends_on macos: ">= :sonoma"
  depends_on arch: :arm64

  app "LocalWhisper.app"

  zap trash: [
    "~/Library/Preferences/com.localwhisper.app.plist",
    "~/Library/Logs/LocalWhisper.log",
    "~/Library/Application Support/com.localwhisper.app",
  ]
end

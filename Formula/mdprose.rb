class Mdprose < Formula
  desc "Markdown prose hygiene: unwrap hard-wrapped paragraphs, strip stray whitespace"
  homepage "https://github.com/Sage-is/homebrew-apps"
  url "https://github.com/Sage-is/homebrew-apps/archive/refs/tags/mdprose-v1.0.0.tar.gz"
  version "1.0.0"
  sha256 "0000000000000000000000000000000000000000000000000000000000000000"
  license "MIT"

  head "https://github.com/Sage-is/homebrew-apps.git", branch: "develop"

  depends_on "python@3.13"

  def install
    rewrite_shebang detected_python_shebang, "mdprose"
    bin.install "mdprose"
  end

  test do
    (testpath/"note.md").write "One line\nwrapped here.\n"
    system bin/"mdprose", "fix", testpath/"note.md"
    assert_equal "One line wrapped here.\n", (testpath/"note.md").read
  end
end

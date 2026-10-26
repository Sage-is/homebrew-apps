class Cleanroom < Formula
  desc "Reverse-engineer anything, locally: MCP server and CLI with pageable Evidence"
  homepage "https://github.com/Sage-is/CleanRoom"
  url "https://github.com/Sage-is/homebrew-apps/archive/refs/tags/cleanroom-v0.1.0.tar.gz"
  sha256 "0000000000000000000000000000000000000000000000000000000000000000"
  license "AGPL-3.0-or-later"

  head "https://github.com/Sage-is/homebrew-apps.git", branch: "develop"

  depends_on "uv" # fetches the pinned cleanroom-re release from PyPI (cached after the first run)

  def install
    bin.install "cleanroom"
  end

  def caveats
    <<~EOS
      The first run fetches cleanroom-re #{version} from PyPI through uv and
      caches it. Run this once before registering it with an agent, because
      MCP clients time out on a cold first start:
        cleanroom doctor

      Register it with Claude Code:
        claude mcp add cleanroom -- cleanroom mcp

      Native decompilation needs Ghidra (free) or Hopper installed on this Mac;
      CleanRoom never sends a binary to a cloud decompiler. Without either, the
      builtin engine (lief + capstone) still gives functions, strings, asm and xrefs.

      Optional extras (web, android, ghidra, all) and the Python uv runs it with:
        CLEANROOM_EXTRAS=all cleanroom doctor
        CLEANROOM_PYTHON=3.12 cleanroom doctor
    EOS
  end

  test do
    assert_match "cleanroom #{version}", shell_output("#{bin}/cleanroom version")
  end
end

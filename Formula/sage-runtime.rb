class SageRuntime < Formula
  desc "Switch docker between Colima, Docker Desktop and OrbStack, and copy data across"
  homepage "https://github.com/Sage-is/homebrew-apps"
  url "https://github.com/Sage-is/homebrew-apps/archive/refs/tags/sage-runtime-v0.1.0.tar.gz"
  version "0.1.0"
  sha256 "0000000000000000000000000000000000000000000000000000000000000000"
  license "MIT"

  head "https://github.com/Sage-is/homebrew-apps.git", branch: "develop"

  depends_on "colima"
  depends_on "docker"
  depends_on "docker-buildx"
  depends_on "docker-compose"
  depends_on "docker-credential-helper"
  depends_on :macos

  # krunkit, the default Colima VM on Apple Silicon, refuses Intel. Homebrew 7
  # neither taps nor trusts a dependency's tap, and krunkit's own dependencies
  # come from that tap too: tap and trust libkrun/krun before installing.
  on_arm do
    depends_on "libkrun/krun/krunkit"
  end

  def install
    # sage-runtime sources the runtime code it shares with trellis-crm and
    # ai-ui from lib/ beside it.
    libexec.install "sage-runtime"
    (libexec/"lib").install "lib/sage-runtime.sh"
    bin.write_exec_script libexec/"sage-runtime"
    man1.install "sage-runtime.1"
  end

  def caveats
    <<~EOS
      Docker Desktop is optional. On Apple Silicon, sage-runtime depends on
      krunkit. krunkit and its libraries come from the libkrun/krun tap, and
      Homebrew installs them only after you tap and trust it:
        brew tap libkrun/krun && brew trust libkrun/krun
      A new Colima VM is then a krunkit VM: it gives the memory it frees
      back to macOS, and its containers can reach the GPU (see
      man sage-runtime). Making one adds two settings to Lima's override
      file, ~/.colima/_lima/_config/override.yaml: "mountType: virtiofs"
      (abiosoft/colima#1607), and a boot step that mounts the VM's data
      disk, where Docker keeps images and volumes (abiosoft/colima#1614).
      Every Colima VM on this Mac reads that file, so a QEMU Colima
      profile will not start. A Colima VM made earlier keeps its type
      until you convert it: convert makes a vz VM krunkit on the same data
      disk, with every image and volume and the same size and mounts. The
      first line prints the plan and changes nothing:
        sage-runtime convert
        sage-runtime convert --yes

      Leaving Docker Desktop? Stop your apps, then bring the named volumes
      across and switch in one go. Anonymous volumes and buildx caches
      stay behind. The dry run starts and copies nothing; it lists what
      would move:
        sage-runtime migrate --dry-run
        sage-runtime migrate

      Or switch docker to Colima (the first time creates a development VM
      sized for this Mac):
        sage-runtime use colima

      Build linux/amd64 images on the build VM, which has Rosetta on Apple
      Silicon and never takes over docker's context:
        sage-runtime build-vm

      Back to Docker Desktop or OrbStack whenever you like:
        sage-runtime use docker-desktop

      Each runtime keeps its own volumes and images. Bring one across:
        sage-runtime copy-volume NAME
        sage-runtime copy-image REF

      Check for problems, what Docker Desktop left behind among them,
      each with the line that mends it:
        sage-runtime status
    EOS
  end

  test do
    assert_match(/^sage-runtime \d+\.\d+\.\d+$/, shell_output("#{bin}/sage-runtime version").strip)
    assert_match "copy-volume NAME", shell_output("#{bin}/sage-runtime --help")
    assert_path_exists man1/"sage-runtime.1"
  end
end

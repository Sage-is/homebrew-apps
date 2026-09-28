class CrDeploy < Formula
  include Language::Python::Virtualenv

  desc "Deploy to CapRover by digest, check the new version answers, roll back"
  homepage "https://github.com/Sage-is/homebrew-apps"
  url "https://github.com/Sage-is/homebrew-apps/archive/refs/tags/cr-deploy-v0.1.0.tar.gz"
  version "0.1.0"
  sha256 "0000000000000000000000000000000000000000000000000000000000000000"
  license "MIT"

  head "https://github.com/Sage-is/homebrew-apps.git", branch: "develop"

  depends_on "libyaml"
  depends_on "python@3.13"

  resource "pyyaml" do
    url "https://files.pythonhosted.org/packages/05/8e/961c0007c59b8dd7729d542c61a4d537767a59645b82a0b521206e1e25c2/pyyaml-6.0.3.tar.gz"
    sha256 "d76623373421df22fb4cf8817020cbb7ef15c725b9d5e45f17e189bfc384190f"
  end

  def install
    venv = virtualenv_create(libexec, "python3.13")
    venv.pip_install resources
    libexec.install "cr-deploy"
    rewrite_shebang python_shebang_rewrite_info(libexec/"bin/python"), libexec/"cr-deploy"
    bin.install_symlink libexec/"cr-deploy"
    man1.install "cr-deploy.1"

    # The tool was called `captain` before its first release; this alias goes in 0.2.0.
    (bin/"captain").write <<~SH
      #!/bin/sh
      echo "captain is now cr-deploy; the captain alias goes in cr-deploy 0.2.0" >&2
      exec "#{opt_bin}/cr-deploy" "$@"
    SH
    chmod 0755, bin/"captain"
  end

  def caveats
    <<~EOS
      cr-deploy logs in with the caprover CLI's saved token. Once, if needed:
        npm install -g caprover && caprover login

      Then:
        cr-deploy apps
        cr-deploy deploy-image APP ghcr.io/org/image@sha256:... --verify https://APP/api/config --expect version=1.2.3
        cr-deploy rollback APP
    EOS
  end

  test do
    assert_match "cr-deploy #{version}", shell_output("#{bin}/cr-deploy version")
    assert_match "cr-deploy #{version}", shell_output("#{bin}/captain version 2>/dev/null")
    assert_path_exists man1/"cr-deploy.1"
  end
end

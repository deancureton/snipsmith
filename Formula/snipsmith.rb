class Snipsmith < Formula
  include Language::Python::Virtualenv

  desc "Manage LaTeX snippets for Obsidian, VS Code, and Neovim from one YAML file"
  homepage "https://github.com/deancureton/snipsmith"
  url "https://github.com/deancureton/snipsmith/archive/refs/tags/v0.1.0.tar.gz"
  sha256 "0000000000000000000000000000000000000000000000000000000000000000"
  license "MIT"
  head "https://github.com/deancureton/snipsmith.git", branch: "main"

  depends_on "libyaml"
  depends_on "python@3.13"

  resource "pyyaml" do
    url "https://files.pythonhosted.org/packages/05/8e/961c0007c59b8dd7729d542c61a4d537767a59645b82a0b521206e1e25c2/pyyaml-6.0.3.tar.gz"
    sha256 "d76623373421df22fb4cf8817020cbb7ef15c725b9d5e45f17e189bfc384190f"
  end

  def install
    virtualenv_install_with_resources
  end

  test do
    assert_match version.to_s, shell_output("#{bin}/snipsmith --version")
    (testpath/"snippets.yaml").write <<~YAML
      snippets:
        - trigger: mk
          replacement: $$1$
          options:
            text: true
            auto: true
    YAML
    system bin/"snipsmith", "check", testpath/"snippets.yaml"
    system bin/"snipsmith", "build", testpath/"snippets.yaml", "--out", testpath/"out"
    assert_path_exists testpath/"out/latex.hsnips"
  end
end

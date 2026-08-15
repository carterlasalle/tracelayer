# Homebrew formula for TraceLayer (agent-native software traceability).
#
# Copy into your own tap: <tap-repo>/Formula/tracelayer.rb
#   brew install <you>/<tap>/tracelayer
#
# After each new PyPI release, regenerate with contrib/brew/bump.sh.
# Installs both executables: `trace` and `tracelayer` (the latter never
# collides with macOS /usr/bin/trace).

class Tracelayer < Formula
  desc "Agent-native software traceability system"
  homepage "https://github.com/carterlasalle/tracelayer"
  url "https://files.pythonhosted.org/packages/ce/83/b6ab7d6941802176addb31f4d2f9e2b7fb8f47958d390f08e3767b566bd6/tracelayer-0.2.2.tar.gz"
  sha256 "5b195df333d67d2ba72a74683025dd1c1dac6a46126908fce38ffeb812b78522"
  license "Apache-2.0"

  depends_on "pipx"
  depends_on "python@3.13"

  def install
    ENV["PIPX_HOME"] = libexec/"venvs"
    ENV["PIPX_BIN_DIR"] = libexec/"bin"
    python = formula_opt_bin("python@3.13")/"python3.13"
    # Build from the formula's own cached sdist (no second PyPI fetch).
    system "pipx", "install", "--python", python, buildpath.to_s
    bin.install_symlink libexec/"bin/trace", libexec/"bin/tracelayer"
  end

  test do
    assert_match "trace", shell_output("#{bin}/tracelayer --help")
  end
end

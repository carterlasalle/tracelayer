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
  url "https://files.pythonhosted.org/packages/9c/d8/28ed724fe5ec36615a672e9b9733c2afbf7ab184e2c28d04b1e88d22cbec/tracelayer-0.2.4.tar.gz"
  sha256 "b6c393787b2bc446fc47b7752c4a309d9d5a8e2a4320f1bf23d403cf9a285bbd"
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

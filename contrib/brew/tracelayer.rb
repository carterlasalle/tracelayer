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
  url "https://files.pythonhosted.org/packages/f7/2d/737e4831c5dc4dc31cc98b1628d73c30d1f37ed2aae2bd53730d8f88d8ad/tracelayer-0.1.12.tar.gz"
  sha256 "bb19b3d39e117b7ec10d4194b302e9ea0038e9d14a64029ded8007e592d2a898"
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

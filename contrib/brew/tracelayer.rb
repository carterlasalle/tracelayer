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
  url "https://files.pythonhosted.org/packages/ad/bd/1755c546c0d856365d38a825fa64ce781a4b4c036f6b263cfb096e142e26/tracelayer-0.2.33.tar.gz"
  sha256 "2d96c872b26319e79b36b50f8157626e3d7c79236125fcd66fb51028fafd20e5"
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

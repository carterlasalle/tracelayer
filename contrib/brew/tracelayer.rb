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
  url "https://files.pythonhosted.org/packages/2d/94/7b54f2134f58458e3428da50a9a7fbfde023a40f56f901c7ebf3b0e7854c/tracelayer-0.2.3.tar.gz"
  sha256 "9c766aa86e6f2bf44b161644c5203cfc786067690404f1b6e7f49add865d5a70"
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

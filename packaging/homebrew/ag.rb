# Homebrew formula for ag (head-only: ag is a fast-moving single file, so
# installs track the GitHub repo HEAD rather than a versioned tarball).
# Not published to any tap; install with:
#   brew install --HEAD ./packaging/homebrew/ag.rb
# or, from a checkout: brew install --HEAD packaging/homebrew/ag.rb
class Ag < Formula
  desc "Single-file CLI + agent orchestrator (stdlib-only Python)"
  homepage "https://github.com/Segzman/ag"
  # No license field: the repo ships no LICENSE file yet.
  head "https://github.com/Segzman/ag.git", branch: "master"

  depends_on "python@3"

  def install
    bin.install "ag"
  end

  test do
    assert_match "0.1.0", shell_output("#{bin}/ag --version")
    system bin/"ag", "--dir", testpath/".agent", "selfcheck"
  end
end

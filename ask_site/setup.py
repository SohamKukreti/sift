"""ask-site-setup: download the browser the crawler needs. Run it once."""

import subprocess
import sys


def main():
    # Use this Python, so the browser matches the Playwright version ask-site runs with.
    subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)


if __name__ == "__main__":
    main()

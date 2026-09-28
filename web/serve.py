"""Serve the sift web page, plus a few requests a browser can't make on its own.

The page does all the work in the browser. But Crawl4AI Cloud does not allow
calls from a web page (no CORS headers), and most sites don't let a page
download their PDFs. So this script forwards those requests. It keeps no keys
and no state: the key comes from the page with each call.

It can also fetch pages with crawl4ai on this machine ("local" mode on the
page), so you only need an OpenRouter key. That needs crawl4ai and its browser:
`pip install -e .` and `sift-setup` from the repo root.

    python web/serve.py              # then open http://localhost:8000
    python web/serve.py 9000         # another port
    python web/serve.py --cloud-only # don't offer local mode (for a hosted copy)

Cloud mode needs only Python's standard library.
"""

import asyncio
import json
import sys
import threading
import urllib.error
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

SCRAPE_URL = "https://api.crawl4ai.com/scrape"
MAX_PDF_SIZE = 20_000_000  # bytes, the same limit as sift/pdf.py
LOCAL_TIMEOUT = 90  # seconds for one page in local mode
WEB_DIR = Path(__file__).parent

# Headers from the cloud that the page needs: the cost, and how long to wait when rate limited.
PASSED_HEADERS = ("x-c4-cost", "retry-after")


class LocalCrawler:
    """One crawl4ai browser, kept open, that fetches one page per call.

    crawl4ai is async, and the server uses threads, so the browser lives on its
    own event loop in a background thread.
    """

    def __init__(self):
        self.loop = None
        self.crawler = None
        self.lock = threading.Lock()

    @staticmethod
    def installed():
        try:
            import crawl4ai  # noqa: F401
            return True
        except ImportError:
            return False

    def scrape(self, url):
        """Return (markdown, html) for one page. Raise on failure."""
        with self.lock:
            if self.loop is None:
                self.loop = asyncio.new_event_loop()
                threading.Thread(target=self.loop.run_forever, daemon=True).start()
        future = asyncio.run_coroutine_threadsafe(self._scrape(url), self.loop)
        return future.result(timeout=LOCAL_TIMEOUT)

    async def _scrape(self, url):
        from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig, PruningContentFilterLXML
        from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator

        if self.crawler is None:
            self.crawler = AsyncWebCrawler(config=BrowserConfig(headless=True, verbose=False))
            await self.crawler.start()
        result = await self.crawler.arun(url, config=CrawlerRunConfig(
            cache_mode=CacheMode.BYPASS,
            # "Fit" markdown: the page without menus, footers and other noise, as in sift/crawl.py.
            markdown_generator=DefaultMarkdownGenerator(content_filter=PruningContentFilterLXML()),
            verbose=False,
        ))
        if not result.success:
            raise RuntimeError(result.error_message or "unknown error")
        fit = (result.markdown.fit_markdown or "").strip()
        return fit or result.markdown.raw_markdown, result.html or ""


local = LocalCrawler()
LOCAL_ENABLED = True  # set by main()


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_DIR), **kwargs)

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if self.path == "/proxy/local-scrape":
            return self.local_scrape(body)
        if self.path != "/proxy/scrape":
            return self.send_error(404)
        request = urllib.request.Request(SCRAPE_URL, data=body, method="POST", headers={
            "Content-Type": "application/json",
            "Authorization": self.headers.get("Authorization", ""),
        })
        self.forward(request, timeout=130)

    def do_GET(self):
        if self.path == "/proxy/local-status":
            return self.send_json(200, {"available": LOCAL_ENABLED and local.installed()})
        if not self.path.startswith("/proxy/pdf"):
            return super().do_GET()
        url = parse_qs(urlparse(self.path).query).get("url", [""])[0]
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.path.lower().endswith(".pdf"):
            return self.send_error(400, "only http(s) links to .pdf files")
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (sift)"})
        self.forward(request, timeout=60, max_size=MAX_PDF_SIZE)

    def local_scrape(self, body):
        if not (LOCAL_ENABLED and local.installed()):
            return self.send_json(501, {"ok": False, "reason": "local mode is off on this server"})
        try:
            url = json.loads(body)["url"]
            if urlparse(url).scheme not in ("http", "https"):
                raise ValueError("only http(s) links")
            markdown, html = local.scrape(url)
        except Exception as error:
            return self.send_json(502, {"ok": False, "reason": str(error).strip().splitlines()[0][:200]
                                        if str(error).strip() else type(error).__name__})
        self.send_json(200, {"ok": True, "markdown": markdown, "html": html})

    def send_json(self, status, data):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def forward(self, request, timeout, max_size=None):
        try:
            response = urllib.request.urlopen(request, timeout=timeout)
        except urllib.error.HTTPError as error:
            response = error  # an HTTP error still has a status, headers and a body
        except Exception as error:
            return self.send_error(502, str(error)[:200])

        data = response.read(max_size + 1 if max_size else -1)
        if max_size and len(data) > max_size:
            return self.send_error(413, f"file is larger than {max_size // 1_000_000} MB")

        self.send_response(response.status)
        self.send_header("Content-Type", response.headers.get("Content-Type", "application/octet-stream"))
        for name in PASSED_HEADERS:
            if response.headers.get(name):
                self.send_header(name, response.headers[name])
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main():
    global LOCAL_ENABLED
    args = sys.argv[1:]
    LOCAL_ENABLED = "--cloud-only" not in args
    ports = [a for a in args if a.isdigit()]
    port = int(ports[0]) if ports else 8000

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"sift is at http://localhost:{port}  (ctrl+c to stop)")
    if LOCAL_ENABLED and not local.installed():
        print("crawl4ai is not installed, so only cloud mode is offered. "
              "For local mode: pip install -e . && sift-setup")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

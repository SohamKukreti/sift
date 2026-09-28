"""Step 1, cloud mode: crawl the site with Crawl4AI Cloud instead of a local browser.

The cloud scrapes one page per call and has no deep crawl, so we follow the
links ourselves: best-first, with the same filters and keyword scorer as the
local crawl. No browser is needed on this machine.
"""

import asyncio
import heapq
import os
import re
import time
from urllib.parse import urldefrag, urljoin, urlparse

import requests
from lxml import html as lxml_html
from crawl4ai import PruningContentFilterLXML
from crawl4ai.deep_crawling.filters import FilterChain, URLPatternFilter
from crawl4ai.deep_crawling.scorers import KeywordRelevanceScorer
from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator
from crawl4ai.utils import get_base_domain

from .crawl import SKIPPED_FILES, Page, make_url_filter
from .pdf import is_pdf, pdf_text

SCRAPE_URL = "https://api.crawl4ai.com/scrape"

# A page that needs a real browser can take up to 90 seconds. The docs say to wait 120.
TIMEOUT = 120

# "fleet-busy" (503) means no cloud browser was free for a moment. Try again a few times.
BUSY_RETRIES = 3

# How long to keep waiting when we are over the rate limit.
MAX_RATE_LIMIT_WAIT = 120  # seconds

# Markdown links: [text](url) and ![alt](url). We take the url up to a space or ")".
# Only a fallback: the cloud's cleaned markdown drops links it sees as boilerplate.
MARKDOWN_LINK = re.compile(r"\]\(\s*<?([^)\s>]+)")


class OutOfCredit(RuntimeError):
    """The Crawl4AI Cloud account has no credit left. The crawl can't go on."""


def api_key():
    key = os.environ.get("CRAWL4AI_API_KEY") or os.environ.get("CRAWL4AI_KEY")
    if not key:
        raise RuntimeError("CRAWL4AI_API_KEY is not set. Put it in a .env file or export it. "
                           "Get a key at https://api.crawl4ai.com/")
    return key


def scrape(url):
    """Scrape one page. Return (markdown, html, credits). Raise on failure."""
    busy_tries = 0
    give_up_at = time.time() + MAX_RATE_LIMIT_WAIT
    while True:
        response = requests.post(
            SCRAPE_URL,
            # Markdown for the text, HTML for the links (the markdown loses some of them).
            json={"url": url, "format": "both"},
            headers={"Authorization": f"Bearer {api_key()}"},
            timeout=TIMEOUT,
        )
        credits = float(response.headers.get("x-c4-cost") or 0)

        if response.status_code == 429 and time.time() < give_up_at:
            # Over the rate limit (the free plan allows 5 pages a minute): wait, then try again.
            time.sleep(float(response.headers.get("retry-after") or 5))
            continue
        if response.status_code == 503 and busy_tries < BUSY_RETRIES:
            busy_tries += 1
            time.sleep(3)
            continue
        if response.status_code == 402:  # no credit, or over the spend cap: stop the crawl
            raise OutOfCredit(response.json().get("message") or response.text[:300])

        data = response.json() if response.content else {}
        if not response.ok or not data.get("ok"):
            reason = data.get("reason") or data.get("error") or response.text[:100]
            raise RuntimeError(f"cloud error {response.status_code}: {reason}")
        return data.get("markdown") or "", data.get("html") or "", credits


def html_to_markdown(page_html, url):
    """Turn HTML into markdown on this machine, the same way the local crawl does."""
    result = DefaultMarkdownGenerator(content_filter=PruningContentFilterLXML()).generate_markdown(
        page_html, base_url=url)
    return (result.fit_markdown or "").strip() or result.raw_markdown


def links_in(page_html, markdown, base_url):
    """Absolute http(s) links in the page, without #fragments, in page order."""
    hrefs = []
    if page_html.strip():
        try:
            hrefs = lxml_html.fromstring(page_html).xpath("//a/@href")
        except Exception:
            pass  # broken HTML: fall back to the markdown links
    if not hrefs:
        hrefs = MARKDOWN_LINK.findall(markdown)

    links = []
    for href in hrefs:
        link = urldefrag(urljoin(base_url, href.strip())).url
        if urlparse(link).scheme in ("http", "https"):
            links.append(link)
    return list(dict.fromkeys(links))


async def cloud_pages(url, filters, keywords, max_pages, max_depth):
    """Crawl best-first with Crawl4AI Cloud. Yield each page as a Page."""
    start_domain = get_base_domain(url)
    url_filter = FilterChain([
        make_url_filter(url, filters),
        URLPatternFilter(SKIPPED_FILES, reverse=True),  # reverse: block these instead of allowing
    ])
    scorer = KeywordRelevanceScorer(keywords=keywords)

    # Highest score first, then lowest depth, then URL: the same order as crawl4ai's local crawl.
    queue = [(0.0, 0, url)]
    seen = {url}
    crawled = 0

    while queue and crawled < max_pages:
        _, depth, page_url = heapq.heappop(queue)
        crawled += 1
        page = Page(page_url, depth)
        page_html = ""

        try:
            if is_pdf(page_url):
                # The cloud does not read PDFs, so we download and read them ourselves.
                page.text = await asyncio.to_thread(pdf_text, page_url)
            else:
                page.text, page_html, page.credits = await asyncio.to_thread(scrape, page_url)
                # Sometimes the cloud's markdown is empty while its HTML has the page.
                if not page.text.strip() and page_html.strip():
                    page.text = await asyncio.to_thread(html_to_markdown, page_html, page_url)
        except OutOfCredit:
            raise
        except Exception as error:
            page.error = str(error) or type(error).__name__
        yield page

        if page.text is None or depth >= max_depth or is_pdf(page_url):
            continue
        for link in links_in(page_html, page.text, page_url):
            # Same site only (subdomains count), like the local crawl with include_external=False.
            if link in seen or get_base_domain(link) != start_domain:
                continue
            if not await url_filter.apply(link):
                continue
            seen.add(link)
            heapq.heappush(queue, (-scorer.score(link), depth + 1, link))

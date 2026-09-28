"""Step 1: crawl the site with crawl4ai on this machine, best-looking links first.

The helpers here (keywords, filters, chunks) are shared with the cloud crawl in cloud.py.
"""

import re
from contextlib import aclosing
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig, PruningContentFilterLXML
from crawl4ai.deep_crawling import BestFirstCrawlingStrategy
from crawl4ai.deep_crawling.filters import DomainFilter, FilterChain, URLPatternFilter
from crawl4ai.deep_crawling.scorers import KeywordRelevanceScorer
from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator

from .pdf import is_pdf, pdf_text

# Jev reads up to 32k tokens. 50k characters is about 12.5k tokens: a safe size.
CHUNK_SIZE = 50_000

# Links to files that have no text to read. We never visit these.
SKIPPED_FILES = ["*.jpg", "*.jpeg", "*.png", "*.gif", "*.webp", "*.svg", "*.ico",
                 "*.mp3", "*.mp4", "*.webm", "*.zip", "*.gz", "*.exe", "*.dmg", "*.apk"]

# Words that say nothing about which link to follow.
STOPWORDS = set("""
    a an the is are was were be been being do does did i me my we our you your it its
    of in on at to for from by with and or but if whether not no can could will would
    should shall may might must that this these those there here what which who whom
    how when where why any some all about into after before than then so as up out
    over under again also just only very such own same too i'm am have has had
    looking look find know want give given offer offers arriving
""".split())


@dataclass
class Page:
    """One crawled page, the same shape for the local and the cloud crawl."""
    url: str
    depth: int
    text: Optional[str] = None   # None when the page failed
    error: Optional[str] = None
    credits: float = 0.0         # Crawl4AI Cloud credits this page cost (0 for a local crawl)


def words_in(text):
    return re.findall(r"[a-z0-9]+", text.lower())


def keywords_from_question(question, start_url):
    """Pick the useful words of the question to rank links with.

    Words that are already in the start URL (like "indiafoss" or "2026") match
    every link on the site, so they can't help us choose. We drop them.
    """
    skip = STOPWORDS | set(words_in(start_url))
    useful = [word for word in words_in(question) if word not in skip and len(word) > 2]
    return list(dict.fromkeys(useful))  # remove duplicates, keep order


def make_url_filter(start_url, filters):
    """Only follow links that contain one of `filters`, or stay on the start domain."""
    if filters:
        return URLPatternFilter([f"*{text}*" for text in filters])
    return DomainFilter(allowed_domains=[urlparse(start_url).netloc])


def make_crawl_strategy(start_url, filters, keywords, max_pages, max_depth):
    return BestFirstCrawlingStrategy(
        max_depth=max_depth,
        max_pages=max_pages,
        include_external=False,
        filter_chain=FilterChain([
            make_url_filter(start_url, filters),
            URLPatternFilter(SKIPPED_FILES, reverse=True),  # reverse: block these instead of allowing
        ]),
        url_scorer=KeywordRelevanceScorer(keywords=keywords),
    )


def make_crawl_config(strategy):
    return CrawlerRunConfig(
        deep_crawl_strategy=strategy,
        stream=True,  # give us each page as soon as it is ready
        cache_mode=CacheMode.BYPASS,
        # "Fit" markdown: the page without menus, footers and other noise.
        markdown_generator=DefaultMarkdownGenerator(content_filter=PruningContentFilterLXML()),
        verbose=False,
    )


async def local_pages(url, filters, keywords, max_pages, max_depth, headless=True):
    """Crawl with a browser on this machine. Yield each page as a Page."""
    strategy = make_crawl_strategy(url, filters, keywords, max_pages, max_depth)
    config = make_crawl_config(strategy)
    try:
        async with AsyncWebCrawler(config=BrowserConfig(headless=headless, verbose=False)) as crawler:
            async with aclosing(await crawler.arun(url, config=config)) as results:
                async for result in results:
                    page = Page(result.url, result.metadata.get("depth"))
                    try:
                        # The browser can't show PDFs, so we download and read them ourselves.
                        if is_pdf(result.url):
                            page.text = pdf_text(result.url)
                        elif result.success:
                            page.text = page_text(result)
                        else:
                            page.error = result.error_message or "unknown error"
                    except Exception as error:
                        page.error = str(error) or type(error).__name__
                    yield page
    finally:
        strategy.cancel()


def page_text(page):
    """The cleaned-up page text, or the full text if cleaning left nothing."""
    fit = (page.markdown.fit_markdown or "").strip()
    return fit or page.markdown.raw_markdown


def split_into_chunks(text, size=CHUNK_SIZE):
    return [text[start:start + size] for start in range(0, len(text), size)]

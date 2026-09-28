"""The main loop: crawl pages one by one until one of them answers the question."""

from contextlib import aclosing
from dataclasses import dataclass
from typing import Callable, Optional, Tuple

from .cloud import cloud_pages
from .crawl import keywords_from_question, local_pages, split_into_chunks
from .jev import ask_jev

# A page is "relevant" when Jev's probability is at or above this.
RELEVANCE_THRESHOLD = 0.6

# Pages with less text than this (login walls, JavaScript-only pages) are skipped.
MIN_PAGE_LENGTH = 50

# Takes (question, url, text) and returns (answer or None if the text has no answer, cost in USD).
Answerer = Callable[[str, str, str], Tuple[Optional[str], float]]


@dataclass
class SearchResult:
    url: Optional[str] = None        # the page the answer came from
    page_text: Optional[str] = None  # the part of that page Jev picked
    score: Optional[float] = None    # Jev's probability for that part
    answer: Optional[str] = None     # the written answer, if an answerer was given
    pages_seen: int = 0
    jev_cost: float = 0.0
    llm_cost: float = 0.0
    cloud_credits: float = 0.0       # Crawl4AI Cloud credits (cloud mode only)

    @property
    def found(self):
        return self.url is not None


async def search_site(
    url,
    question,
    *,
    filters=None,
    keywords=None,
    max_pages=50,
    max_depth=3,
    answerer: Optional[Answerer] = None,
    crawl_only=False,
    cloud=False,
    headless=True,
    log=print,
):
    """Crawl `url` best-first and stop at the first page that answers `question`.

    With `cloud=True`, Crawl4AI Cloud fetches the pages (no local browser).
    Otherwise crawl4ai runs a browser on this machine.

    Every page is checked by Jev. When a page passes:
      - with an `answerer` (an LLM), we ask it for the answer. If it finds
        none, the crawl goes on.
      - without one, we stop and return the page text, so the caller can answer.
    """
    keywords = keywords or keywords_from_question(question, url)
    result = SearchResult()

    log(f"Keywords : {keywords}")

    if cloud:
        pages = cloud_pages(url, filters, keywords, max_pages, max_depth)
    else:
        pages = local_pages(url, filters, keywords, max_pages, max_depth, headless=headless)

    async with aclosing(pages):
        async for page in pages:
            # crawl4ai can hand out a few more pages than max_pages, so we count too.
            if result.pages_seen >= max_pages:
                break
            result.pages_seen += 1
            result.cloud_credits += page.credits
            label = f"[{result.pages_seen:>2}] depth={page.depth}"

            if page.error is not None:
                log(f"{label}  FAILED   {page.url}  ({first_line(page.error)})")
                continue

            if crawl_only:
                log(f"{label}  {len(page.text):>6} chars  {page.url}")
                continue

            if len(page.text.strip()) < MIN_PAGE_LENGTH:
                log(f"{label}  empty    {page.url}")
                continue

            if await check_page(question, page.url, page.text, answerer, result, label, log):
                break

    return result


def first_line(error):
    message = str(error).strip()
    return message.splitlines()[0][:100] if message else type(error).__name__


async def check_page(question, url, text, answerer, result, label, log):
    """Ask Jev about each chunk of the page. Return True when we are done."""
    best_score = 0.0

    # Long pages are checked in chunks. The first relevant chunk wins.
    for chunk in split_into_chunks(text):
        score, cost = ask_jev(question, url, chunk)
        result.jev_cost += cost
        best_score = max(best_score, score)

        if score < RELEVANCE_THRESHOLD:
            continue

        if answerer is None:
            log(f"{label}  jev={score:.2f}  {url}  <- relevant")
            result.url, result.page_text, result.score = url, chunk, score
            return True

        log(f"{label}  jev={score:.2f}  {url}  <- relevant, asking for an answer")
        answer, cost = answerer(question, url, chunk)
        result.llm_cost += cost
        if answer:
            result.url, result.page_text, result.score, result.answer = url, chunk, score, answer
            return True
        log("            no answer on this page, crawling on")

    if best_score < RELEVANCE_THRESHOLD:
        log(f"{label}  jev={best_score:.2f}  {url}")
    return False

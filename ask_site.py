"""
Context, context, everywhere. Not a drop to use.

Ask a question about a website. We crawl it page by page and stop as soon
as we find the answer:

    crawl.py       crawl4ai crawls the site, best-looking links first
    jev.py         Jev decides: "does this page answer the question?"
    claude_cli.py  Claude reads only the page Jev picked and writes a short answer

Usage:
    python3 ask_site.py <url> "<question>" [--filter TEXT ...] [--keywords WORD ...]
                        [--max-pages 50] [--max-depth 3] [--crawl-only]
"""

import argparse
import asyncio
from urllib.parse import urlparse

from crawl4ai import AsyncWebCrawler, BrowserConfig
from dotenv import load_dotenv

from claude_cli import ask_claude
from crawl import keywords_from_question, make_crawl_config, make_crawl_strategy, page_text, split_into_chunks
from jev import ask_jev

# A page is "relevant" when Jev's probability is at or above this.
RELEVANCE_THRESHOLD = 0.6

# Pages with less text than this (login walls, JavaScript-only pages) are skipped.
MIN_PAGE_LENGTH = 50


async def find_answer(args):
    keywords = args.keywords or keywords_from_question(args.question, args.url)
    strategy = make_crawl_strategy(args.url, args.filter, keywords, args.max_pages, args.max_depth)
    config = make_crawl_config(strategy)

    print_header(args, keywords)

    answer = None
    pages_seen = 0
    jev_cost = 0.0

    async with AsyncWebCrawler(config=BrowserConfig(headless=False)) as crawler:
        async for page in await crawler.arun(args.url, config=config):
            pages_seen += 1
            label = f"[{pages_seen:>2}] depth={page.metadata.get('depth')}"

            if not page.success:
                print(f"{label}  FAILED   {page.url}  ({page.error_message})")
                continue

            text = page_text(page)

            if args.crawl_only:
                print(f"{label}  {len(text):>6} chars  {page.url}")
                continue

            if len(text.strip()) < MIN_PAGE_LENGTH:
                print(f"{label}  empty    {page.url}")
                continue

            # Long pages are checked in chunks. The first relevant chunk goes to Claude.
            best_score = 0.0
            for chunk in split_into_chunks(text):
                score, cost = ask_jev(args.question, page.url, chunk)
                jev_cost += cost
                best_score = max(best_score, score)

                if score < RELEVANCE_THRESHOLD:
                    continue

                print(f"{label}  jev={score:.2f}  {page.url}  <- relevant, asking Claude")
                answer = ask_claude(args.question, page.url, chunk)
                if answer:
                    break
                print("            Claude found no answer here, crawling on")

            if answer:
                break
            if best_score < RELEVANCE_THRESHOLD:
                print(f"{label}  jev={best_score:.2f}  {page.url}")

    strategy.cancel()

    print(f"\nPages crawled: {pages_seen}")
    if not args.crawl_only:
        print(f"Jev cost: ${jev_cost:.6f}")
        print("\n" + (answer or "No answer found."))


def print_header(args, keywords):
    scope = args.filter or [f"same domain: {urlparse(args.url).netloc}"]
    mode = "  (crawl only, no Jev/Claude)" if args.crawl_only else ""
    print(f"Question : {args.question}")
    print(f"Start    : {args.url}")
    print(f"Filter   : {scope}")
    print(f"Keywords : {keywords}")
    print(f"Limits   : max_pages={args.max_pages} max_depth={args.max_depth}{mode}\n")


def parse_args():
    parser = argparse.ArgumentParser(description="Crawl a site and answer a question with Jev + Claude.")
    parser.add_argument("url", help="Page to start crawling from.")
    parser.add_argument("question", help="What you want to know.")
    parser.add_argument(
        "--filter", action="append",
        help="Only crawl URLs that contain this text. Repeat for more (any match passes). "
             "Default: stay on the start URL's domain.",
    )
    parser.add_argument(
        "--keywords", nargs="+",
        help="Words to rank links by (matched against the URL). Default: words from the question.",
    )
    parser.add_argument("--max-pages", type=int, default=50, help="Stop after this many pages (default 50).")
    parser.add_argument("--max-depth", type=int, default=3, help="How many links deep to go (default 3).")
    parser.add_argument(
        "--crawl-only", action="store_true",
        help="Only list the pages. No Jev or Claude calls, so it costs nothing.",
    )
    return parser.parse_args()


def main():
    load_dotenv()
    asyncio.run(find_answer(parse_args()))


if __name__ == "__main__":
    main()

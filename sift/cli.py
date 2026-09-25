"""Command line: sift <url> "<question>" [options]"""

import argparse
import asyncio
from urllib.parse import urlparse

from dotenv import find_dotenv, load_dotenv

from .claude_cli import ask_claude
from .search import search_site


def parse_args():
    parser = argparse.ArgumentParser(
        prog="sift",
        description="Crawl a website and answer a question about it. "
                    "Jev picks the relevant page, Claude writes the answer.",
    )
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
    parser.add_argument(
        "--no-claude", action="store_true",
        help="Don't call Claude. Print the first relevant page's text instead.",
    )
    parser.add_argument("--show-browser", action="store_true", help="Show the browser window while crawling.")
    return parser.parse_args()


def main():
    load_dotenv(find_dotenv(usecwd=True))
    args = parse_args()

    scope = args.filter or [f"same domain: {urlparse(args.url).netloc}"]
    print(f"Question : {args.question}")
    print(f"Start    : {args.url}")
    print(f"Filter   : {scope}")
    print(f"Limits   : max_pages={args.max_pages} max_depth={args.max_depth}")

    result = asyncio.run(search_site(
        args.url,
        args.question,
        filters=args.filter,
        keywords=args.keywords,
        max_pages=args.max_pages,
        max_depth=args.max_depth,
        answerer=None if args.no_claude else ask_claude,
        crawl_only=args.crawl_only,
        headless=not args.show_browser,
    ))

    print(f"\nPages crawled: {result.pages_seen}")
    if args.crawl_only:
        return
    print(f"Jev cost: ${result.jev_cost:.6f}\n")

    if not result.found:
        print("No answer found.")
    elif result.answer:
        print(result.answer)
    else:
        print(f"Relevant page: {result.url}  (jev={result.score:.2f})\n")
        print(result.page_text)


if __name__ == "__main__":
    main()

"""ask-site: crawl a website and answer a question about it.

crawl4ai crawls, Jev picks the relevant page, Claude (or your AI) answers.
"""

from .search import SearchResult, search_site

__all__ = ["search_site", "SearchResult"]

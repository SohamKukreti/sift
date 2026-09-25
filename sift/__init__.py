"""sift: crawl a website and answer a question about it.

crawl4ai crawls, Jev picks the relevant page, an LLM answers.
"""

from .search import SearchResult, search_site

__all__ = ["search_site", "SearchResult"]

---
name: sift
description: Answer a question about a specific website by crawling it. Use when the user asks something that a website should answer (an event, a project's docs, a company's policy) and gives the site or it is obvious which site to look at. Crawls page by page and stops at the first relevant page, so it is cheaper than reading the whole site.
---

# sift

Find the page on a website that answers a question, then answer from that page.

## Run it

```bash
uvx --from "${CLAUDE_PLUGIN_ROOT}" sift <url> "<question>" --no-claude [options]
```

- Always pass a full URL, starting with `https://`.
- Pass the user's question **word for word**. Don't shorten or reword it: Jev's score depends
  on the exact words, and a reworded question can push the right page below the threshold.
  Only remove the URL from it if the user put the URL inside the question.
- Always pass `--no-claude`. It makes the command print the relevant page instead of calling
  Claude again, because you are Claude and you write the answer.
- The crawl can take a minute. Don't run it in the background.

## Options

- `--filter TEXT`: **pass this whenever the start URL points to one part of a site.** Without it,
  the crawl covers the whole domain (home page, forum, other events) and can use up all its
  pages before it reaches the right one. Use the word from the URL path that names the topic:
  `https://fossunited.org/indiafoss/2026` → `--filter indiafoss`,
  `https://docs.example.com/v2/api` → `--filter /v2/`. Repeat it to allow more.
  Leave it out only when the whole site is about the topic.
- `--keywords WORD ...`: only if the default crawl misses. They are matched against URLs, so
  use URL-like words: `schedule`, `cfp`, `faq`, `tickets`, `docs`.
- `--max-pages N`: default 50. Use 30 unless the user asks for more. Every page costs a
  little (Jev), so don't raise it without a reason.
- `--max-depth N`: default 3.

## Read the output

- One line per page: `jev=` is Jev's probability that the page answers the question.
- `Relevant page: <url>` followed by the page text: answer from that text only, in 1-3
  sentences, and give the URL as the source. If the text does not really answer the question,
  say so and offer to crawl again with other filters or keywords.
- `No answer found.`: tell the user how many pages were crawled, and suggest a better start URL,
  filter, or keywords.

## If it fails

- `OPENROUTER_API_KEY is not set`: the user must export it, or put it in a `.env` file in the
  current folder. Get a key at https://openrouter.ai/settings/keys.
- `uvx: command not found`: the user must install uv: https://docs.astral.sh/uv/
- A Playwright or browser error: run `uvx --from "${CLAUDE_PLUGIN_ROOT}" sift-setup` once,
  then try again.

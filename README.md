# Context, context, everywhere. Not a drop to use.

Ask a question about a website. Get a short answer, with the page it came from.

Websites have a lot of text, and most of it is not what you are looking for.
Sending every page to a big LLM is slow and expensive. This project crawls a
site one page at a time, uses a small, cheap **decision model** to throw away
pages that don't matter, and only calls the LLM once it has found a page that
does.

```
python3 ask_site.py https://fossunited.org/indiafoss/2026 \
  "Does IndiaFOSS 2026 offer on-spot lightning talks that I can sign up for after arriving at the venue?" \
  --filter indiafoss
```

```
Question : Does IndiaFOSS 2026 offer on-spot lightning talks that I can sign up for after arriving at the venue?
Start    : https://fossunited.org/indiafoss/2026
Filter   : ['indiafoss']
Keywords : ['spot', 'lightning', 'talks', 'sign', 'venue']
Limits   : max_pages=50 max_depth=3

[ 1] depth=0  jev=0.17  https://fossunited.org/indiafoss/2026
[ 2] depth=1  jev=0.08  https://fossunited.org/indiafoss/2026/devrooms/design
[ 3] depth=1  jev=0.04  https://forum.fossunited.org/t/diversity-support-is-back-for-indiafoss-2026/8499
[ 4] depth=1  jev=0.06  https://fossunited.org/c/indiafoss/2026/workshops
[ 5] depth=1  jev=0.69  https://fossunited.org/c/indiafoss/2026communi-con  <- relevant, asking Claude

Pages crawled: 5
Jev cost: $0.000583

No. You must submit a talk idea online before the event. The CFP opens Sep 25 and closes
Sep 26 at 4 PM. Then everyone votes, and the top 7 talks are picked. There's no sign-up
after you arrive.

Source: https://fossunited.org/c/indiafoss/2026communi-con
```

Five pages checked, one LLM call, less than a tenth of a cent spent on filtering.

## How it works

```
                 ┌──────────────┐
  start URL ───▶ │   crawl4ai   │  best-first crawl: links whose URL matches
                 └──────┬───────┘  your keywords are visited first
                        │ one page at a time (as markdown)
                        ▼
                 ┌──────────────┐
                 │     Jev      │  "Does this page answer the question?"
                 └──────┬───────┘  returns a probability, not text
                        │
          below 0.6 ◀───┴───▶ 0.6 or above
          next page             │
                                ▼
                 ┌──────────────┐
                 │    Claude    │  reads only this page, answers in
                 └──────┬───────┘  1-3 sentences, or says NOT_FOUND
                        │
          NOT_FOUND ◀───┴───▶ answer
          next page             │
                                ▼
                         print and stop
```

1. **Crawl** ([`crawl.py`](crawl.py)): [crawl4ai](https://github.com/unclecode/crawl4ai)
   does a best-first deep crawl. It only follows links that match your
   `--filter` (or stay on the same domain), and it ranks links by keywords
   taken from your question. Each page becomes clean markdown, with menus
   and footers removed.
2. **Decide** ([`jev.py`](jev.py)): [Jev](https://openrouter.ai/typesafe/jev-1.13)
   is a decision model from TypeSafe, served on OpenRouter. It doesn't generate
   text. We ask it one yes/no question ("does this page contain information
   that answers the question?") and it returns a probability. Long pages are
   split into chunks and each chunk is checked.
3. **Answer** ([`claude_cli.py`](claude_cli.py)): the first page that scores
   0.6 or more goes to Claude through the local `claude` CLI, with all tools
   off. Claude answers using only that page. If it can't find the answer
   there, it says so and the crawl continues.

The crawl stops at the first answer, or when it hits `--max-pages` or `--max-depth`.

## Setup

You need Python 3.10+, an [OpenRouter API key](https://openrouter.ai/settings/keys),
and [Claude Code](https://claude.com/claude-code) installed and logged in (the
`claude` command).

```bash
git clone <this repo> && cd <this repo>
pip install -r requirements.txt
crawl4ai-setup                 # installs the browser crawl4ai uses
cp .env.example .env           # then put your OpenRouter key in .env
```

## Usage

```bash
python3 ask_site.py <url> "<question>" [options]
```

| Option | Default | What it does |
| --- | --- | --- |
| `--filter TEXT` | same domain as the start URL | Only crawl URLs that contain `TEXT`. Repeat it to allow more (any match passes). |
| `--keywords WORD ...` | words from your question | Words used to rank links. They are matched against the URL, so pick URL-like words (`schedule`, `cfp`, `talks`). |
| `--max-pages N` | 50 | Stop after `N` pages. |
| `--max-depth N` | 3 | How many links deep to go from the start page. |
| `--crawl-only` | off | Only list the pages that would be crawled. No Jev or Claude calls, so it costs nothing. Handy for tuning filters. |

Examples:

```bash
# Try your filters for free first
python3 ask_site.py https://fossunited.org/indiafoss/2026 "Is there childcare?" \
  --filter indiafoss --max-pages 10 --crawl-only

# Push the crawl towards the schedule
python3 ask_site.py https://fossunited.org/indiafoss/2026 "When is the keynote?" \
  --filter indiafoss --keywords schedule keynote
```

## Settings you might want to change

| Setting | File | Default |
| --- | --- | --- |
| `RELEVANCE_THRESHOLD`: how sure Jev must be before we ask Claude | [`ask_site.py`](ask_site.py) | `0.6` |
| `MIN_PAGE_LENGTH`: skip pages with less text than this (login walls) | [`ask_site.py`](ask_site.py) | `50` characters |
| `CHUNK_SIZE`: how much text Jev sees per call | [`crawl.py`](crawl.py) | `50_000` characters |
| Claude model | [`claude_cli.py`](claude_cli.py) | `sonnet` |
| Browser window | [`ask_site.py`](ask_site.py) | visible (`headless=False`), so you can watch the crawl |

## Cost

Jev only charges for input tokens, and the price is low: a full 50-page crawl
costs around a cent. Claude runs through your own Claude Code login and is
called once for each page that passes the Jev check, usually only once per question.

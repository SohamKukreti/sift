# sift

Sift a website for the one page that answers your question. Get a short answer, with the page it came from.

`sift` crawls a site one page at a time. Each page is checked by
[Jev](https://openrouter.ai/typesafe/jev-1.13), a small and cheap **decision
model**: "does this page answer the question?" The crawl stops at the first
page that does, and only that page goes to an LLM for the answer.

Sending every page of a website to an LLM is slow and expensive. Jev costs a
fraction of a cent for a whole crawl, so the LLM reads one page instead of fifty.

```
$ sift https://www.jiit.ac.in "Will everyone be given the degree on stage at JIIT convocation 2026?"

Keywords : ['everyone', 'degree', 'stage', 'convocation', '2026']
[ 1] depth=0  jev=0.14  https://www.jiit.ac.in
[ 2] depth=1  jev=0.08  https://www.jiit.ac.in/research-&-development/phd-degrees-awarded
[ 3] depth=1  jev=0.03  https://www.jiit.ac.in/uploads/FDP_2026_Brochure_65b6d379b8.pdf
  ...
[10] depth=1  jev=0.05  https://www.jiit.ac.in/uploads/Medal_Winners_2026_99c7949f90.pdf
[11] depth=1  jev=0.02  https://www.jiit.ac.in/uploads/Refund_Policy_2026_2_a27b461d05.pdf
[12] depth=1  jev=0.97  https://www.jiit.ac.in/uploads/Student_Invitation_12th_Convocation_6ab982354a.pdf  <- relevant, asking for an answer

Pages crawled: 12
Jev cost: $0.001013
LLM cost: $0.000321  (deepseek/deepseek-v4.1-flash)

No. Only all medals and PhD degrees will be awarded in person on the stage; all other graduates
will rise at their respective seats for conferment of the degree and then collect their degree
certificates from designated rooms after the ceremony.
Source: https://www.jiit.ac.in/uploads/Student_Invitation_12th_Convocation_6ab982354a.pdf
```

The answer was in a one-page PDF invitation, linked from the home page. A search
engine won't surface it and an LLM can't know it. sift checked 12 pages, called
the LLM once, and spent less than a cent and a half in total.

## How it works

```
                 ┌──────────────┐
  start URL ───▶ │   crawl4ai   │  best-first crawl: links whose URL matches
                 └──────┬───────┘  your keywords are visited first
                        │ one page at a time, as clean markdown
                        ▼
                 ┌──────────────┐
                 │     Jev      │  "Does this page answer the question?"
                 └──────┬───────┘  returns a probability, not text
                        │
          below 0.6 ◀───┴───▶ 0.6 or above
          next page             │
                                ▼
                 ┌──────────────┐
                 │     LLM      │  reads only this page and answers,
                 └──────┬───────┘  or says there is no answer here
                        │
        no answer ◀─────┴───▶ answer
        next page               │
                                ▼
                         print and stop
```

1. **Crawl**: [crawl4ai](https://github.com/unclecode/crawl4ai) does a
   best-first deep crawl. It only follows links that match `--filter` (or stay
   on the same domain), and visits links whose URL matches keywords from your
   question first. Menus and footers are removed from each page. PDF links are
   downloaded and read too, since that is where universities and events often
   put the real details. Images and other media are skipped.
2. **Decide**: Jev (by TypeSafe, served on OpenRouter) answers one yes/no
   question per page and returns a probability. Long pages are split into
   chunks and each chunk is checked.
3. **Answer**: the first page that scores 0.6 or more goes to an LLM on
   OpenRouter (default: the open-weight
   [DeepSeek V4.1 Flash](https://openrouter.ai/deepseek/deepseek-v4.1-flash)).
   It answers from that page only, or says there is no answer there. With
   `--no-llm`, the page is printed instead. The skill uses this, because
   Claude is already the one asking.

The crawl stops at the first answer, or at `--max-pages` / `--max-depth`.

## What you need

- An [OpenRouter API key](https://openrouter.ai/settings/keys). One key covers both
  Jev and the answer model.
- [uv](https://docs.astral.sh/uv/getting-started/installation/) (or Python 3.10+ and pip).

Put your key in your environment, or in a `.env` file in the folder you run from:

```bash
export OPENROUTER_API_KEY=sk-or-...
```

The crawler uses a headless browser. Install it once:

```bash
uvx --from git+https://github.com/SohamKukreti/sift sift-setup
```

## Two ways to use it

### 1. In Claude Code (a skill)

```
/plugin marketplace add SohamKukreti/sift
/plugin install sift@sift
```

Then just ask: *"Check fossunited.org/indiafoss/2026: can I give a talk without applying first?"*

The `sift` skill tells Claude when to crawl a site and how to run the
`sift` command. Claude reads the page that Jev picks and answers from it.

### 2. On the command line

```bash
uvx --from git+https://github.com/SohamKukreti/sift sift <url> "<question>" [options]
```

Or install it:

```bash
git clone https://github.com/SohamKukreti/sift && cd sift
pip install -e .
sift <url> "<question>" [options]
```

| Option | Default | What it does |
| --- | --- | --- |
| `--filter TEXT` | same domain as the start URL | Only crawl URLs that contain `TEXT`. Repeat it to allow more (any match passes). |
| `--keywords WORD ...` | words from your question | Words used to rank links. They are matched against the URL, so pick URL-like words (`schedule`, `cfp`, `faq`). |
| `--max-pages N` | 50 | Stop after `N` pages. |
| `--max-depth N` | 3 | How many links deep to go from the start page. |
| `--crawl-only` | off | Only list the pages that would be crawled. No Jev or LLM calls, so it costs nothing. Useful for tuning filters. |
| `--no-llm` | off | Don't call the LLM. Print the relevant page instead. |
| `--model NAME` | `deepseek/deepseek-v4.1-flash` | Any [OpenRouter model](https://openrouter.ai/models) for the answer. |
| `--show-browser` | off | Show the browser window while crawling. Nice for demos. |

Examples:

```bash
# Check your filter for free first
sift https://fossunited.org/indiafoss/2026 "Is there childcare?" \
  --filter indiafoss --max-pages 10 --crawl-only

# Push the crawl towards the schedule
sift https://fossunited.org/indiafoss/2026 "When is the keynote?" \
  --filter indiafoss --keywords schedule keynote
```

## Project layout

```
sift/
  crawl.py        crawl4ai setup: filters, keyword ranking, clean page text
  jev.py          one Jev call: "does this page answer the question?"
  llm.py          one LLM call: a short answer from one page
  openrouter.py   sends requests to OpenRouter (used by jev.py and llm.py)
  pdf.py          reads PDF links (the browser can't)
  search.py       the loop: crawl, check with Jev, answer, stop
  cli.py          the sift command
  setup.py        the sift-setup command (downloads the browser)
skills/sift/      the Claude Code skill
.claude-plugin/   plugin + marketplace files, so the skill installs with /plugin
```

Settings you might want to change:

| Setting | File | Default |
| --- | --- | --- |
| `RELEVANCE_THRESHOLD`: how sure Jev must be before we ask for an answer | `sift/search.py` | `0.6` |
| `MIN_PAGE_LENGTH`: skip pages with less text (login walls) | `sift/search.py` | `50` characters |
| `CHUNK_SIZE`: how much text Jev sees per call | `sift/crawl.py` | `50_000` characters |
| `DEFAULT_MODEL`: the answer model | `sift/llm.py` | `deepseek/deepseek-v4.1-flash` |

## Cost

Jev charges only for input tokens, and the price is low: a 50-page crawl costs
about a cent. The answer step runs once per question in most cases, and costs
a fraction of a cent with the default model.

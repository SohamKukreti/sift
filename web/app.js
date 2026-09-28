// sift in the browser: the same loop as the sift command, with no server of its own.
//
//   crawl  Crawl4AI fetches one page at a time: in the cloud, or on this machine
//          through serve.py ("local" mode). We follow the links
//          ourselves, best-first, with the words of the question as a guide.
//   decide Jev (on OpenRouter) says how sure it is that the page answers the question.
//   answer The first page that scores 0.6 or more goes to an LLM, which answers from it.
//
// Crawl4AI Cloud, local crawl4ai and most PDF links can't be reached from a web
// page, so those go through serve.py (see PROXY). OpenRouter is called directly.

const PROXY = "/proxy";
const JEV_URL = "https://openrouter.ai/api/alpha/decisions";
const CHAT_URL = "https://openrouter.ai/api/v1/chat/completions";
const PDFJS = "https://cdn.jsdelivr.net/npm/pdfjs-dist@4.10.38/build/pdf.min.mjs";

// The same settings as the Python version (sift/search.py and sift/crawl.py).
const RELEVANCE_THRESHOLD = 0.6;
const MIN_PAGE_LENGTH = 50;
const CHUNK_SIZE = 50_000;
const BUSY_RETRIES = 3;
const MAX_RATE_LIMIT_WAIT = 120_000; // ms

const SKIPPED_FILES = /\.(jpe?g|png|gif|webp|svg|ico|mp3|mp4|webm|zip|gz|exe|dmg|apk)$/i;

const STOPWORDS = new Set(`
  a an the is are was were be been being do does did i me my we our you your it its
  of in on at to for from by with and or but if whether not no can could will would
  should shall may might must that this these those there here what which who whom
  how when where why any some all about into after before than then so as up out
  over under again also just only very such own same too i'm am have has had
  looking look find know want give given offer offers arriving
`.trim().split(/\s+/));

const PROMPT = (question, url, text) => `Question: ${question}

Source URL: ${url}

Page content:
<page>
${text}
</page>

Answer the question using ONLY the page content above. Reply in 1-3 sentences
and end with the line 'Source: <url>'. If the page content does not answer the
question, reply with exactly NOT_FOUND and nothing else.`;

const $ = (id) => document.getElementById(id);

class OutOfCredit extends Error {}
class Stopped extends Error {}

// ---------- keys ----------

const KEY_NAMES = { c4: "sift.c4key", or: "sift.orkey", mode: "sift.mode" };

function store(action, name, value) {
  try {
    if (action === "get") return localStorage.getItem(name) || "";
    if (action === "set") localStorage.setItem(name, value);
    if (action === "del") localStorage.removeItem(name);
  } catch { /* private window or blocked storage: keys just aren't remembered */ }
  return "";
}

function loadKeys() {
  $("c4-key").value = store("get", KEY_NAMES.c4);
  $("or-key").value = store("get", KEY_NAMES.or);
  $("remember").checked = Boolean($("c4-key").value || $("or-key").value);
}

function saveKeys() {
  if ($("remember").checked) {
    store("set", KEY_NAMES.c4, $("c4-key").value.trim());
    store("set", KEY_NAMES.or, $("or-key").value.trim());
  } else {
    store("del", KEY_NAMES.c4);
    store("del", KEY_NAMES.or);
  }
}

function mode() {
  return document.querySelector('input[name="mode"]:checked')?.value || "cloud";
}

function showKeyState() {
  const missing = [];
  $("c4-field").hidden = mode() === "local";
  if (mode() === "cloud" && !$("c4-key").value.trim()) missing.push("Crawl4AI");
  if (!$("or-key").value.trim()) missing.push("OpenRouter");
  const state = $("keys-state");
  state.textContent = missing.length ? `(need ${missing.join(" and ")})` : "(all set)";
  state.classList.toggle("missing", missing.length > 0);
  if (missing.length) $("keys-drawer").open = true;
}

// ---------- urls and links ----------

function words(text) {
  return text.toLowerCase().match(/[a-z0-9]+/g) || [];
}

// Words in the start URL match every link on the site, so they can't help us choose.
function keywordsFromQuestion(question, startUrl) {
  const skip = new Set([...STOPWORDS, ...words(startUrl)]);
  return [...new Set(words(question).filter((w) => !skip.has(w) && w.length > 2))];
}

// "www.cs.uni.ac.in" -> "uni.ac.in", the same rule as crawl4ai's get_base_domain.
const SECOND_LEVEL = new Set(["co", "com", "org", "gov", "edu", "net", "mil", "int", "ac", "ad", "ae", "af", "ag"]);
function baseDomain(url) {
  const parts = new URL(url).hostname.toLowerCase().replace(/^www\./, "").split(".");
  const keep = parts.length > 2 && SECOND_LEVEL.has(parts.at(-2)) ? 3 : 2;
  return parts.slice(-keep).join(".");
}

function isPdf(url) {
  return new URL(url).pathname.toLowerCase().endsWith(".pdf");
}

function makeLinkFilter(startUrl, filters) {
  const startHost = new URL(startUrl).host.toLowerCase();
  const startBase = baseDomain(startUrl);
  return (link) => {
    const url = new URL(link);
    if (baseDomain(link) !== startBase) return false;
    if (SKIPPED_FILES.test(url.pathname)) return false;
    if (filters.length) return filters.some((f) => link.toLowerCase().includes(f.toLowerCase()));
    const host = url.host.toLowerCase();
    return host === startHost || host.endsWith("." + startHost);
  };
}

// crawl4ai's KeywordRelevanceScorer: the share of keywords found in the URL.
function scoreLink(link, keywords) {
  if (!keywords.length) return 0;
  const lower = link.toLowerCase();
  return keywords.filter((k) => lower.includes(k)).length / keywords.length;
}

function linksIn(html, markdown, baseUrl) {
  let hrefs = [];
  if (html.trim()) {
    const doc = new DOMParser().parseFromString(html, "text/html");
    hrefs = [...doc.querySelectorAll("a[href]")].map((a) => a.getAttribute("href"));
  }
  if (!hrefs.length) {
    // Fallback: markdown links. The cloud's markdown drops links it sees as boilerplate.
    hrefs = [...markdown.matchAll(/\]\(\s*<?([^)\s>]+)/g)].map((m) => m[1]);
  }
  const links = new Set();
  for (const href of hrefs) {
    try {
      const url = new URL(href.trim(), baseUrl);
      url.hash = "";
      if (url.protocol === "http:" || url.protocol === "https:") links.add(url.href);
    } catch { /* not a URL */ }
  }
  return [...links];
}

function htmlToText(html) {
  const doc = new DOMParser().parseFromString(html, "text/html");
  doc.querySelectorAll("script, style, nav, header, footer, noscript").forEach((el) => el.remove());
  return (doc.body?.innerText || doc.body?.textContent || "").replace(/\n{3,}/g, "\n\n").trim();
}

// ---------- fetching pages ----------

const sleep = (ms, signal) => new Promise((resolve, reject) => {
  const timer = setTimeout(resolve, ms);
  signal.addEventListener("abort", () => { clearTimeout(timer); reject(new Stopped()); }, { once: true });
});

async function scrapeLocal(url, signal) {
  const response = await fetch(`${PROXY}/local-scrape`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
    signal,
  });
  const data = await response.json().catch(() => ({}));
  if (response.status === 501 || data.ok === undefined) {
    throw new OutOfCredit("Local mode isn't running here. Pick Crawl4AI Cloud, or run python web/serve.py with crawl4ai installed.");
  }
  if (!data.ok) throw new Error(data.reason || `local ${response.status}`);
  return { text: data.markdown || "", html: data.html || "", credits: 0 };
}

async function scrape(url, key, signal, onWait) {
  let busyTries = 0;
  const giveUpAt = Date.now() + MAX_RATE_LIMIT_WAIT;
  for (;;) {
    const response = await fetch(`${PROXY}/scrape`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${key}` },
      // Markdown for the text, HTML for the links (the markdown loses some of them).
      body: JSON.stringify({ url, format: "both" }),
      signal,
    });
    const credits = parseFloat(response.headers.get("x-c4-cost")) || 0;

    if (response.status === 429 && Date.now() < giveUpAt) {
      // Over the rate limit (the free plan allows 5 pages a minute): wait, then try again.
      const wait = (parseFloat(response.headers.get("retry-after")) || 5) * 1000;
      onWait(`rate limited, waiting ${Math.round(wait / 1000)}s`);
      await sleep(wait, signal);
      continue;
    }
    if (response.status === 503 && busyTries < BUSY_RETRIES) {
      busyTries++;
      await sleep(3000, signal);
      continue;
    }
    if (!(response.headers.get("content-type") || "").includes("json")) {
      // Not an answer from the cloud: this page was not opened through serve.py.
      throw new OutOfCredit("Can't reach the crawl proxy. Run python web/serve.py and open the page it prints.");
    }
    const data = await response.json().catch(() => ({}));
    if (response.status === 402) throw new OutOfCredit(data.message || "Out of Crawl4AI credit.");
    if (response.status === 401 || response.status === 403) {
      throw new OutOfCredit("Crawl4AI didn't accept the key. Check it under \"your keys\".");
    }
    if (!response.ok || !data.ok) {
      throw new Error(`cloud ${response.status}: ${data.reason || data.error || "failed"}`);
    }
    return { text: data.markdown || "", html: data.html || "", credits };
  }
}

let pdfjs = null;
async function pdfText(url, signal) {
  const response = await fetch(`${PROXY}/pdf?url=${encodeURIComponent(url)}`, { signal });
  if (!response.ok) throw new Error(response.status === 413 ? "PDF too big, skipped" : `PDF ${response.status}`);
  const bytes = await response.arrayBuffer();

  if (!pdfjs) {
    pdfjs = await import(PDFJS);
    pdfjs.GlobalWorkerOptions.workerSrc = PDFJS.replace("pdf.min.mjs", "pdf.worker.min.mjs");
  }
  const doc = await pdfjs.getDocument({ data: bytes }).promise;
  const pages = [];
  for (let i = 1; i <= doc.numPages; i++) {
    const content = await (await doc.getPage(i)).getTextContent();
    pages.push(content.items.map((item) => item.str + (item.hasEOL ? "\n" : " ")).join(""));
  }
  return pages.join("\n");
}

// ---------- OpenRouter ----------

async function openrouter(url, body, key, signal) {
  const response = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${key}`,
      "X-Title": "sift",
    },
    body: JSON.stringify(body),
    signal,
  });
  if (response.status === 401) throw new OutOfCredit("OpenRouter didn't accept the key. Check it under \"your keys\".");
  if (response.status === 402) throw new OutOfCredit("Out of OpenRouter credit.");
  if (!response.ok) throw new Error(`OpenRouter ${response.status}: ${(await response.text()).slice(0, 200)}`);
  return response.json();
}

async function askJev(question, url, text, key, signal) {
  const data = await openrouter(JEV_URL, {
    model: "typesafe/jev-1.13",
    state: { question, page_url: url, page_content: text },
    questions: {
      relevant: {
        type: "noul", // Jev's yes/no question type
        instructions: "Does `page_content` contain information that answers `question`? " +
          "A page that clearly answers it with 'no' still counts as yes.",
        criteria: {
          true: "The page has specific information that directly answers the question.",
          false: "The page does not contain information that answers the question.",
        },
      },
    },
  }, key, signal);
  return { score: data.answers.relevant.noul, cost: data.usage?.cost || 0 };
}

async function askLlm(question, url, text, model, key, signal) {
  const data = await openrouter(CHAT_URL, {
    model,
    messages: [{ role: "user", content: PROMPT(question, url, text) }],
    reasoning: { enabled: false }, // a short answer from one page needs no thinking
  }, key, signal);
  const answer = (data.choices?.[0]?.message?.content || "").trim();
  return { answer: answer.startsWith("NOT_FOUND") ? null : answer, cost: data.usage?.cost || 0 };
}

// ---------- the loop ----------

// A crawl keeps all it needs to go on later: the queue, the links seen, the
// totals, the page with the last answer, and the page it was stopped on.
function newCrawl(opts) {
  const keywords = opts.keywords.length ? opts.keywords : keywordsFromQuestion(opts.question, opts.url);
  return {
    opts,
    keywords,
    allowed: makeLinkFilter(opts.url, opts.filters),
    queue: [{ score: 0, depth: 0, url: opts.url }],
    seen: new Set([opts.url]),
    maxPages: opts.maxPages,
    result: { pages: 0, tossed: 0, jevCost: 0, llmCost: 0, credits: 0, keywords, mode: opts.mode },
    found: null,   // the page of the last answer, with the chunk to go on from
    current: null, // the page being worked on now
  };
}

function canContinue(crawl) {
  return Boolean(crawl && (crawl.found || crawl.queue.length));
}

// Highest score first, then lowest depth, then URL: the same order as crawl4ai.
function nextInQueue(queue) {
  queue.sort((a, b) => b.score - a.score || a.depth - b.depth || (a.url < b.url ? -1 : 1));
  return queue.shift();
}

function queueLinks(crawl, item, text, html) {
  if (item.depth >= crawl.opts.maxDepth || isPdf(item.url)) return;
  for (const link of linksIn(html, text, item.url)) {
    if (crawl.seen.has(link) || !crawl.allowed(link)) continue;
    crawl.seen.add(link);
    crawl.queue.push({ score: scoreLink(link, crawl.keywords), depth: item.depth + 1, url: link });
  }
}

// Crawl until a page answers the question, or the queue or page limit runs out.
// Call it again with the same crawl to go on from where it stopped.
async function sift(crawl, run, ui) {
  const { result, opts } = crawl;

  // Going on after an answer that was not it: the rest of that page first, then its links.
  if (crawl.found) {
    // crawl.found stays set until this is done, so a stop here can go on from the same place.
    const { item, row, text, html, score, nextChunk } = crawl.found;
    const hit = await checkPage(crawl, item.url, text, row, nextChunk, run);
    if (hit) {
      crawl.found = { item, row, text, html, ...hit };
      ui.tally(result);
      return { ...result, ...hit };
    }
    crawl.found = null;
    row.set("junk notit", "not it", score);
    result.tossed++;
    ui.tally(result);
    queueLinks(crawl, item, text, html);
  }

  while (crawl.queue.length && result.pages < crawl.maxPages) {
    const item = nextInQueue(crawl.queue);
    result.pages++;
    const row = ui.addPage(result.pages, item.url);
    crawl.current = { item, row };
    let text = null;
    let html = "";

    try {
      if (isPdf(item.url)) {
        row.set("working", "opening");
        text = await pdfText(item.url, run.signal);
      } else {
        row.set("working", "fetching");
        const page = opts.mode === "local"
          ? await scrapeLocal(item.url, run.signal)
          : await scrape(item.url, run.keys.c4, run.signal, (msg) => ui.status(msg, true));
        ({ text, html } = page);
        result.credits += page.credits;
        // Sometimes the cloud's markdown is empty while its HTML has the page.
        if (!text.trim() && html.trim()) text = htmlToText(html);
      }
    } catch (error) {
      if (error instanceof OutOfCredit || error instanceof Stopped || run.signal.aborted) throw error;
      row.set("failed", "failed", null, error.message);
    }
    ui.status("sifting", true);

    if (text !== null) {
      if (text.trim().length < MIN_PAGE_LENGTH) {
        row.set("skipped", "empty");
      } else {
        const hit = await checkPage(crawl, item.url, text, row, 0, run);
        if (hit) {
          crawl.current = null;
          crawl.found = { item, row, text, html, ...hit };
          ui.tally(result);
          return { ...result, ...hit };
        }
        result.tossed++;
      }
    }
    crawl.current = null;
    ui.tally(result);
    if (text !== null) queueLinks(crawl, item, text, html);
  }
  return result;
}

// A stopped page goes back in the queue, so going on starts with it again.
function putBackCurrent(crawl) {
  if (!crawl.current) return;
  crawl.queue.push(crawl.current.item);
  crawl.current.row.remove();
  crawl.result.pages--;
  crawl.current = null;
}

// Ask Jev about each chunk of the page, from chunk `first` on. The first relevant
// chunk that has an answer wins. A page Jev liked but the LLM found no answer in is a "dud".
async function checkPage(crawl, url, text, row, first, { keys, signal }) {
  const { result, opts } = crawl;
  let best = 0;
  for (let i = first; i * CHUNK_SIZE < text.length; i++) {
    const chunk = text.slice(i * CHUNK_SIZE, (i + 1) * CHUNK_SIZE);
    row.set("working", "checking", best);
    const { score, cost } = await askJev(opts.question, url, chunk, keys.or, signal);
    result.jevCost += cost;
    best = Math.max(best, score);
    if (score < RELEVANCE_THRESHOLD) continue;

    row.set("maybe", "reading", best);
    const { answer, cost: llmCost } = await askLlm(opts.question, url, chunk, opts.model, keys.or, signal);
    result.llmCost += llmCost;
    if (answer) {
      row.set("keep", "keep", best);
      return { answer, url, score, nextChunk: i + 1 };
    }
  }
  row.set("junk", best >= RELEVANCE_THRESHOLD ? "dud" : "junk", best);
  return null;
}

// ---------- the page ----------

const ui = {
  addPage(n, url) {
    const li = $("page-row").content.firstElementChild.cloneNode(true);
    li.querySelector(".n").textContent = n;
    const link = li.querySelector(".url");
    link.href = url;
    link.title = url;
    link.textContent = shortUrl(url);
    $("pages").append(li);
    updateFold();
    return {
      remove: () => li.remove(),
      set(state, verdict, score = null, detail = "") {
        li.className = `page ${state}`;
        li.querySelector(".verdict").textContent = score === null || state === "working"
          ? verdict : `${score.toFixed(2)} ${verdict}`;
        li.querySelector(".fill").style.width = score === null ? "" : `${Math.round(score * 100)}%`;
        if (detail) li.title = detail;
      },
    };
  },
  tally(r) {
    $("tally").textContent = `${r.pages} looked at, ${r.tossed} tossed`;
    updateFold();
  },
  status(text, busy = false, error = false) {
    const el = $("status");
    el.textContent = text;
    el.classList.toggle("busy", busy);
    el.classList.toggle("error", error);
  },
};

// The pile shows its first few pages. The rest fold away, except pages that
// matter: the one being checked, and any past the line. Keep in step with style.css.
const PILE_SHOWN = 8;
const PILE_HIDDEN = `.page:nth-child(n+${PILE_SHOWN + 1}):not(.working):not(.maybe):not(.keep):not(.notit)`;

function updateFold() {
  const pile = $("pages");
  const hidden = pile.querySelectorAll(PILE_HIDDEN).length;
  const fold = $("fold");
  fold.hidden = hidden === 0;
  fold.textContent = pile.classList.contains("collapsed")
    ? `show ${hidden} more` : "show less";
}

$("fold").addEventListener("click", () => {
  $("pages").classList.toggle("collapsed");
  updateFold();
});

function shortUrl(url) {
  const u = new URL(url);
  const path = decodeURIComponent(u.pathname + u.search);
  return path === "/" ? u.host : u.host.replace(/^www\./, "") + path;
}

function money(n) {
  if (!n) return "$0";
  return "$" + n.toFixed(n < 0.01 ? 6 : 4);
}

function showResult(r, crawl) {
  const box = $("result");
  box.hidden = false;
  box.replaceChildren();

  if (r.answer) {
    // The model ends with "Source: <url>". We show the source ourselves, as a link.
    const answer = r.answer.replace(/\n?\s*Source:.*$/is, "").trim();
    box.innerHTML = `
      <div class="found">
        <h2><mark>found it.</mark></h2>
        <p class="answer"></p>
        <p class="source">from <a target="_blank" rel="noopener"></a></p>
        <dl class="receipt"></dl>
      </div>`;
    box.querySelector(".answer").textContent = answer;
    const src = box.querySelector(".source a");
    src.href = r.url;
    src.textContent = r.url;
  } else {
    box.innerHTML = `
      <div class="nothing">
        <h2></h2>
        <p></p>
        <dl class="receipt"></dl>
      </div>`;
    const pages = `${r.pages} page${r.pages === 1 ? "" : "s"}`;
    box.querySelector("h2").textContent = r.stopped ? "stopped." : "nothing here.";
    box.querySelector("p").textContent = r.failed
      ? `It stopped after ${pages} because of the problem above. Fix it, then keep going.`
      : r.stopped
      ? `You stopped it after ${pages}. Here is what that cost.`
      : !r.pages
      ? "Didn't get to look at any pages."
      : canContinue(crawl)
      ? `Went through ${pages} and all of it was junk. There are more links to try.`
      : `Went through ${pages} and all of it was junk. That was every link it could follow. ` +
        "Try a filter, some link words, or more depth under \"knobs\".";
  }

  const rows = [
    ["pages", String(r.pages)],
    r.mode === "local" ? ["crawl", "on your machine, free"] : ["crawl4ai credits", r.credits.toFixed(1)],
    ["jev", money(r.jevCost)],
    ["answer model", money(r.llmCost)],
    ["openrouter total", money(r.jevCost + r.llmCost)],
  ];
  const receipt = box.querySelector(".receipt");
  rows.forEach(([k, v], i) => {
    const dt = document.createElement("dt");
    const dd = document.createElement("dd");
    dt.textContent = k;
    dd.textContent = v;
    if (i === rows.length - 1) { dt.className = dd.className = "total"; }
    receipt.append(dt, dd);
  });

  if (canContinue(crawl)) {
    const more = document.createElement("button");
    more.type = "button";
    more.className = "more";
    const atLimit = !crawl.found && crawl.result.pages >= crawl.maxPages;
    more.textContent = r.answer ? "not it? keep looking"
      : atLimit ? `look at ${crawl.opts.maxPages} more pages` : "keep going";
    more.addEventListener("click", () => {
      if (atLimit) crawl.maxPages += crawl.opts.maxPages;
      runCrawl(crawl);
    });
    box.firstElementChild.append(more);
  }
}

function list(value) {
  return value.split(",").map((s) => s.trim().toLowerCase()).filter(Boolean);
}

function currentKeys() {
  return { c4: $("c4-key").value.trim(), or: $("or-key").value.trim() };
}

let controller = null;

// Run a new crawl, or go on with one from where it stopped.
async function runCrawl(crawl) {
  const keys = currentKeys();
  if ((crawl.opts.mode === "cloud" && !keys.c4) || !keys.or) {
    ui.status("add your keys first", false, true);
    return;
  }

  controller = new AbortController();
  $("go").disabled = true;
  $("stop").hidden = false;
  $("result").hidden = true;
  $("run").hidden = false;
  ui.status("sifting", true);

  try {
    const result = await sift(crawl, { keys, signal: controller.signal }, ui);
    showResult(result, crawl);
    ui.status(result.answer ? "" : "done");
  } catch (error) {
    putBackCurrent(crawl);
    document.querySelectorAll(".page.working, .page.maybe").forEach((li) => {
      li.className = "page skipped";
      li.querySelector(".verdict").textContent = "stopped";
    });
    ui.tally(crawl.result);
    if (error instanceof Stopped || controller.signal.aborted) {
      ui.status("stopped.");
      showResult({ ...crawl.result, stopped: true }, crawl);
    } else {
      ui.status(error.message, false, true);
      // Keys and credit can be fixed, so offer to go on from here.
      showResult({ ...crawl.result, stopped: true, failed: true }, crawl);
    }
  } finally {
    $("go").disabled = false;
    $("stop").hidden = true;
  }
}

$("ask").addEventListener("submit", (event) => {
  event.preventDefault();
  saveKeys();
  showKeyState();

  let url = $("url").value.trim();
  if (!/^https?:\/\//i.test(url)) url = "https://" + url;
  try {
    // "https://site.com" and "https://site.com/" are the same page. Links always have the "/".
    const parsed = new URL(url);
    parsed.hash = "";
    url = parsed.href;
  } catch {
    ui.status("that website address doesn't look right", false, true);
    return;
  }

  $("pages").replaceChildren();
  $("pages").classList.add("collapsed");
  $("tally").textContent = "";
  updateFold();

  runCrawl(newCrawl({
    url,
    question: $("question").value.trim(),
    filters: list($("filter").value),
    keywords: list($("keywords").value),
    maxPages: Math.max(1, parseInt($("max-pages").value, 10) || 50),
    maxDepth: Math.max(0, parseInt($("max-depth").value, 10) || 0),
    model: $("model").value.trim() || "deepseek/deepseek-v4.1-flash",
    mode: mode(),
  }));
});

$("stop").addEventListener("click", () => controller?.abort());

for (const button of document.querySelectorAll(".example")) {
  button.addEventListener("click", () => {
    $("url").value = button.dataset.url;
    $("question").value = button.dataset.q;
    $("filter").value = button.dataset.filter || "";
    $("question").focus();
  });
}

for (const id of ["c4-key", "or-key"]) $(id).addEventListener("input", showKeyState);
$("remember").addEventListener("change", saveKeys);

for (const radio of document.querySelectorAll('input[name="mode"]')) {
  radio.addEventListener("change", () => {
    store("set", KEY_NAMES.mode, radio.value);
    showKeyState();
  });
}

// Offer local mode only when serve.py can run crawl4ai. Pick the saved mode, or local when it works.
async function loadMode() {
  let available = false;
  try {
    const response = await fetch(`${PROXY}/local-status`);
    available = (await response.json()).available === true;
  } catch { /* no serve.py, or an old one */ }

  const localRadio = document.querySelector('input[name="mode"][value="local"]');
  localRadio.disabled = !available;
  if (!available) $("local-note").textContent = "not available here";

  const saved = store("get", KEY_NAMES.mode);
  const pick = saved === "cloud" || !available ? "cloud" : "local";
  document.querySelector(`input[name="mode"][value="${pick}"]`).checked = true;
  showKeyState();
}

loadKeys();
loadMode();

"""Step 3: ask an LLM on OpenRouter for a short answer from one page."""

from .openrouter import post

CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"

# Open-weight, 1M-token context, and a fraction of a cent per answer.
# Any OpenRouter model works: pass --model on the command line.
DEFAULT_MODEL = "deepseek/deepseek-v4.1-flash"

PROMPT = """\
Question: {question}

Source URL: {url}

Page content:
<page>
{text}
</page>

Answer the question using ONLY the page content above. Reply in 1-3 sentences
and end with the line 'Source: <url>'. If the page content does not answer the
question, reply with exactly NOT_FOUND and nothing else."""


def make_answerer(model=DEFAULT_MODEL):
    """Return a function that answers (question, url, text) with `model`."""

    def ask_llm(question, url, text):
        """Return (short answer or None if the page has no answer, cost in USD)."""
        data = post(CHAT_URL, {
            "model": model,
            "messages": [{"role": "user", "content": PROMPT.format(question=question, url=url, text=text)}],
            "reasoning": {"enabled": False},  # a short answer from one page needs no thinking
        })
        answer = (data["choices"][0]["message"]["content"] or "").strip()
        cost = data.get("usage", {}).get("cost", 0.0)
        return (None if answer.startswith("NOT_FOUND") else answer), cost

    return ask_llm

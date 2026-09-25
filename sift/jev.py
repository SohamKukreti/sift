"""Step 2: ask Jev "does this page answer the question?"

Jev is a decision model on OpenRouter. It does not write text. It returns a
probability, so our code can branch on it directly.
"""

from .openrouter import post

JEV_URL = "https://openrouter.ai/api/alpha/decisions"
JEV_MODEL = "typesafe/jev-1.13"


def ask_jev(question, url, text):
    """Return (probability that the page answers the question, cost in USD)."""
    request = {
        "model": JEV_MODEL,
        "state": {
            "question": question,
            "page_url": url,
            "page_content": text,
        },
        "questions": {
            "relevant": {
                "type": "noul",  # Jev's yes/no question type
                "instructions": (
                    "Does `page_content` contain information that answers `question`? "
                    "A page that clearly answers it with 'no' still counts as yes."
                ),
                "criteria": {
                    "true": "The page has specific information that directly answers the question.",
                    "false": "The page does not contain information that answers the question.",
                },
            },
        },
    }
    data = post(JEV_URL, request, timeout=60)

    probability = data["answers"]["relevant"]["noul"]
    cost = data.get("usage", {}).get("cost", 0.0)
    return probability, cost

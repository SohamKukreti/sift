"""Step 3: ask Claude (the local `claude` CLI) for a short answer."""

import re
import subprocess

CLAUDE_COMMAND = [
    "claude", "-p",
    "--model", "sonnet",
    "--tools", "",               # no tools: Claude may only use the text we give it
    "--strict-mcp-config",
    "--no-session-persistence",
]

CLAUDE_PROMPT = """\
Question: {question}

Source URL: {url}

Page content:
<page>
{text}
</page>

Answer the question using ONLY the page content above. Reply in 1-3 sentences
and end with the line 'Source: <url>'. If the page content does not answer the
question, reply with exactly NOT_FOUND and nothing else."""


def ask_claude(question, url, text):
    """Return a short answer, or None if Claude finds no answer in the text."""
    prompt = CLAUDE_PROMPT.format(question=question, url=url, text=text)
    result = subprocess.run(CLAUDE_COMMAND, input=prompt, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise RuntimeError(f"claude CLI failed: {result.stderr.strip()[:300]}")

    # Some CLI setups put a "[hh:mm:ss]" timestamp in front of the output.
    answer = re.sub(r"^\[\d{1,2}:\d{2}(:\d{2})?\]\s*", "", result.stdout.strip())
    return None if answer.startswith("NOT_FOUND") else answer

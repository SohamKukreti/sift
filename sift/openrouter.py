"""One small helper for OpenRouter. Jev and the answer model both use the same key."""

import os

import requests


def post(url, body, timeout=120):
    """POST `body` to an OpenRouter endpoint and return the JSON reply."""
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not set. Put it in a .env file or export it.")

    response = requests.post(
        url,
        json=body,
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=timeout,
    )
    if not response.ok:
        raise RuntimeError(f"OpenRouter error {response.status_code}: {response.text[:300]}")
    return response.json()

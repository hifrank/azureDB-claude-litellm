"""
Local test script for the LiteLLM proxy (stdlib only, no extra dependencies).

Requires environment variables (set in .env or shell):
  LITELLM_PROXY_URL  — e.g. http://<host>:4000/v1
  LITELLM_PROXY_KEY  — the proxy master key

Usage:
    python test_proxy.py
    LITELLM_PROXY_URL=http://localhost:4000/v1 LITELLM_PROXY_KEY=sk-xxx python test_proxy.py
"""

import json
import os
import sys
import urllib.request

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

PROXY_URL = os.environ.get("LITELLM_PROXY_URL", "http://localhost:4000/v1")
API_KEY = os.environ.get("LITELLM_PROXY_KEY", "")

if not API_KEY:
    print("ERROR: LITELLM_PROXY_KEY environment variable is not set.")
    print("Set it in .env or pass it directly: LITELLM_PROXY_KEY=sk-xxx python test_proxy.py")
    sys.exit(1)

HEADERS = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {API_KEY}",
}


def test_health():
    """Check if the proxy is reachable."""
    req = urllib.request.Request(f"{PROXY_URL}/models", headers=HEADERS)
    resp = urllib.request.urlopen(req, timeout=10)
    data = json.loads(resp.read())
    models = [m["id"] for m in data["data"]]
    print(f"PASS  /v1/models — {len(models)} models: {', '.join(models)}")
    return models


def test_chat(model):
    """Send a chat completion request."""
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "What is 2+2? Reply with just the number."}],
        "max_tokens": 32,
    }).encode()
    req = urllib.request.Request(f"{PROXY_URL}/chat/completions", data=body, headers=HEADERS)
    resp = urllib.request.urlopen(req, timeout=30)
    data = json.loads(resp.read())
    content = data["choices"][0]["message"]["content"]
    usage = data["usage"]
    print(f"PASS  chat/{model} — \"{content.strip()}\" ({usage['total_tokens']} tokens)")


def test_embedding(model):
    """Send an embedding request."""
    body = json.dumps({
        "model": model,
        "input": ["Hello world"],
    }).encode()
    req = urllib.request.Request(f"{PROXY_URL}/embeddings", data=body, headers=HEADERS)
    resp = urllib.request.urlopen(req, timeout=30)
    data = json.loads(resp.read())
    dims = len(data["data"][0]["embedding"])
    print(f"PASS  embeddings/{model} — {dims} dimensions")


def main():
    print(f"Testing LiteLLM proxy at {PROXY_URL}\n")
    failures = 0

    # 1. Health / model list
    try:
        models = test_health()
    except Exception as e:
        print(f"FAIL  /v1/models — {e}")
        print("\nProxy is not reachable. Is the VM running?")
        sys.exit(1)

    # 2. Chat completion tests
    chat_models = [
        "databricks-claude-sonnet-4-6",
        "databricks-meta-llama-3-3-70b-instruct",
    ]
    for m in chat_models:
        if m not in models:
            print(f"SKIP  chat/{m} — not in model list")
            continue
        try:
            test_chat(m)
        except Exception as e:
            print(f"FAIL  chat/{m} — {e}")
            failures += 1

    # 3. Embedding test
    emb_model = "databricks-gte-large-en"
    if emb_model in models:
        try:
            test_embedding(emb_model)
        except Exception as e:
            print(f"FAIL  embeddings/{emb_model} — {e}")
            failures += 1
    else:
        print(f"SKIP  embeddings/{emb_model} — not in model list")

    # Summary
    print(f"\nDone. {'All tests passed!' if failures == 0 else f'{failures} test(s) failed.'}")
    sys.exit(failures)


if __name__ == "__main__":
    main()

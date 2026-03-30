"""
Local test script using the OpenAI SDK against the LiteLLM proxy.

The proxy translates OpenAI-compatible requests to Azure Databricks Model Serving.

Requires environment variables (set in .env or shell):
  LITELLM_PROXY_URL  — e.g. http://<host>:4000/v1
  LITELLM_PROXY_KEY  — the proxy master key

Usage:
    pip install openai python-dotenv
    python test_openai_sdk.py
"""

import os
import sys

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from openai import OpenAI

PROXY_URL = os.environ.get("LITELLM_PROXY_URL", "http://localhost:4000/v1")
API_KEY = os.environ.get("LITELLM_PROXY_KEY", "")

if not API_KEY:
    print("ERROR: LITELLM_PROXY_KEY environment variable is not set.")
    print("Set it in .env or pass it directly: LITELLM_PROXY_KEY=sk-xxx python test_openai_sdk.py")
    sys.exit(1)

client = OpenAI(base_url=PROXY_URL, api_key=API_KEY)


def test_models():
    """List available models."""
    models = [m.id for m in client.models.list()]
    print(f"PASS  /v1/models — {len(models)} models: {', '.join(models)}")
    return models


def test_chat(model):
    """Chat completion test."""
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": "What is 2+2? Reply with just the number."}],
        max_tokens=32,
    )
    content = response.choices[0].message.content.strip()
    tokens = response.usage.total_tokens
    print(f"PASS  chat/{model} — \"{content}\" ({tokens} tokens)")


def test_streaming(model):
    """Streaming chat completion test."""
    stream = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": "Count from 1 to 5."}],
        max_tokens=64,
        stream=True,
    )
    chunks = []
    for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content:
            chunks.append(chunk.choices[0].delta.content)
    result = "".join(chunks).strip()
    print(f"PASS  stream/{model} — \"{result}\" ({len(chunks)} chunks)")


def test_embedding(model):
    """Embedding test."""
    response = client.embeddings.create(
        model=model,
        input=["Hello world"],
    )
    dims = len(response.data[0].embedding)
    print(f"PASS  embeddings/{model} — {dims} dimensions")


def main():
    print(f"Testing LiteLLM proxy at {PROXY_URL}")
    print(f"Using OpenAI Python SDK\n")
    failures = 0

    # 1. Model list
    try:
        models = test_models()
    except Exception as e:
        print(f"FAIL  /v1/models — {e}")
        print("\nProxy is not reachable. Is the VM running?")
        sys.exit(1)

    # 2. Chat completion
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

    # 3. Streaming
    stream_model = "databricks-claude-sonnet-4-6"
    if stream_model in models:
        try:
            test_streaming(stream_model)
        except Exception as e:
            print(f"FAIL  stream/{stream_model} — {e}")
            failures += 1

    # 4. Embedding
    emb_model = "databricks-gte-large-en"
    if emb_model in models:
        try:
            test_embedding(emb_model)
        except Exception as e:
            print(f"FAIL  embeddings/{emb_model} — {e}")
            failures += 1

    # Summary
    print(f"\nDone. {'All tests passed!' if failures == 0 else f'{failures} test(s) failed.'}")
    sys.exit(failures)


if __name__ == "__main__":
    main()

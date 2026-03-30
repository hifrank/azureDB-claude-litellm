"""
Use LiteLLM to call Azure Databricks Model Serving endpoints.

Requires environment variables (set in .env or shell):
  DATABRICKS_API_KEY   — Personal Access Token or Service Principal secret
  DATABRICKS_API_BASE  — https://<workspace>.azuredatabricks.net/serving-endpoints

Usage:
  python main.py [--chat-model MODEL] [--embed-model MODEL]
"""

import argparse
import os

from dotenv import load_dotenv
from litellm import completion, embedding

load_dotenv()

DEFAULT_CHAT_MODEL = "databricks/databricks-claude-sonnet-4-6"
DEFAULT_EMBED_MODEL = "databricks/databricks-gte-large-en"


def chat_example(model: str):
    response = completion(
        model=model,
        messages=[{"role": "user", "content": "What is Azure Databricks in one sentence?"}],
        max_tokens=256,
    )
    print("=== Chat Completion ===")
    print(f"Model: {model}")
    print(response.choices[0].message.content)
    print(f"Tokens: {response.usage.prompt_tokens} prompt / {response.usage.completion_tokens} completion")


def embedding_example(model: str):
    response = embedding(
        model=model,
        input=["Azure Databricks model serving with LiteLLM"],
    )
    print("\n=== Embedding ===")
    print(f"Model: {model}")
    vec = response.data[0]["embedding"]
    print(f"Dimensions: {len(vec)}, first 5 values: {vec[:5]}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="LiteLLM + Azure Databricks demo")
    parser.add_argument("--chat-model", default=DEFAULT_CHAT_MODEL, help=f"Chat model (default: {DEFAULT_CHAT_MODEL})")
    parser.add_argument("--embed-model", default=DEFAULT_EMBED_MODEL, help=f"Embedding model (default: {DEFAULT_EMBED_MODEL})")
    args = parser.parse_args()

    chat_example(args.chat_model)
    embedding_example(args.embed_model)

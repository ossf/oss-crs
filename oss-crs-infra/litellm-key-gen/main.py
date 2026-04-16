#!/usr/bin/env python3
import os
import yaml
import requests


def _read_secret(path: str) -> str | None:
    """Read a Docker secret file."""
    try:
        with open(path) as f:
            return f.read().strip()
    except FileNotFoundError:
        return None


LITELLM_MASTER_KEY = _read_secret("/run/secrets/litellm_master_key")
LITELLM_API_URL = os.getenv("LITELLM_API_URL")


def create_llm_key(key: str, budget: int) -> str | None:
    """
    Create an LLM API key using LiteLLM's key/generate endpoint.

    Args:
        key: specified key
        budget: Max budget for this key (in USD)

    Returns:
        The generated API key string, or None if failed
    """
    url = f"{LITELLM_API_URL}/key/generate"
    headers = {
        "Authorization": f"Bearer {LITELLM_MASTER_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "key": key,
        "max_budget": budget,
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=30)
        response.raise_for_status()
        data = response.json()
        assert data.get("key") == key
        return key
    except requests.exceptions.RequestException as e:
        print(f"Error creating LLM key: {e}")
        return None


def get_available_models() -> list[str]:
    """
    Get list of available models from LiteLLM.

    Returns:
        List of model names/IDs available on the LiteLLM instance
    """
    url = f"{LITELLM_API_URL}/models"
    headers = {
        "Authorization": f"Bearer {LITELLM_MASTER_KEY}",
    }

    try:
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        data = response.json()
        # LiteLLM returns {"data": [{"id": "model-name", ...}, ...]}
        models = data.get("data", [])
        return [model.get("id") for model in models if model.get("id")]
    except requests.exceptions.RequestException as e:
        print(f"Error fetching available models: {e}")
        return []


def main():
    yaml_path = "/key_gen_request.yaml"
    with open(yaml_path, "r") as f:
        key_requests = yaml.safe_load(f)
    available_models = get_available_models()
    print("available models:")
    for model in available_models:
        print(f" - {model}")

    for crs_name, info in key_requests.items():
        required_models = info.get("required_llms") or []
        for model in required_models:
            if model not in available_models:
                print(
                    f"Error: Required model '{model}' for CRS '{crs_name}' is not available."
                )
                return 1
        api_key = create_llm_key(
            info["api_key"],
            info["llm_budget"],
        )
        if api_key:
            print(f"Generated API key for CRS '{crs_name}': {api_key}")
        else:
            print(f"Failed to generate API key for CRS '{crs_name}'")
            return 1


if __name__ == "__main__":
    exit(main())

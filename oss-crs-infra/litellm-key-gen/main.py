#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
import json
import os
import signal
import time
import yaml
import requests


def _read_secret(path: str) -> str:
    """Read a Docker secret file."""
    with open(path) as f:
        return f.read().strip()


LITELLM_MASTER_KEY = _read_secret("/run/secrets/litellm_master_key")
LITELLM_API_URL = os.getenv("LITELLM_API_URL")
READY_FILE_PATH = os.getenv("LITELLM_KEY_GEN_READY_FILE", "/tmp/litellm-key-gen.ready")
SPEND_REPORT_PATH = os.getenv("LITELLM_SPEND_REPORT_PATH", "/litellm-spend-report.json")
SPEND_POLL_INTERVAL_SEC = int(os.getenv("LITELLM_SPEND_POLL_INTERVAL_SEC", "5"))

_SHUTDOWN = False


def _handle_signal(_signum, _frame):
    global _SHUTDOWN
    _SHUTDOWN = True


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


def get_key_spend(api_key: str) -> float:
    """Fetch cumulative spend for a single key via /key/info."""
    url = f"{LITELLM_API_URL}/key/info"
    headers = {
        "Authorization": f"Bearer {LITELLM_MASTER_KEY}",
    }
    try:
        response = requests.get(
            url, headers=headers, params={"key": api_key}, timeout=30
        )
        response.raise_for_status()
        data = response.json()
        info = data.get("info") or data
        spend = info.get("spend")
        if isinstance(spend, (int, float)):
            return float(spend)
        return 0.0
    except requests.exceptions.RequestException as e:
        print(f"Error fetching spend for key: {e}")
        return 0.0


def collect_spend_summary(key_requests: dict[str, dict]) -> dict:
    """Build spend summary by querying per-key spend from LiteLLM."""
    crs_summary: dict[str, dict[str, float]] = {}
    total = 0.0
    for crs_name, info in key_requests.items():
        api_key = str(info.get("api_key", ""))
        spend = get_key_spend(api_key) if api_key else 0.0
        crs_summary[crs_name] = {"credits_used": round(spend, 6)}
        total += spend
    return {
        "totals": {"credits_used": round(total, 6)},
        "crs": crs_summary,
        "updated_at": int(time.time()),
    }


def write_spend_summary(summary: dict) -> None:
    with open(SPEND_REPORT_PATH, "w") as f:
        json.dump(summary, f, indent=2, sort_keys=True)
        f.write("\n")


def main():
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

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

    # Mark key generation ready for healthcheck-gated CRS startup.
    with open(READY_FILE_PATH, "w") as f:
        f.write("ready\n")

    # Poll LiteLLM spend and keep writing a host-recoverable summary file.
    while not _SHUTDOWN:
        summary = collect_spend_summary(key_requests)
        write_spend_summary(summary)
        time.sleep(max(SPEND_POLL_INTERVAL_SEC, 1))

    return 0


if __name__ == "__main__":
    exit(main())

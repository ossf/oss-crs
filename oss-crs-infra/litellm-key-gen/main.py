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


def get_global_spend_report() -> dict | list | None:
    """Fetch global spend report from LiteLLM."""
    url = f"{LITELLM_API_URL}/global/spend/report"
    headers = {
        "Authorization": f"Bearer {LITELLM_MASTER_KEY}",
    }
    try:
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Error fetching global spend report: {e}")
        return None


def _iter_dicts(obj):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _iter_dicts(v)
    elif isinstance(obj, list):
        for item in obj:
            yield from _iter_dicts(item)


def _as_float(value) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def summarize_spend_report(report, key_requests: dict[str, dict]) -> dict:
    """Build a stable summary with totals and per-CRS credits used."""
    per_key: dict[str, float] = {}

    if report is not None:
        for entry in _iter_dicts(report):
            key = None
            for key_field in ("api_key", "key", "user_api_key", "token"):
                raw_key = entry.get(key_field)
                if isinstance(raw_key, str) and raw_key:
                    key = raw_key
                    break
            if key is None:
                continue

            spend = None
            for spend_field in ("spend", "total_spend", "credits_used", "cost"):
                spend = _as_float(entry.get(spend_field))
                if spend is not None:
                    break
            if spend is None:
                continue
            per_key[key] = per_key.get(key, 0.0) + spend

    crs_summary: dict[str, dict[str, float]] = {}
    for crs_name, info in key_requests.items():
        api_key = str(info.get("api_key", ""))
        crs_summary[crs_name] = {"credits_used": round(per_key.get(api_key, 0.0), 6)}

    total = round(sum(v["credits_used"] for v in crs_summary.values()), 6)
    return {
        "totals": {"credits_used": total},
        "crs": crs_summary,
        "updated_at": int(time.time()),
    }


def write_spend_summary(summary: dict) -> None:
    dst = SPEND_REPORT_PATH
    tmp = f"{dst}.tmp"
    with open(tmp, "w") as f:
        json.dump(summary, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, dst)


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

    # Poll LiteLLM spend report and keep writing a host-recoverable summary file.
    while not _SHUTDOWN:
        report = get_global_spend_report()
        summary = summarize_spend_report(report, key_requests)
        write_spend_summary(summary)
        time.sleep(max(SPEND_POLL_INTERVAL_SEC, 1))

    return 0


if __name__ == "__main__":
    exit(main())

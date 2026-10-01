#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
import hashlib
import json
import os
import signal
import time
from datetime import datetime, timedelta, timezone

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


def _today_str() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _end_date_str() -> str:
    """Exclusive window end for spend-log queries.

    LiteLLM treats ``end_date`` as exclusive: querying with
    ``start_date == end_date == today`` always returns zero rows, so the
    end of the window must be tomorrow to include today's traffic.
    """
    return (datetime.now(timezone.utc).date() + timedelta(days=1)).isoformat()


def get_key_tokens(
    api_key: str,
    start_date: str | None = None,
    end_date: str | None = None,
) -> tuple[int, int]:
    """Fetch cumulative (prompt, completion) tokens for a key.

    Sums ``prompt_tokens``/``completion_tokens`` over this key's rows in
    LiteLLM's spend logs (provider ``usage`` verbatim), independent of the
    price map — so models missing from the cost map still report tokens
    while ``spend`` stays 0. Rows are matched by key hash, the form
    LiteLLM stores in ``api_key``. Fail-open: any error yields (0, 0) so
    dollar polling never breaks.
    """
    if not api_key:
        return (0, 0)
    headers = {
        "Authorization": f"Bearer {LITELLM_MASTER_KEY}",
    }
    start = start_date or _today_str()
    end = end_date or _end_date_str()
    try:
        key_hash = hashlib.sha256(api_key.encode()).hexdigest()
        response = requests.get(
            f"{LITELLM_API_URL}/spend/logs",
            headers=headers,
            params={"start_date": start, "end_date": end, "summarize": "false"},
            timeout=30,
        )
        response.raise_for_status()
        prompt = 0
        completion = 0
        for row in response.json():
            if row.get("api_key") not in (api_key, key_hash):
                continue
            prompt += int(row.get("prompt_tokens", 0) or 0)
            completion += int(row.get("completion_tokens", 0) or 0)
        return prompt, completion
    except Exception as e:
        print(f"Error fetching token usage for key: {e}")
        return (0, 0)


def collect_spend_summary(
    key_requests: dict[str, dict],
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict:
    """Build spend summary by querying per-key spend and tokens from LiteLLM."""
    crs_summary: dict[str, dict[str, float | int]] = {}
    total_spend = 0.0
    total_prompt = 0
    total_completion = 0
    start = start_date or _today_str()
    end = end_date or _end_date_str()
    for crs_name, info in key_requests.items():
        api_key = str(info.get("api_key", ""))
        spend = get_key_spend(api_key) if api_key else 0.0
        prompt, completion = get_key_tokens(api_key, start, end) if api_key else (0, 0)
        crs_summary[crs_name] = {
            "credits_used": round(spend, 6),
            "prompt_tokens": prompt,
            "completion_tokens": completion,
        }
        total_spend += spend
        total_prompt += prompt
        total_completion += completion
    return {
        "totals": {
            "credits_used": round(total_spend, 6),
            "prompt_tokens": total_prompt,
            "completion_tokens": total_completion,
        },
        "crs": crs_summary,
        "updated_at": int(time.time()),
    }


def write_spend_summary(summary: dict) -> None:
    """Write the spend summary atomically.

    A SIGKILL landing mid-write must never leave a truncated report behind
    (readers turn that into zeros), so the payload goes to a temp file in
    the same directory first and is moved into place with os.replace.
    """
    tmp_path = SPEND_REPORT_PATH + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(summary, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp_path, SPEND_REPORT_PATH)


def _poll_and_persist(key_requests: dict[str, dict], start_date: str) -> None:
    """One poll cycle: collect the spend summary and persist it.

    Shared by the polling loop and the shutdown path so the final flush
    exercises exactly the same code as every poll.
    """
    summary = collect_spend_summary(key_requests, start_date=start_date)
    write_spend_summary(summary)


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

    # Fixed window start so token queries capture the entire run.
    # End date is refreshed every poll inside collect_spend_summary.
    run_start_date = _today_str()

    # Poll LiteLLM spend and tokens, keep writing host-recoverable summary.
    while not _SHUTDOWN:
        _poll_and_persist(key_requests, run_start_date)
        time.sleep(max(SPEND_POLL_INTERVAL_SEC, 1))

    # Final flush: capture traffic from the last partial interval
    try:
        _poll_and_persist(key_requests, run_start_date)
    except Exception as e:
        print(f"Error in final spend flush: {e}")

    return 0


if __name__ == "__main__":
    exit(main())

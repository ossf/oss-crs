# LLM Providers Through LiteLLM Proxy

## Local model hosted on domain

The LiteLLM proxy can forward requests to any OpenAI-compatible servers. [vLLM](https://docs.vllm.ai/en/stable/serving/openai_compatible_server/) in particular is verified by us to be compatible. 

In OSS-CRS, you will want to set a custom LiteLLM proxy configuration.

You can provide any referenced credentials either through exported shell variables or a `.env` file in the directory where you run `oss-crs`. The CLI loads `.env` automatically via dotenv.

As documented in [LiteLLM Providers](https://docs.litellm.ai/docs/providers/openai_compatible#usage-with-litellm-proxy-server), the important part is to prefix your available model names with `openai/` for `model_list[].litellm_params.model`. However, you can still use your original model name or alias them with `model_list[].model_name`.

```yaml example/test-local/litellm-config.yaml
model_list:
- model_name: "claude-opus-4-5-20251101"    # alias model name to the ones CRS uses
  litellm_params:
    model: "openai/Qwen/Qwen3-0.6B"         # openai/{MODEL_NAME}
    api_key: os.environ/VLLM_KEY            # set in local model server
    api_base: https://example.com/v1        # known domain
```

The LiteLLM config is referenced in your CRS compose file:

```yaml example/test-local/compose.yaml
# --- LLM Configuration -----------------------------------------------------
llm_config:
  litellm:
    mode: internal
    internal:
      config_path: ./example/test-local/litellm-config.yaml
```

The environment that was tested looks like the following.

```
+-------------+                    +---------------+
| Local Model | -- HTTP :8000 -->  | Reverse Proxy |
+-------------+                    +---------------+
                                           ^
                                           |
                                        HTTPS /v1
                                           |
                                       +---------+
                                       | OSS-CRS |
                                       +---------+
```

## Local model hosted on local machine

We also tested OSS-CRS in a completely local setting where CRSs are run on the same machine models are hosted on (e.g. desktops). You can set the host as the Docker interface IP. Make sure expose the LLM server's port on the Docker interface in your firewall.

```yaml example/test-local/litellm-config.yaml
model_list:
- model_name: "claude-opus-4-5-20251101"    # alias model name to the ones CRS uses
  litellm_params:
    model: "openai/Qwen/Qwen3-0.6B"         # openai/{MODEL_NAME}
    api_key: os.environ/VLLM_KEY            # set in local model server
    api_base: http://172.17.0.1:8000/v1     # IP retrieved from `ip addr show docker0`
```

The environment that was tested looks like the following.

```
+-------------+
| Local Model |
|     ^       |
|     |       |
|  HTTP /v1   |
|     |       |
|  OSS-CRS    |
+-------------+
```

# Verifying LiteLLM Proxy

We added a CRS called `test-local` to check the LiteLLM proxy forwarding.

You'll need to first update `example/test-local/litellm-config.yaml` with your key, model names, and endpoint URL.

```sh
# Set LLM key (can rename environment variable, see NOTE in litellm-config.yaml)
export VLLM_KEY=<SECRET_KEY>
# Or place VLLM_KEY=<SECRET_KEY> in .env and run the same commands below.

# Prepare the CRS
uv run oss-crs prepare --compose-file example/test-local/compose.yaml

# Build the target (no-op for the sake of demo)
uv run oss-crs build-target --compose-file example/test-local/compose.yaml \
    --fuzz-proj-path <PATH_TO_OSS_FUZZ_PROJ>/json-c

# Should say hello from LLM
uv run oss-crs run --compose-file example/test-local/compose.yaml \
    --fuzz-proj-path <PATH_TO_OSS_FUZZ_PROJ>/json-c \
    --target-harness json_array_fuzzer
```

# Known Issues with Aliasing Models

Recent LLMs like GPT-5 no longer support the `temperature` parameter. This can cause silent failures if you’re swapping model backends in LiteLLM while keeping the original model name (e.g., model name set to gpt-4o but relay configured to gpt-5). The temperature param gets passed through and the API rejects it.

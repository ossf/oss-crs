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

# Endpoints Behind an Internal CA

If your LLM endpoint presents a certificate that chains to an internal corporate CA,
the chain is valid but that CA is not in the trust store of the containers OSS-CRS
starts, so TLS verification fails. Point OSS-CRS at the CA and it verifies normally —
there is no option to skip verification, and you should not need one.

Supply a PEM file holding the CA certificate (concatenate the root and any
intermediates into one file), in any of three ways:

```sh
# Once per machine, in your shell or a .env file in the directory you run oss-crs from
export OSS_CRS_EXTRA_CA_CERTS=/etc/pki/corp-root.pem

# Or per invocation
uv run oss-crs run --compose-file ... --extra-ca-certs /etc/pki/corp-root.pem
```

```yaml
# Or in the CRS compose file. Supports ~ and ${VAR}, so a compose file you commit
# can defer to the environment instead of hardcoding a machine-specific path.
extra_ca_certs: ${CORP_CA}
```

The command-line flag wins over the compose file, which wins over the environment
variable. The path is validated before any container starts: a missing file, or one
OpenSSL cannot parse, fails immediately rather than surfacing as a TLS error an hour
into a run.

## What this covers

OSS-CRS mounts the CA read-only at `/etc/oss-crs/ca` in the internal LiteLLM proxy and
in every CRS run container, and sets `SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE`,
`CURL_CA_BUNDLE` and `NODE_EXTRA_CA_CERTS` to point at it. That covers Python's `ssl`
and `urllib`, `httpx`, `requests`, curl, LiteLLM itself, and the Node-based agent CRSs.
The host-side `/models` check for `litellm.mode=external` uses it too.

Two files are generated in that directory. `bundle.pem` is the public root store with
your CA appended, and is what `SSL_CERT_FILE` and friends point at — those variables
*replace* the trust store rather than extending it, so the public roots have to be
carried along or endpoints like `api.anthropic.com` would stop verifying. `extra.pem`
holds your CA alone and is what `NODE_EXTRA_CA_CERTS` points at, because Node appends
that file to its own built-in roots.

A CRS that manages its own trust store can override any of these four variables through
`additional_env` in the compose file.

## What this does not cover

`docker pull` and `docker build` use the **Docker daemon's** trust store, not container
environment variables, so this setting has no effect on them. On a machine where IT has
installed the CA in the system trust store, the daemon already trusts it and there is
nothing to do. Otherwise the CA has to be installed on the host running the Docker
daemon; `oss-crs prepare` and CRS image builds cannot be fixed from the compose file.

If your network intercepts TLS through an explicit proxy, set `HTTPS_PROXY`/`NO_PROXY`
per CRS via `additional_env`. `NO_PROXY` must include the compose-internal hostnames —
`litellm.oss-crs`, `builder-sidecar.<crs>`, `runner-sidecar.<crs>`,
`postgres.oss-crs-infra-only` — or intra-compose HTTP will break.

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

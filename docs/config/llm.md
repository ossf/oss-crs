# LLM Configuration Reference

OSS-CRS uses [LiteLLM](https://github.com/BerriAI/litellm) for LLM integration. The configuration format follows the [LiteLLM Proxy Configuration](https://docs.litellm.ai/docs/proxy/configs) specification.

This document describes the **LiteLLM config file format** used in `llm_config.litellm.mode=internal`.
For compose-level mode selection (`internal`, `external`, `null`), see [crs-compose.md](crs-compose.md).

For complete documentation, refer to:
- [LiteLLM Proxy Configuration](https://docs.litellm.ai/docs/proxy/configs)
- [LiteLLM Supported Providers](https://docs.litellm.ai/docs/providers)

> **Note:** Use `os.environ/VAR_NAME` format to reference environment variables (e.g., `os.environ/OPENAI_API_KEY`). OSS-CRS extracts these references and validates that the environment variables are set at runtime. You can satisfy them either by exporting variables in your shell or by putting them in a `.env` file in the directory where you run `oss-crs`; the CLI calls `load_dotenv()` automatically on startup.

---

## Example Configuration

```yaml
model_list:
  #########################################
  # OPENAI API
  #########################################
  - model_name: o4-mini
    litellm_params:
      model: o4-mini
      api_key: os.environ/OPENAI_API_KEY

  - model_name: gpt-4o
    litellm_params:
      model: gpt-4o
      api_key: os.environ/OPENAI_API_KEY

  - model_name: gpt-4.1
    litellm_params:
      model: gpt-4.1
      api_key: os.environ/OPENAI_API_KEY

  #########################################
  # ANTHROPIC API
  #########################################
  - model_name: claude-sonnet-4-20250514
    litellm_params:
      model: claude-sonnet-4-20250514
      api_key: os.environ/ANTHROPIC_API_KEY

  - model_name: claude-opus-4-20250514
    litellm_params:
      model: claude-opus-4-20250514
      api_key: os.environ/ANTHROPIC_API_KEY

  #########################################
  # GEMINI API
  #########################################
  - model_name: gemini-2.5-pro
    litellm_params:
      model: gemini/gemini-2.5-pro
      api_key: os.environ/GEMINI_API_KEY

  #########################################
  # CUSTOM ENDPOINT (e.g., Azure)
  #########################################
  - model_name: azure-gpt-4
    litellm_params:
      model: gpt-4.1
      api_key: os.environ/AZURE_API_KEY
      api_base: "https://my-azure-endpoint/openai/v1"

  #########################################
  # CUSTOM/SELF-HOSTED MODELS
  #########################################
  # vLLM or other OpenAI-compatible servers
  - model_name: my-local-llama
    litellm_params:
      model: openai/meta-llama/Llama-3.1-70B
      api_key: os.environ/VLLM_API_KEY
      api_base: "http://localhost:8000/v1"

  # Ollama
  - model_name: ollama-codellama
    litellm_params:
      model: ollama/codellama
      api_key: os.environ/OLLAMA_API_KEY
      api_base: "http://localhost:11434"
```

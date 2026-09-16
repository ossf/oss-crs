# MCP Servers

OSS-CRS can run [Model Context Protocol](https://modelcontextprotocol.io/)
servers and expose their tools to agents through the
internal LiteLLM proxy. Agents call tools via the `libCRS mcp` CLI, so no
agent harness needs its own MCP client. Requires internal LLM mode.

## Registry (`registry/mcp/*.yaml`)

| Field             | Required | Default | Description                                       |
| ----------------- | -------- | ------- | ------------------------------------------------- |
| `name`            | Yes      | —       | Alphanumeric plus `_`. Used as the container alias and gateway key. |
| `image`           | Yes      | —       | Path to dockerfile or docker image url.           |
| `url`             | Yes      | —       | `Address of MCP server (e.g. http://name:port/mcp)` |
| `requires_source` | No       | `false` | Mount target source read-only at `/OSS_CRS_TARGET_SOURCE`. |
| `command`         | No       | image default | Override default command of MCP server image |

## Compose usage

```yaml
mcp_servers:
  - ast_grep
```

Each entry renders a `mcp-<name>` service on the infra-only network.

## LiteLLM gateway

Each server is registered with `transport: http` and `allow_all_keys: true`.
Tools execute via the REST surface:

- `GET /mcp-rest/tools/list` — tools visible to the caller's key.
- `POST /mcp-rest/tools/call` — body `{"server_id": ..., "name": ...,
  "arguments": {...}}`; `server_id` accepts a UUID, name, or alias.

## Prompt hook

Registering any server wires a LiteLLM callback that appends a short
`libCRS mcp` usage note to every request's system prompt. The note is
skipped when already present, and an error leaves the request unchanged.

## `libCRS mcp` CLI

Used by the agent to access MCP servers. Full command reference with examples: [libCRS CLI Reference](../design/libCRS.md#mcp-commands).

## Adding a new MCP server

1. Create a docker image with the MCP server HTTP API exposed to LiteLLM (listening
on 0.0.0.0).
2. Create a corresponding registry entry in registry/mcp/.

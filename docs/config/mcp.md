# MCP Servers

OSS-CRS runs [Model Context Protocol](https://modelcontextprotocol.io/) servers
as sidecars and registers their tools with the internal LiteLLM gateway.
Agents discover and call tools with `libCRS mcp`, without implementing an MCP
client in their own harness.

## Compose configuration

Set `mcp_servers` under each CRS entry in the [compose configuration](crs-compose.md):

```yaml
run_env: local
docker_registry: local
oss_crs_infra:
  cpuset: "0-1"
  memory: 8G
llm_config:
  litellm:
    mode: internal
crs-bug-finding-claude-code:
  cpuset: "2-7"
  memory: 16G
  mcp_servers:
    - ast_grep
```

Names refer to server definitions in `registry/mcp/`. A non-empty list requires
`llm_config.litellm.mode: internal`; missing/null LLM configuration and external
mode fail validation.

Each CRS gets its own server instance. For example, the configuration above
creates service `mcp-crs-bug-finding-claude-code-ast_grep` and gateway/DNS alias
`crs_bug_finding_claude_code_ast_grep`. Hyphens in the CRS name become underscores
in the gateway alias. Colliding gateway names are rejected.

Each MCP server container shares its owning CRS's resource group (`cgroup_parent`
in `--cgroup-parent` mode, otherwise `cpuset` and `mem_limit`), receives
`OSS_CRS_NAME=<crs_name>`, `BUILDER_MODULE=builder-sidecar`, and
`RUNNER_MODULE=runner-sidecar` in its environment, and joins both
`<compose>-infra-only-network` (under its gateway alias) and `<compose>-network`
so servers that drive builds or replays can resolve `builder-sidecar.<crs>` and
`runner-sidecar.<crs>`.

## Registry (`registry/mcp/*.yaml`)

| Field | Required | Default | Description |
|---|---|---|---|
| `name` | Yes | — | Server name containing only ASCII letters, digits, and underscores. Hyphens are rejected. |
| `image` | Yes | — | Docker image reference or Dockerfile path. Relative paths are resolved against the registry YAML's directory. |
| `url` | Yes | — | HTTP or HTTPS MCP endpoint, including its port and path. Its hostname is replaced with the per-CRS alias. |
| `transport` | No | `http` | `http`, `sse`, or `streamable-http`. The latter is normalized to `http` for LiteLLM. |
| `requires_source` | No | `false` | Mount target source read-only at `/OSS_CRS_TARGET_SOURCE`. |
| `command` | No | Image default | List of command arguments overriding the image command. |
| `requires_build_artifacts` | No | `false` | Mount the owning CRS's base build output read-only at `/OSS_CRS_BUILD_OUT_DIR` and the shared rebuild output directory read-write at `/OSS_CRS_REBUILD_OUT_DIR`. |

For example, an SSE server that reads target source:

```yaml
name: ast_grep
image: oss-crs-mcp/ast-grep:latest
url: http://ast_grep:3101/sse
transport: sse
requires_source: true
```

`requires_source` uses the same fixed source path as CRS run modules.
Tool arguments referring to target files should use `/OSS_CRS_TARGET_SOURCE`.

For a tool that also reads or writes build artifacts (such as trace/verdict
files alongside replayed builds), set `requires_build_artifacts: true` to mount
`/OSS_CRS_BUILD_OUT_DIR` read-only and `/OSS_CRS_REBUILD_OUT_DIR` read-write.
This requires existing build output for that CRS and target; rendering fails if
the build output is missing or the run is source-only.

## Image preparation

During `oss-crs prepare`, the framework collects the distinct server names used
by all CRS entries and prepares their images once per server definition.

To build using a Dockerfile, set `image` to an absolute path or a relative path
(for example `./server/Dockerfile`). Paths beginning with `/`, `./`, `../`, or
`~/`, filenames starting with `Dockerfile`, and filenames ending in
`.dockerfile` are recognized as Dockerfile paths. Relative paths are resolved
against the registry YAML's directory. Missing files and directory paths fail
with an error instead of falling back to a registry pull.

The build context is always the Dockerfile's parent directory; `COPY` and
`ADD` paths must refer to files within that directory. Preparation builds with
`docker build -f <dockerfile> -t <generated-tag> <parent-directory>`.
The tag is `oss-crs-mcp/<lowercase-server-name>:<12-character-path-hash>`;
preparation and Compose rendering use the same tag. The hash identifies the
resolved path, not file contents. Every prepare rebuilds the image using
Docker's layer cache; rendering references it without triggering a build.
Without a local Dockerfile, the image is pulled from a registry.

## Gateway and prompt hook

The generated LiteLLM configuration registers each namespaced endpoint using
its validated transport and `allow_all_keys: false` (deny by default). During
key generation, each CRS's virtual key is granted only its own namespaced MCP
servers via `object_permission.mcp_servers` (or an explicit empty list when the
CRS declares no `mcp_servers`).

`libCRS` connects using the framework-injected `OSS_CRS_LLM_API_URL` and key
from `OSS_CRS_LLM_API_KEY_FILE`, falling back to `OSS_CRS_LLM_API_KEY`.
It uses these gateway REST endpoints:

- `GET /mcp-rest/tools/list` to discover tools.
- `POST /mcp-rest/tools/call` with `server_id`, `name`, and `arguments` to invoke a tool.

Registering servers also installs a proxy callback that appends a short
`libCRS mcp` usage note to request system prompts. Existing prompt content is
preserved, and injection is skipped when the prompt already mentions
`libCRS mcp`.

See the [MCP CLI reference](../design/libCRS.md#mcp-commands) for discovery,
invocation, output limits, and exit codes.

## Adding a server

1. Build or publish a Docker image whose MCP server listens on `0.0.0.0`.
2. Add a registry YAML defining its name, image, endpoint, transport, and mounts.
3. Add its name to the desired CRS entry's `mcp_servers` list and select internal LLM mode.
4. Run `oss-crs prepare`, then build the target if the server needs build output.
5. During a run, use `libCRS mcp list` and `libCRS mcp describe <tool>` to inspect
   the available tools before calling them.

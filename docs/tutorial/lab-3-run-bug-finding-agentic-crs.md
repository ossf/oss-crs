# Lab 3: Run a Bug-Finding Agentic CRS

**Goal:** See where traditional fuzzing stalls but reasoning agents succeed.

**CRS-bug-finding-Claude-Code** is an LLM-based bug-finding CRS. Given source code, it identifies a bug, generates a PoV, builds and verifies it through the builder and runner sidecars, and repeats the process for distinct bugs within the time budget.

**Target:** `asc-nginx-delta-01` - Nginx, a high-performance web server widely used as a reverse proxy, load balancer, HTTP cache, and mail proxy.

<details>
<summary>Nginx harness - smtp_harness.c</summary>

The fuzzer input is injected into a fake socket, and the input is pushed through nginx's mail state machine.

```c
extern "C"
int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size)
{
  // ...
  while (ngx_cycle->free_connection_n != 3) {
    ngx_process_events_and_timers((ngx_cycle_t *)ngx_cycle);

    if (process_counter == 25) {
      fprintf(stdout, "[ERROR] Bailing out of mail harness due to hitting counter maximum\n");
      fflush(stdout);

      // Get the set of existing connections
      c = ngx_cycle->connections;

      // Loop through the connections and release them
      for (int i = 0; i < cycle->connection_n; i++) {

        if (c[i].fd != -1 && c[i].fd != http_listen_fd) {
          ngx_close_connection(&c[i]);
        }
      }
    } else {
      process_counter += 1;
    }
  }
  // ...
```

</details>

## Run the Reasoning CRS

```bash
CRS=./example/crs-bug-finding-claude-code/compose.yaml
TARGET=benchmarks/asc-nginx-delta-01
HARNESS=smtp_harness

uv run oss-crs prepare --compose-file "$CRS"
uv run oss-crs build-target --compose-file "$CRS" --fuzz-proj-path "$TARGET"
uv run oss-crs run --compose-file "$CRS" --fuzz-proj-path "$TARGET" \
  --target-harness "$HARNESS" --timeout 1200
```

You can also point `crs-libfuzzer` at the same target to see how a plain fuzzer stalls on it.

## The Bug

In `ngx_mail_smtp_handler.c:ngx_mail_smtp_noop()`, the SMTP NOOP handler should do nothing except reply `250 OK`. However, its bogus "more than 10 args" guard both destroys `c->pool`, freeing the session `s` and `s->buffer`, and returns `NGX_ERROR` to the dispatcher, which later reuses the freed frame. The result is a **heap-use-after-free**. The PoV is a 27-byte SMTP line with eleven space-separated arguments: `NOOP f f f f f f f f f f f \n`.

```c
static ngx_int_t
ngx_mail_smtp_noop(ngx_mail_session_t *s, ngx_connection_t *c) {
  if (s->args.nelts > 10) {
    ngx_str_set(&s->out, smtp_invalid_argument);
    ngx_mail_close_connection(c); // destroys s and s->buffer
    return NGX_ERROR;
  }
  ngx_str_set(&s->out, smtp_noop);
  return NGX_OK;
}
```

```c
rc = ngx_mail_smtp_noop(s, c); // call site of the destroyer
if (s->buffer->pos < s->buffer->last) // use-after-free
    s->blocked = 1;
```

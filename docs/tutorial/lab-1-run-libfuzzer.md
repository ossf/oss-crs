# Lab 1: Run libFuzzer

**Goal:** Run a fuzzer-based CRS and watch it produce a crash.

**CRS-libFuzzer** is built entirely on libFuzzer, a widely used fuzzing engine. It does not use any LLM agents.

**What is fuzzing?** Fuzzing is an automated testing technique that finds bugs by repeatedly feeding a program mutated inputs and watching for abnormal behavior.

Key terms:

- **Harness** - a small adapter that turns the fuzzer's raw input bytes into real calls to the target's API.
- **Coverage** - a measure of which parts of the program an input executed. Fuzzers use coverage as feedback to identify inputs that reach new behavior. Interesting inputs become *seeds*.
- **Sanitizer** - compile-time instrumentation that adds runtime checks and aborts the moment a bug happens, acting as the crash oracle, such as **ASan** for memory safety and **UBSan** for undefined behavior. Crashing inputs become a **PoV**.

**Target:** `atlanta-mongoose-delta-01` - Mongoose, a small networking library for firmware and IoT devices. The harness (`fuzz`) builds a fake packet from fuzzer input and injects it into Mongoose's TCP/IP stack.

<details>
<summary>Harness of Mongoose - fuzz.c</summary>

```c
int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
  // ...
  if (size > 0) {
    struct mip_cfg cfg = {};
    size_t pktlen = 1540;
    char t[sizeof(struct mip_if) + pktlen * 2 + 0 /* qlen */];
    struct mip_if *ifp = (struct mip_if *) t;
    struct mg_mgr mgr;
    mg_mgr_init(&mgr);
    if_init(ifp, &mgr, &cfg, &mip_driver_mock, NULL, pktlen, 0);

    // Make a copy of the random data, in order to modify it
    uint8_t pkt[size];
    struct eth *eth = (struct eth *) pkt;
    memcpy(pkt, data, size);
    if (size > sizeof(*eth)) {
      static size_t i;
      uint16_t eth_types[] = {0x800, 0x800, 0x806, 0x86dd};
      memcpy(eth->dst, ifp->mac, 6);  // Set valid destination MAC
      eth->type = mg_htons(eth_types[i++]);
      if (i >= sizeof(eth_types) / sizeof(eth_types[0])) i = 0;
    }

    mip_rx(ifp, (void *) pkt, size);
    mgr.priv = NULL;  // Don't let Mongoose free() ifp
    mg_mgr_free(&mgr);
  }

  return 0;
}
```

</details>

## Run CRS-libFuzzer

```bash
CRS=./example/crs-libfuzzer/compose.yaml
TARGET=benchmarks/atlanta-mongoose-delta-01 # or the path you unzipped the benchmark
HARNESS=fuzz

uv run oss-crs prepare --compose-file "$CRS"
uv run oss-crs build-target --compose-file "$CRS" --fuzz-proj-path "$TARGET"
uv run oss-crs run --compose-file "$CRS" --fuzz-proj-path "$TARGET" \
  --target-harness "$HARNESS" --timeout 300
```

When it finishes, you will see the PoVs and seeds it produced, similar to the path below. **Save the path to a PoV**. You will need it for patching in Lab 2.

```text
.../oss-crs/.oss-crs-workdir/crs_compose/43e5a06b0d32/address/runs/1781271789u6/crs/crs-libfuzzer/atlanta-mongoose-delta-01_6474d46de441/SUBMIT_DIR/fuzz/povs
```

## The Bug It Finds

In `mip/mip.c:rx_icmp()`, the ICMP echo responder copies the payload after clamping it to the buffer length (`left`), but computes the checksum over the raw `pay.len`. When `pay.len` exceeds the buffer, the checksum reads past the end of the TX stack buffer, causing a **dynamic-stack-buffer-overflow**. The PoV is a roughly 131 KB Ethernet + IPv4 + ICMP-echo frame, with 42 bytes of framing plus padding.

```c
static void rx_icmp(struct mip_if *ifp, struct pkt *pkt) {
  // ...
  struct ip *ip = tx_ip(ifp, 1, ifp->ip, pkt->ip->src,
                        sizeof(struct icmp) + pkt->pay.len);
  struct icmp *icmp = (struct icmp *) (ip + 1);
  size_t len = PDIFF(ifp->tx.buf, icmp + 1),
         left = ifp->tx.len - len;
  if (left > pkt->pay.len) left = pkt->pay.len;
  memset(icmp, 0, sizeof(*icmp));
  memcpy(icmp + 1, pkt->pay.buf, left);
  icmp->csum = ipcsum(icmp, sizeof(*icmp) + pkt->pay.len); // The vulnerable site
```

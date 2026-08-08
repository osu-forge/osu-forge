# Architecture

## Shape

```
engine/      Rust    osu!stable memory engine — separate process, read-only
diffcalc/    Rust    difficulty + pp, independent implementation, PyO3 bindings
core/        Python  platform: event bus, extension host, local server
extensions/          first-party analysis extensions
overlays/            first-party overlays (HTML/JS)
hwtest/      Python  Windows Raw Input hardware diagnostics — separate process
web/                 dashboard + HTML report templates
```

### Why the engine and hwtest are separate processes

Not for tidiness. Both are the components most likely to (a) break when osu! updates,
(b) get flagged by antivirus, and (c) crash on malformed data. Isolating them means
none of those takes down the rest of the tool. A stale signature after a game update
should degrade the live features and leave offline replay analysis working.

`hwtest` additionally *cannot import* `core` or `extensions` — CI enforces this. That
is how "the input logger never correlates keystrokes to game state" becomes a
structural property rather than a promise.

### Why Rust for the engine and Python for the platform

The platform's genuinely hard part is statistics: bootstrap confidence intervals,
cluster-robust standard errors, hinge regression. Everything else on the list — a
WebSocket client, an HTTP server, binary parsing — is a commodity in any language.
Pick the language that wins the hard part. `numpy`, `scipy`, and `statsmodels` win it
decisively.

Implementing cluster-robust covariance or BCa bootstrap by hand produces the specific
failure mode this project exists to avoid: numbers that look plausible, read as
authoritative, and are quietly wrong for months.

Rust is used where its properties matter — a single static binary that can be
code-signed, and memory safety across a large surface of pointer arithmetic over
data that may be corrupt (which is exactly what a stale offset produces).

---

## Extension model — three tiers

Deliberately three different mechanisms, because the three kinds of extension have
different requirements.

| Tier | For | Mechanism | Isolation |
|---|---|---|---|
| **1** | overlays, UI | web page (HTML/JS + `manifest.json`) | browser sandbox |
| **2** | analysis, compute | separate process, **JSON-RPC 2.0 over stdio** | process |
| **0** | first-party built-ins | `importlib`, in-process | none — **trusted tier, never third-party code** |

**Tier 1** costs nothing to sandbox and works as an OBS browser source for free.

**Tier 2** uses stdio with LSP-style `Content-Length` framing. That makes extensions
language-agnostic — Python, Node, or a compiled binary — with zero Windows-specific
code. The known objection is that a stray write to stdout corrupts the stream; the
answer is a contract (**extensions log to stderr, never stdout**) plus framing that
lets the host *detect* desynchronization and kill the extension rather than silently
misparse. LSP has proven this across dozens of languages.

**Tier 0** is fast and unisolated. It is for our own code only. If you are tempted to
load third-party code here, that is what Tier 2 is for.

WASM (Extism, wasmtime) was considered and rejected for v1. The isolation is better,
but Tier 2 extensions want numpy and scipy, and getting that stack into WASM is not a
solved problem. The whole point of Tier 2 is heavy statistics; forcing extension
authors into a numerics desert defeats it.

### Crash and hang policy

- Every host→extension call has a deadline. On timeout the call rejects and the
  extension is marked degraded.
- Heartbeat ping; three missed beats terminates the process.
- Restart with exponential backoff; circuit-break after N crashes in a window and
  surface it in the UI.
- Events are fire-and-forget. **No extension call is ever on the critical path of the
  state loop.**

### Capability model

Extensions declare capabilities in their manifest. The host mediates every one:

```
state:read            subscribe to live state paths
files:beatmaps:read   host resolves and parses; the extension never sees a path
files:replays:read
files:config:read
net:osu-api           host holds the OAuth token, proxies and rate-limits
net:*                 requires explicit user grant, shown prominently
ui:overlay            may register a Tier-1 page
storage               host-owned scoped key-value
```

**The rule that makes this work: Tier-2 extensions receive neither filesystem paths
nor network credentials.** An extension asks for `beatmap(md5)` and gets a parsed
object back.

This is simultaneously the security boundary and the deduplication mechanism. Because
extensions cannot open files, N extensions cannot each parse the same beatmap N
times. The host owns:

- a content-addressed cache (md5 → parsed object, LRU + refcounted)
- **single-flight**: concurrent requests for the same uncached key coalesce into one
  parse, and every caller awaits the same future
- two delivery paths — Tier 0 gets the object by reference (zero copy); Tier 2 gets a
  serialized projection containing **only the fields its manifest declared**, so a
  config linter never pays to serialize hit objects
- prefetch on `beatmap:changed`, so the cache is warm before anyone asks

---

## Event and state model — three layers

**Layer 1 — versioned immutable snapshot.** One object, monotonic revision counter.
An extension can always ask "what is true now" without replaying history.

**Layer 2 — path-filtered subscriptions.** Extensions declare interest as dotted
paths (`beatmap.metadata.title`, `play.hits`). The obvious benefit is bandwidth. The
real benefit is that **the host knows what nobody is watching** and can skip computing
it — expensive derived fields are lazy and demand-driven.

**Layer 3 — semantic events from snapshot diffing.** The host diffs consecutive
snapshots and emits `play:started`, `play:ended`, `beatmap:changed`, `play:retried`,
`session:started`. Extensions should not have to write state machines. Filesystem
events (a new `.osr`, `osu!.db` mtime change) and job-completion events from
historical analysis ride the same bus with the same envelope shape and per-extension
ordering.

---

## Local server security

A local server is reachable by any local process **and by any web page the user
visits** — the request originates from the user's own machine, so IP-based filtering
does nothing about it. This is a well-documented class of bug and the reason for the
layering below.

1. **Bind explicitly to `127.0.0.1`** — never `0.0.0.0`, never `::`.
2. **Validate the `Host` header** on every request; 403 anything that is not
   `127.0.0.1:PORT` or `localhost:PORT`. A DNS-rebinding attack necessarily carries
   the attacker's hostname, which makes this the most reliable single defense.
3. **Validate `Origin`** against an allowlist on WebSocket upgrade. Never `*`. When
   overlays are served from our own server their Origin *is* our origin, so this
   costs nothing.
4. **Per-session bearer token**, generated at startup, written with restrictive ACLs
   to `%LOCALAPPDATA%\osu-forge\runtime.json`, required on every request. This is the
   layer that holds if 2 and 3 are bypassed.
5. **Separate read and write scopes.** Anything that mutates the filesystem requires
   the token *and* in-app confirmation. Never a bare GET.
6. **Avoid ports 24050 and 20727** (used by existing osu! tools). Fixed configurable
   default; the real port and token go in the runtime file so extensions never guess.
   On bind conflict, **fail loudly** — do not silently drift to another port, because
   the user has an OBS source pointing at a specific URL.

---

## Data sources

| Source | Provides | Needs the engine? |
|---|---|---|
| `Songs\` + `.osr` files | replays, beatmaps, offline analysis | no |
| `osu!.<user>.cfg` | settings, keybinds | no |
| `osu!.db` | per-beatmap local/online offset | no |
| Windows registry / Win32 probes | mouse settings, refresh rate, audio latency | no |
| Raw Input (`hwtest`) | hardware diagnostics | no |
| **memory engine** | live gameplay state | yes |

Everything except live state works with the engine absent. That ordering is
deliberate: if the engine breaks after an osu! update, offset analysis, the config
doctor, pp calculation, and hardware diagnostics all keep working.

---

## The `Finding` contract

`Finding` is the only type that crosses subsystem boundaries. `web/` and the CLI
import `core/src/osuforge/models.py` and nothing else from any subsystem; an
import-linter test enforces it.

Two fields deserve explanation:

- **`basis`** — `hard_fact` | `community_consensus` | `preference`. Without this, a
  linter sells taste as truth. "Enhance Pointer Precision is off" is a fact.
  "Disable Game Mode" is disputed. They must not render identically, and
  `--only-facts` must be able to filter.
- **`confidence`** — includes `insufficient`, which carries **no recommendation at
  all**. When the sample is too small, the honest output is "not enough data, play
  ~15 more maps across 2 more sessions," not a hedged number.

See `core/src/osuforge/models.py` for the definition.

# recall

A searchable store of questions already answered, with the raw evidence behind each answer.
Shared across CLIs, works from the terminal, costs no tokens.

**If you have forgotten everything else, this is the command:**

```bash
recall search "caching gain"
```

---

## Why it exists

On 2026-08-13 the question *"how much did yesterday's caching gain?"* was answered by re-running
the benchmark instead of reading the recorded numbers. The re-run was badly constructed — a cold
DB connect on one side, a warm pool on the other — and gave "0.5x", then a corrected "146x".
Neither matched the figure recorded the day before: **1.27s → 0.01s**.

The data was on disk the whole time. Nothing made it findable, so re-deriving looked cheaper than
retrieving — and re-deriving got it wrong twice.

Measured cost of that one incident: **~9,400 tokens** of re-derivation versus **~217 tokens** for
the equivalent lookup. About 43x, and the lookup is the one that's correct.

## Install

```bash
cd ~/recall-mcp
./install.sh
```

Copies four files to `~/.shared_memory`, links `recall` into `~/.local/bin`, and registers the MCP
server with Claude Code, Codex and Gemini if they are installed. Every config edit is backed up
first, and re-running is safe — **the store is never overwritten**.

Restart any running CLI afterwards to load the MCP server.

## Terminal usage

### Browse everything

```bash
recall list                    # one line per entry, whole store on a screen
recall list -f                 # full entries with evidence
recall list --explicit-only    # hand-written only, skip auto-extracted
recall list --project recall   # one project
recall projects                # entry counts per project
recall prune --dry-run         # auto-entries that fail the CURRENT rules
recall prune                   # remove them (never touches hand-written)
```

Colour is on for a TTY and off when piped, so `recall list | grep` stays clean.
`NO_COLOR=1` disables it; `RECALL_COLOR=always` forces it for `| less -R`.

### Search

```bash
recall search "caching gain"           # the common case
recall search "pool size" -v           # full answer, all evidence, tags
recall search "pool size" -n 10        # more results (default 5)
recall search "pool size" --all        # include superseded (corrected) entries
recall search "pool size" --explicit-only   # curated entries only, skip auto-extracted
```

Real output:

```
1 result(s) for 'caching gain'

#5  2026-08-13
  Q: how much did the Auth Dashboard caching and bulk endpoint optimisation gain?
  A: Three wins, all measured 2026-08-12. (1) Cache on /api/policy_status:
     0.703s -> 0.0014s, ~500x. (2) Bulk endpoint replaced one request per device:
     41 requests in 1.27s -> 1 request in 0.01s, and now flat in fleet size...
     | connect: 2217.6 ms / 2 count queries: 17.7 ms (46 rows)
     | policy_status call 1: 0.703146s (cold) -> call 2: 0.001370s (cached)
     refs: 6b6b61057, 263c9d33e, 4080a308b
```

### Record

```bash
recall add \
  -q "what does the dash mean in the Authorization column?" \
  -a "No device_policies row stored, so no verdict recorded. Unevaluated is not denied." \
  -e "6 of 46 devices have no device_policies row" \
  -e "example: 000000111111 - Identification=yes, rest no" \
  --ref 6be28a894 \
  --tags "dash authorization dashboard unevaluated" \
  --project iot_zerotrust_prod
```

`-e` and `--ref` repeat. **Put raw numbers in `-e` verbatim** — that is what makes an entry
checkable rather than merely memorable. Near-duplicate questions are detected and refused unless
you pass `--force`.

### Correct a wrong answer

```bash
recall add -q "..." -a "the corrected answer" --force    # -> #28
recall supersede 27 --by 28
```

`#27` then drops out of normal search but stays reachable with `--all`. This matters: the 146x
figure above was wrong, and a store that cannot record corrections serves bad numbers forever.

### Everything else

```bash
recall show 5      # one entry in full
recall stats       # how many entries, explicit vs auto-extracted
recall --help
```

## Using it from a CLI

No slash command. The MCP tool description tells the model to check before re-deriving, so it
happens on its own. To nudge it: *"check recall first"*, *"have we measured this before?"*.

| CLI | Config | Key |
|---|---|---|
| Claude Code | `~/.claude.json` | `mcpServers.recall` |
| Codex | `~/.codex/config.toml` | `[mcp_servers.recall]` |
| Gemini | `~/.gemini/settings.json` | `mcpServers.recall` |

All three point at the same two lines:

```
command: python3
args:    ["~/.shared_memory/recall_mcp.py"]
```

They share **one SQLite file**, so an entry written by Codex is immediately visible to Claude.
No embedding model, no API key, no network — model-agnostic by construction.

## How entries get in

**Automatically**, from the Claude Code `Stop` hook, after every session. Runs outside the model,
so it costs nothing. Deterministic rules, no LLM:

1. Real user turns have *string* content; tool results are lists. One test removes most noise.
2. Harness traffic is dropped by prefix (`<local-command`, `<system-reminder`, …).
3. The question must name a subject — a file, a metric, a number, a known keyword. "anything
   else?" is dropped: nothing to search on later.
4. The answer must carry **hard evidence** — a number with a unit, a commit sha, a `file.py:123`,
   or a before/after arrow. This is the rule that keeps chatter out.

Measured: 2,457 raw `user` lines → 142 real questions → **4 stored entries** for one long session.
Backfill over 57 transcripts (147 MB) produced 19 entries.

Auto-extracted entries are marked `[auto-extracted]` and rank below hand-written ones. Compare
`recall show 5` (written by hand) with `recall show 2` (extracted) on the same topic.

**Explicitly**, whenever it matters — a good hand-written entry beats anything extraction produces.

## Search behaviour

Keyword search over SQLite FTS5, not semantic.

- Query terms are **OR-ed**, then re-ranked by how many terms the entry actually contains.
- A **multi-word query needs at least 2 matching terms**. Below that you get nothing, deliberately:
  `"recall search"` used to return a pasted UI dump purely because it contained the word "Search".
  An honest empty result beats a confident wrong one.
- Stop words are stripped first, so `"how much did the caching gain"` reduces to
  `caching`, `gain` — still 2 terms, still a hit.
- A single-word query needs 1 match.

Measured at **0.74 ms per search**.

## Why not a vector database

The corpus is ~1k entries / ~1 MB, not millions. FTS5 is in the Python stdlib, needs no embedding
model on every client, costs no tokens per query, and works offline.

Embeddings would buy fuzzy matching when query and entry share no words; explicit `tags` cover
most of that. If real use shows genuine recall misses, add embeddings as an optional second
ranker — the schema does not change. Decide that on measured misses, not on assumption.

## Tests

```bash
python3 test_recall.py     # 47 checks, stdlib only, uses a temp DB
```

Every case exists because something went wrong. Search quality is a balance between recall
(finding what exists) and precision (not inventing relevance), and each past tweak to one
silently damaged the other — relaxing the term floor to fix `"where do I configure
antigravity"` immediately let irrelevant entries back in. The suite pins both ends.

## Known limits

- **Keyword, not semantic.** A query sharing no words with an entry misses. Tags mitigate it.
- **Extraction is lossy both ways.** It misses things and keeps some noise, biased toward
  precision.
- **Single machine.** The DB is one file; syncing it across machines is a separate decision.

## Files

| Path | Role |
|---|---|
| `src/recall_store.py` | the one implementation — CLI, MCP and extractor all call it |
| `src/recall` | CLI |
| `src/recall_mcp.py` | MCP server (stdio) |
| `src/recall_extract.py` | deterministic transcript extractor |
| `install.sh` | copy, link, register, verify |
| `docs/SETUP.md` | manual setup and the auto-capture hook |

Runtime lives in `~/.shared_memory/`, with the store at `~/.shared_memory/recall.db`.

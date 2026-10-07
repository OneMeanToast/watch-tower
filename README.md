# Watch Tower

Watch Tower watches news, papers, and social posts for things you care about. You describe each thing as an **indicator** (a YAML file). Incoming items are embedded, cheaply prefiltered, then scored by a pluggable classifier. A strong enough score sends an alert. A weaker score waits for the digest. You can mark each alert right or wrong, and those labels are kept for later threshold tuning or a Kev fine-tune.

It is meant to run on an Apple Silicon Mac, with free and open-source pieces only. No paid model APIs. X/Twitter is not supported.

## Pipeline

```
feeds → normalize / dedupe → embed → cosine + keyword prefilter
      → decider (Kev-style System One, or Ollama, or an offline lexical fallback)
      → threshold / cooldown → ntfy or Apprise → thumbs up/down
```

Feed text is untrusted. It is fenced and scrubbed of instruction-shaped phrases before it reaches a model, and a model is only allowed to produce a score. Alerts are notifications. Nothing in an article is executed.

## Demo mode

Demo mode needs no network and no model download. It replays eight fixture items through the real pipeline: hashing embeddings, SQLite, sqlite-vec, the lexical decider, and the example indicators.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
wt demo
```

You should see at least one `[push]` block on stdout, including the LLM-JEPA weight release. Exit code is 0 when a push fired. The demo database is `data/demo.db` and is replaced every run.

`wt demo` always starts on the hashing embedder and the lexical decider, even if `WT_EMBEDDER` or `WT_DECIDER` are set. Pass `--decider systemone` or `--embedder qwen` only when you want the demo fixtures scored by a local server or the real embedding model.

## Mac setup (live mode)

Any Apple Silicon Mac is enough for the offline path and for [Kev-0.8B](https://huggingface.co/jaredpalmer/kev-0.8b). Kev-4B wants about 32 GB. The embedding model `Qwen/Qwen3-Embedding-0.6B` is about 1.2 GB.

```bash
brew install python@3.12
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
# optional: real embeddings, full-text extraction, Bluesky websocket client
pip install -e ".[embed,extract,bluesky]"
```

Copy `config/watchtower.example.env` and export what you need. Then:

```bash
wt doctor          # indicators, sqlite-vec, whether Kev or Ollama is up
wt run             # one ingest cycle
wt serve --interval 15
```

Alerts print locally. To push them to a phone, pick an unguessable [ntfy](https://ntfy.sh) topic (the server is Apache-2.0; you can also self-host it):

```bash
export WT_NTFY_URL='ntfy://ntfy.sh/replace-with-a-long-random-topic'
```

Other services go through [Apprise](https://github.com/caronc/apprise) (BSD-2-Clause), either in that env var as a comma-separated list or per indicator under `alert.channels`.

### Classifier

The decider is selected with `WT_DECIDER`:

| Value | What it calls |
| --- | --- |
| `auto` (live default) | A System One server if `GET /v1/models` answers, else Ollama, else lexical |
| `systemone` | `POST /v1/systemone` on `WT_DECIDER_URL` (default `http://127.0.0.1:8009`) |
| `ollama` | A local Ollama chat model forced to return JSON (`WT_OLLAMA_MODEL`) |
| `lexical` | Offline keyword / example overlap. This is what demo mode uses |

`WT_DECIDER_FALLBACK=1` (the default) scores an item lexically when the chosen server errors, so a downed Kev process does not stall the loop.

**Kev** is the primary open System One model (Apache-2.0). It is not vendored here; the weights are large and the server is a separate project. On a Mac:

```bash
git clone https://github.com/jaredpalmer/kev.git
cd kev
uv sync --extra serve
uv run --extra serve python -m kev.serve --run jaredpalmer/kev-0.8b --port 8009
```

Kev-4B (`jaredpalmer/kev-4b`) is the better model when you have the memory. Pin a release with `@v1.0` if you want the same weights next month. Von (`von serve`) and TinyJev (`tinyjev serve`) speak the same HTTP shape; point `WT_DECIDER_URL` at them and set `WT_DECIDER_MODEL` if their server expects a model name.

**Ollama** is the other local fallback:

```bash
ollama pull qwen2.5:3b
export WT_DECIDER=ollama
```

The lexical backend is a fixed heuristic, not a calibrated probability. The `0.85` line in the example indicators was checked on the fixture set for that heuristic. After you switch to Kev, re-fit the thresholds on your own labels (`wt eval` is only the fixture set).

### Embeddings

`WT_EMBEDDER=auto` loads `Qwen/Qwen3-Embedding-0.6B` when `sentence-transformers` is installed, and otherwise uses a hashing embedder. Vectors are float32 blobs in SQLite and compared with sqlite-vec's `vec_distance_cosine`. Delete `data/watchtower.db` if you change embedder; the dimension is fixed for the life of the file.

Hashing cosine is not comparable to Qwen cosine. If you move to Qwen, expect to retune `prefilter.min_cosine`.

Full article text is optional. RSS and Atom entries use their summary unless you later turn on trafilatura yourself; the MVP does not crawl arbitrary pages by default.

## Write an indicator

Put one YAML file per indicator in `config/indicators/`. This is the shape (also see the five examples already there):

```yaml
id: ai-open-text-jepa
name: Open-weight text JEPA released
description: >
  A lab releases open weights for a JEPA-style text model, or ships the code.
sources: [arxiv, rss:ai-news, bluesky, gdelt]   # rss:<feed id> or a bare source name
prefilter:
  min_cosine: 0.32
  keywords_any: ["JEPA", "LLM-JEPA", "joint embedding predictive"]
  keywords_all: []          # every term must appear
  keywords_none: []         # any term blocks the item
  entities_any: []          # literal phrases (a GLiNER tagger is not bundled)
positives:                  # few-shot anchors, also embedded
  - "Meta releases open weights for an LLM-JEPA text embedding model under Apache-2.0"
negatives:
  - "A blog post explaining what JEPA is"
questions:                  # sent to the System One decider
  hit:
    type: noul
    instructions: "Does this item report a NEW open-weight JEPA-style text model or code release?"
  severity:
    type: score
    instructions: "How significant is this?"
    criteria: ["trivial", "minor", "notable", "major", "field-changing"]
  kind:
    type: choice
    instructions: "What kind of item is this?"
    criteria:
      release: "weights or code released"
      paper: "paper only"
      commentary: "explainer"
policy:
  alert_if: "hit >= 0.85 and severity >= 2"
  digest_if: "hit >= 0.5"
  cooldown: 6h              # 30s, 15m, 6h, 1d
alert:
  channels: []              # Apprise URLs, or leave empty and set WT_NTFY_URL
```

`alert_if` / `digest_if` may use `hit`, `severity`, `cosine`, and `confidence`, comparisons, `and` / `or` / `not`, and parentheses. They are not Python.

An item passes the prefilter when its source matches and it is not blocked by `keywords_none` / `keywords_all`, and either cosine to the indicator (or a positive example) is at least `min_cosine` or a keyword, entity phrase, or regex hits. Only the top few candidates are sent to the decider.

`sources` entries are either a source name (`arxiv`, `gdelt`, `bluesky`, `openalex`, `mastodon`, `rss`) or `rss:<id>` for one feed from `config/sources.yaml`.

## Live sources

`config/sources.yaml` lists RSS/Atom feeds, arXiv categories, GDELT queries, an OpenAlex search, and optional Bluesky and Mastodon settings.

- **RSS / Atom** via feedparser.
- **arXiv** via the public Atom API (`cs.AI`, `cs.CL`, `cs.LG` by default).
- **GDELT DOC 2.0**, one request every five seconds. The article list returns titles and URLs, not full text, so the item body is the headline. The adapter does not scrape the linked page.
- **OpenAlex**. Set `WT_OPENALEX_KEY` (free, from [openalex.org/settings/api](https://openalex.org/settings/api)) for the daily free budget. A few keyless calls still work.
- **Bluesky Jetstream**. Set `bluesky.enabled: true` and install `watch-tower[bluesky]`. Each cycle reads a short window, not a gapless archive. Default endpoint: `wss://jetstream2.us-east.bsky.network/subscribe`.
- **Mastodon** public hashtag timelines, when `hashtags` is non-empty.
- **X/Twitter** is a disabled stub. The official API is paid, and this project does not scrape it.

`wt serve --interval 15` repeats `wt run`. A cron line works too: `*/15 * * * * cd /path/to/watch-tower && .venv/bin/wt run`.

Digests are printed during a cycle and stored. `wt digest` prints the ones since the last cursor (use that from a morning cron if you want a single summary).

## Feedback

Every push includes:

```text
wt feedback <match-id> up
wt feedback <match-id> down
```

That writes SQLite and appends `data/feedback.jsonl`. For clickable links, run `wt feedback-server` (default `http://127.0.0.1:8787`) and open the URLs in the alert.

```bash
wt export-kev --out data/kev-train.jsonl
```

writes Kev fine-tune rows: the item text as `state`, and the indicator's `hit` question with `label: true/false`. Score and choice labels are not invented; add those yourself if you train them. Kev's own trainer (`python -m kev.train --init_from jaredpalmer/kev-0.8b`) consumes that JSONL. Watch Tower does not download or fine-tune the weights.

`python eval/run_eval.py` (or `wt eval`) scores the lexical decider on the checked-in fixture labels and prints precision, recall, and Brier score.

## Layout

```text
config/sources.yaml          feeds, arXiv, GDELT, OpenAlex, Bluesky
config/indicators/*.yaml     one indicator per file
wt/                          ingest, embed, store, prefilter, decider, policy, alert
wt/experimental/jepa_head.py optional linear predictor in embedding space
fixtures/                    offline demo items and parser samples
eval/                        seed labels, eval, Kev export
```

## Tests

```bash
pytest
wt demo
wt eval
```

## What differs from the research note

Checked on 7 Oct 2026 while building this:

- **Kev, Von, TinyJev, and open-alternative-jev are real projects**, and Kev's published `POST /v1/systemone` body matches the client here (verified against the Kev README, and with a mock of that JSON in the tests). The weights were not downloaded in this environment, so the backend that actually ran end to end is the **lexical** fallback. Ollama is implemented and covered with a mock response. `wt doctor` tells you if a live server is up.
- **Qwen3-Embedding-0.6B** is the live default when sentence-transformers is installed. Demo mode and machines without the package use a hashing embedder so the pipeline runs with no download. Do not reuse hashing thresholds as if they were Qwen scores.
- **sqlite-vec** is used through `vec_distance_cosine` on float32 blob columns (the documented non-virtual-table path). `vec0` virtual tables want integer row ids and a frozen dimension, which fights text ids and switching embedders.
- **Entities** in the YAML are literal phrases. GLiNER is not bundled.
- **Feedback HTTP** is the standard library, not FastAPI.
- **Scheduler** is a sleep loop (`wt serve`) or cron, not APScheduler.
- **Bluesky** is a bounded sample per cycle on `jetstream2.us-east.bsky.network`, which is the current public subscribe host. The older path in the research note can be pasted into `endpoint` if you prefer it.
- **JEPA head** (`wt/experimental/jepa_head.py`) is a small ridge map. It is not on the alert path. There is still no production open text-JEPA embedder; the pipeline does not pretend otherwise.
- AGPL projects mentioned in the research (RSSHub, World Monitor, and others) are not copied. Run them beside this repo if you want them.
- Lexical probabilities are **not calibrated**. The fixture eval is a regression check for the heuristic, not a claim about Kev's precision.

## License

Apache-2.0. See `LICENSE`.

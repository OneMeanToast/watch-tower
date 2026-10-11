# Watch Tower

Watch Tower watches news, papers, and social posts for things you care about. You describe each thing as an **indicator** (a YAML file). Incoming items are embedded, cheaply prefiltered, then scored by a pluggable classifier. A strong enough score sends an alert. A weaker score waits for the digest. You can mark each alert right or wrong, and those labels are kept for later threshold tuning or a Kev fine-tune.

It is meant to run on an Apple Silicon Mac, with free and open-source pieces only. No paid model APIs. X/Twitter is not supported.

## Pipeline

```
feeds → strip hidden text → drop Bluesky spam clusters → dedupe
      → group the same event → embed → cosine + keyword prefilter
      → optional full text for likely hits
      → injection guard → decider (Kev 1.0, or Ollama, or lexical)
      → confidence cascade / cooldown → ntfy or Apprise → thumbs up/down
```

Feed text is untrusted. Hidden and invisible text is removed first. What remains is trimmed to a title plus about 300 words, scrubbed of instruction-shaped phrases, and wrapped in a spotlight fence (`untrusted=true`, body lines quoted with `>`). A model is only allowed to produce a score. Alerts are notifications. Nothing in an article is executed. A flagged item is not sent to Kev and cannot push.

## Demo mode

Demo mode needs no network and no model download. It replays the fixture items through the real pipeline: hashing embeddings, SQLite, sqlite-vec, the lexical decider, and the example indicators. Full-text fetch and escalation stay off.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
wt demo
```

You should see at least one `[push]` block on stdout, including the LLM-JEPA weight release. Exit code is 0 when a push fired. The demo database is `data/demo.db` and is replaced every run.

`wt demo` always starts on the hashing embedder and the lexical decider, even if `WT_EMBEDDER` or `WT_DECIDER` are set. Pass `--decider systemone` or `--embedder qwen` only when you want the demo fixtures scored by a local server or the real embedding model.

## Mac setup (live mode)

Any Apple Silicon Mac is enough for the offline path and for [Kev-0.8B](https://huggingface.co/jaredpalmer/kev-0.8b). Kev-4B wants a 32 GB Mac. The embedding model `Qwen/Qwen3-Embedding-0.6B` is separate from Kev and is about 1.2 GB.

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

**Kev 1.0** is the decision model (Apache-2.0, [jaredpalmer/kev](https://github.com/jaredpalmer/kev)). It is not vendored here. The current release has four sizes on Qwen3.5 / Qwen3.8 bases, pinned with `@v1.0`:

| Checkpoint | Where it fits | New-source automation at a 5% error budget |
| --- | --- | --- |
| `jaredpalmer/kev-0.8b@v1.0` | Any Apple Silicon Mac | about 14% |
| `jaredpalmer/kev-4b@v1.0` | A 32 GB Mac | about 52–69% |
| `jaredpalmer/kev-9b@v1.0` | More memory than the 4B | about 52–69% |
| `jaredpalmer/kev-27b@v1.0` | More memory than the 4B | about 52–69% |

The 0.8B checkpoint is much weaker than 4B and up. Earlier Qwen3 generations (0.6B / 4B / 8B, and a 0.5B on Qwen2.5) are older Hub revisions, not this release.

Kev's own server already runs on MLX on Apple Silicon. Watch Tower talks to it over the existing System One HTTP path. The request's `model` field stays `kev-latest`; the server serves whatever `--run` loaded. Set `KEV_DATE_FACTS=1` on that process so date questions get a small facts block. Validated context is 8,192 tokens, and the small checkpoints were trained on much shorter states, so Watch Tower sends the title, about 300 words, and the matched snippets.

```bash
git clone https://github.com/jaredpalmer/kev.git
cd kev
uv sync --extra serve
# 32 GB Mac. On a smaller Mac, use jaredpalmer/kev-0.8b@v1.0 instead.
KEV_DATE_FACTS=1 uv run --extra serve python -m kev.serve \
  --run jaredpalmer/kev-4b@v1.0 --port 8009
```

Third-party MLX backends were checked and left out. [bnsd55/jevmlx](https://github.com/bnsd55/jevmlx) scores generic MLX chat models (Qwen2.5 aliases), and SemIf's MLX path scores stock Qwen. Kev 1.0 weights stay on `python -m kev.serve`, which already selects MLX on Apple Silicon.

Von speaks the same HTTP shape if you already run it. Point `WT_DECIDER_URL` at it. TinyJev is not a target.

**Ollama** is the other local fallback:

```bash
ollama pull qwen2.5:3b
export WT_DECIDER=ollama
```

The lexical backend is a fixed heuristic, not a calibrated probability. The `0.85` line in the example indicators was checked on the fixture set for that heuristic. After you switch to Kev, re-fit the thresholds on your own labels (`wt eval` is only the fixture set).

### Confidence cascade

Each indicator can set `push_min_confidence` and `uncertain_min` (the examples use `0.45` and `0.15`). A score that clears `alert_if` still pushes only when confidence is at least `push_min_confidence`. The middle band is stored as a digest instead of a phone push. Below `uncertain_min` it is dropped. `push_min_confidence: 0` (the field default) leaves this off.

`WT_ESCALATE=ollama` or `WT_ESCALATE=systemone` re-scores that middle band with the other local backend. The digest becomes a push only when the second answer is itself confident and still clears `alert_if`. Demo mode leaves escalation off. Lexical confidence is `abs(p_hit - 0.5) * 2`, so the example band does not demote the fixture pushes.

### Same event

Exact copies are still dropped (URL, content hash, simhash, or cosine). A second outlet telling the same story is grouped when the titles overlap, the simhash is close, or cosine is high. When the decider is Kev, one `noul` question asks whether it is the same event. The lexical decider answers that from the title overlap, so the offline demo groups a wire rewrite and still alerts a looser paraphrase on its own. A later item in an event that already pushed does not push that indicator again.

### Why an alert matched

Push and digest text, and the `matches` row, include the indicator name, matched keywords, a short snippet around each keyword, cosine, decision scores, backend, and event id.

### Injection guard, hidden text, spotlight

Before classification, Watch Tower strips zero-width and bidi characters, HTML comments, `hidden` / `aria-hidden`, `display:none`, `visibility:hidden`, `font-size:0`, and white-on-white text. A clean article with an injection only in that hidden text can still alert. A visible injection cannot push.

`WT_INJECTION_GUARD=heuristic` (the default, and what demo mode uses) is the offline pattern list. `WT_INJECTION_GUARD=deberta` also runs [`protectai/deberta-v3-base-prompt-injection-v2`](https://huggingface.co/protectai/deberta-v3-base-prompt-injection-v2) (Apache-2.0, DeBERTa-v3-base, about 184M parameters) when `pip install 'watch-tower[guard]'` is installed. That model is optional and was not downloaded here. Flagged items are held as digests and are not sent to Kev.

`fixtures/canaries.json` is a red-team set (visible injections, a zero-width split, a fake fence). `wt` tests assert that none of them push.

### Bluesky spam

Identical or near-identical Bluesky posts in one cycle are dropped before the classifier when at least three share the same normalized text (`WT_SPAM_MIN_CLUSTER`, default 3). Other sources are not clustered this way.

### Full text

`WT_FETCH_FULLTEXT=1` (the live default) fetches the article with trafilatura when an item already passed the prefilter and the body is short, which is the normal GDELT case. Install `watch-tower[extract]`. Extracted text is stripped and treated as untrusted. Demo mode does not fetch, and `example.test` URLs are never requested. The prefilter decision is not undone if the full text is less similar than the headline.

### Embeddings

`WT_EMBEDDER=auto` loads `Qwen/Qwen3-Embedding-0.6B` when `sentence-transformers` is installed, and otherwise uses a hashing embedder. Vectors are float32 blobs in SQLite and compared with sqlite-vec's `vec_distance_cosine`. Delete `data/watchtower.db` if you change embedder; the dimension is fixed for the life of the file.

Hashing cosine is not comparable to Qwen cosine. If you move to Qwen, expect to retune `prefilter.min_cosine`.

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
  push_min_confidence: 0.45 # confident pushes only; 0 disables the band
  uncertain_min: 0.15       # below this, an uncertain push is dropped
alert:
  channels: []              # Apprise URLs, or leave empty and set WT_NTFY_URL
```

`alert_if` / `digest_if` may use `hit`, `severity`, `cosine`, and `confidence`, comparisons, `and` / `or` / `not`, and parentheses. They are not Python.

An item passes the prefilter when its source matches and it is not blocked by `keywords_none` / `keywords_all`, and either cosine to the indicator (or a positive example) is at least `min_cosine` or a keyword, entity phrase, or regex hits. Only the top few candidates are sent to the decider.

`sources` entries are either a source name (`arxiv`, `gdelt`, `bluesky`, `openalex`, `mastodon`, `rss`) or `rss:<id>` for one feed from `config/sources.yaml`.

## Live sources

`config/sources.yaml` lists RSS/Atom feeds, arXiv categories, GDELT queries, an OpenAlex search, and optional Bluesky and Mastodon settings.

- **RSS / Atom** via feedparser. A feed's `group` is what indicators match: `group: ai-news` satisfies `rss:ai-news` even though the feed id is `huggingface-blog` or similar. The checked-in list adds labs and AI desks whose feeds returned XML (Hugging Face, OpenAI, Google AI, DeepMind, Simon Willison, MIT Technology Review, The Verge AI, BAIR, Microsoft Research, Interconnects, Latent Space, Sebastian Raschka, Last Week in AI, The Gradient, MarkTechPost, Lobsters AI) on top of the original three. `federal-register-ai` is `ai-policy`. Anthropic, Meta AI, and VentureBeat did not return a feed from here, so they are not in the list yet. The World Monitor catalog stays off.
- **arXiv** via the public Atom API (`cs.AI`, `cs.CL`, `cs.LG` by default).
- **GDELT DOC 2.0**, one request every five seconds. The article list returns titles and URLs, not full text, so the stored body starts as the headline. With `WT_FETCH_FULLTEXT=1` and trafilatura installed, a likely hit then fetches the page.
- **OpenAlex**. Set `WT_OPENALEX_KEY` (free, from [openalex.org/settings/api](https://openalex.org/settings/api)) for the daily free budget. A few keyless calls still work.
- **Bluesky Jetstream**. Set `bluesky.enabled: true` and install `watch-tower[bluesky]`. Each cycle reads a short window, not a gapless archive. Default endpoint: `wss://jetstream2.us-east.bsky.network/subscribe`. Repeated-text clusters are dropped before classification.
- **World Monitor feeds** (names and URLs only) live in `config/feeds/worldmonitor.yaml`. They stay off unless `worldmonitor.enabled` is true. See `docs/worldmonitor-ui.md` for what its briefing UI does that a future Watch Tower screen could borrow. No World Monitor code is included.
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
config/feeds/worldmonitor.yaml  feed URLs mined as data, off by default
config/indicators/*.yaml     one indicator per file
wt/                          ingest, embed, store, prefilter, decider, policy, alert
wt/experimental/jepa_head.py optional linear predictor in embedding space
fixtures/                    offline demo items, canaries, parser samples
docs/worldmonitor-ui.md      UI notes only; there is no Watch Tower UI
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

- **Kev 1.0** (`kev-0.8b`, `kev-4b`, `kev-9b`, `kev-27b`, tag `@v1.0`) is the decision model. Its server already uses MLX on Apple Silicon, so there is no second MLX backend. jevmlx and SemIf exist and were rejected because they score generic Qwen checkpoints, not Kev 1.0. The weights were not downloaded in this environment, so the backend that actually ran end to end is the **lexical** fallback. The System One client was checked against a mock of Kev's published JSON, including the same-event question. `wt doctor` tells you if a live server is up.
- **DeBERTa prompt-injection v2** is optional and was not downloaded. Canaries are blocked by the offline heuristic.
- **Qwen3-Embedding-0.6B** is the live default when sentence-transformers is installed. Demo mode and machines without the package use a hashing embedder so the pipeline runs with no download. Do not reuse hashing thresholds as if they were Qwen scores.
- **sqlite-vec** is used through `vec_distance_cosine` on float32 blob columns (the documented non-virtual-table path). `vec0` virtual tables want integer row ids and a frozen dimension, which fights text ids and switching embedders.
- **Entities** in the YAML are literal phrases. GLiNER is not bundled.
- **Feedback HTTP** is the standard library, not FastAPI.
- **Scheduler** is a sleep loop (`wt serve`) or cron, not APScheduler.
- **Bluesky** is a bounded sample per cycle on `jetstream2.us-east.bsky.network`, which is the current public subscribe host. The older path in the research note can be pasted into `endpoint` if you prefer it.
- **JEPA head** (`wt/experimental/jepa_head.py`) is a small ridge map. It is not on the alert path. There is still no production open text-JEPA embedder; the pipeline does not pretend otherwise.
- World Monitor's feed inventory is mined as URLs and names only (`config/feeds/worldmonitor.yaml`). Its AGPL code is not copied. `docs/worldmonitor-ui.md` is a short note for a future UI, which this repo does not build.
- RSSHub and other AGPL projects mentioned in the research are not copied.
- Lexical probabilities are **not calibrated**. The fixture eval is a regression check for the heuristic, not a claim about Kev's precision.

## License

Apache-2.0. See `LICENSE`.

# Watch Tower

Watch Tower watches news, papers, and social posts for things you care about. You describe each thing as an **indicator** (a YAML file). Incoming items are embedded, cheaply prefiltered, then scored by a pluggable classifier. A strong enough score sends an alert. A weaker score waits for the digest. You can mark each alert right or wrong, and those labels are kept for later threshold tuning or a Kev fine-tune.

It is meant to run on an Apple Silicon Mac. The offline path is free. Jev, the hosted System One classifier, is optional and billed only when `JEV_API_KEY` is set. X/Twitter is not supported.

## Pipeline

```
feeds → strip hidden text → drop Bluesky spam clusters → dedupe
      → group the same event → embed → cosine + keyword prefilter
      → optional full text for likely hits
      → injection guard → decider (Jev, then Kev, then lexical)
      → confidence cascade / cooldown → ntfy or Apprise → thumbs up/down
```

Feed text is untrusted. Hidden and invisible text is removed first. What remains is trimmed, scrubbed of instruction-shaped phrases, and wrapped in a spotlight fence (`untrusted=true`, body lines quoted with `>`). Jev sees a shorter slice: the title, the windows around the matched terms, and dates and counts already compared in code. Kev still sees the title plus about 300 words. A model is only allowed to produce a score. Alerts are notifications. Nothing in an article is executed. A flagged item is not sent to Jev or Kev and cannot push. A pasted upload or a private file stays on the local decider.

## Demo mode

Demo mode needs no network and no model download. It replays the fixture items through the real pipeline: hashing embeddings, SQLite, sqlite-vec, the lexical decider, and the example indicators. Full-text fetch and escalation stay off.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
wt demo
```

You should see at least one `[push]` block on stdout, including the LLM-JEPA weight release. Exit code is 0 when a push fired. The demo database is `data/demo.db` and is replaced every run.

## Desk

The desk is the screen for the same pipeline. It is a local site, not a hosted app: one shared wire, and a named desk per person.

```bash
wt desk
```

That starts the API on `127.0.0.1:8641` and the browser UI on [http://127.0.0.1:47221](http://127.0.0.1:47221). The first open replays the fixture wire into `data/desk.db` when that database is empty. Replay fixtures replaces it. Pull live feeds uses the real sources and can fail a feed without taking the rest of the page down.

- **Highway.** One lane per indicator. Push, digest, and suppressed items sit on the lane. A clear lane is a quiet road.
- **Trace.** Ten numbered gates, in the order an article actually moves: source, visible text, spam, dedupe, event, prefilter, injection, decision, cascade, policy. Hit and miss labels are recorded here.
- **Indicators.** Edit the name, keywords, cosine floor, alert language, and confidence band. The next pull uses the saved YAML.
- **Digest.** Unread digest-tier matches for the desk you have open. Marking it read moves only that desk's cursor.
- **Export.** Markdown briefing, CSV, JSON packet, and Kev JSONL. Kev rows come from hit and miss labels, so an unlabeled wire exports an empty file. A desk only offers the formats it has turned on.
- **Desks.** Weston follows every lane. Research, Policy, and Releases are narrower. Open a new desk for someone else on the machine. An empty lane list means the whole wire. Edits go to `data/desks.yaml` and leave `config/desks.yaml` as the seed.

There is no login. A desk is a local view of one database, so two people on one Mac share the wire and keep their own lanes, digest cursor, and export set.

### Rip a URL

The catalog's manual upload is also a rip desk at [http://127.0.0.1:47221/rip](http://127.0.0.1:47221/rip). Opening that address, including from a bookmarklet, does not fetch the article. **Rip now** and **Subscribe** are the actions, and each one sends a CSRF token the page just minted. A POST without that token, or from another site, is refused.

```bash
wt rip https://example.com/story
wt subscribe https://example.com/ --dry-run
wt bookmarklet          # prints the bookmarklet for port 47221
wt rip-token            # bearer token for the Share Sheet, stored in data/rip.token
```

**Bookmarklet, Safari or Chrome.** Show the bookmarks bar. Run `wt bookmarklet` and make a bookmark whose address is that whole `javascript:` line (Safari: add a bookmark, then edit the address; Chrome: new bookmark, paste into the URL field). `extras/bookmarklet.txt` is the same line. On a story, click the bookmark. A window opens on the desk with the page URL, title, and any text you had selected. Then click Rip now or Subscribe. The bookmarklet navigates to localhost. It does not `fetch()` the desk, which is what gets blocked as a local-network request.

**Share Sheet, Mac or iPhone.** Shortcuts cannot be exported reliably from here, so the steps live in `extras/shortcuts/README.md`. Short version: new shortcut, show in Share Sheet, accept URLs and Safari pages and text, menu for Rip now or Subscribe. On the home Wi-Fi, POST `http://<mac>.local:47221/api/rip` with `Authorization: Bearer` set to the output of `wt rip-token` and a JSON body `{"url":"…","mode":"rip"}`. Off that Wi-Fi, POST the single URL to a private ntfy topic and let `wt inbox` pick it up (`WT_NTFY_INBOX` is the topic's `/json` URL). A message with no URL, or with more than one, is ignored. `wt desk --lan` is only needed if a phone should hit the API port directly. The desk on 47221 already accepts LAN connections and proxies `/api/rip`.

`extras/launchd/` has two example plists. Replace `/Users/weston/watch-tower` with the checkout path before loading them.

There is no paywall bypass. A short page is a paste or a stub. Playwright is optional (`pip install -e ".[render]"` and `playwright install chromium` on the Mac) and is not installed in this environment.

### Catalog

Catalog is the source list the highway was missing. Every feed in `config/sources.yaml` is a row: RSS, arXiv, GDELT, OpenAlex, Bluesky, Mastodon, the World Monitor catalog, and X. A row is **armed** when the next live pull will use it, **off** when it is disabled (Bluesky, Mastodon with no hashtags, World Monitor, and X), **live** after a ping returns, **quiet** when the ping fails, and **seen** when items are already on the wire. Ping sources checks the configured URLs from this machine. It does not turn a disabled source on.

The tether draws a moving line from a source to push, digest, suppressed, or drop only when that source produced a scored item. The published-date bar (24h, 7d, 30d, or a from/to range) filters the highway, the digest, the catalog, and the exports. Sources that have no publish date stay in the catalog and drop out of a dated window.

Manual upload takes a paste or a URL. A URL with no paste runs the rip chain (Trafilatura strict, then recall, then readability, then an optional Playwright render, with PDF, Bluesky, and X oEmbed as special cases). A thin page can be stored from a paste. Hidden text is stripped, a hidden instruction is flagged, and the item is scored like any other upload. It is listed under manual upload and extraction, next to any full-text extract the live pull already saved.

Subscriptions are written to `config/sources.local.yaml`, which is gitignored, and merged on top of `config/sources.yaml`. They show up in the catalog. The curated file is not rewritten. A subscription that `robots.txt` disallows is refused. A one-off rip only warns.

Two export pathways sit on top of the older briefing formats. **OPML** is the source list. **Tether** is the packet from sources to decisions for the desk and the date window you have open. Kev JSONL is unchanged and still needs hit and miss labels.

`wt demo` always starts on the hashing embedder and the lexical decider, even if `WT_EMBEDDER`, `WT_DECIDER`, or `JEV_API_KEY` are set. Pass `--decider kev` or `--embedder qwen` only when you want the demo fixtures scored by a local server or the real embedding model. Demo fixtures are not sent to Jev.

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
wt doctor          # indicators, sqlite-vec, Jev OK/FAIL, whether Kev or Ollama is up
wt run             # one ingest cycle
wt serve --interval 15
```

Alerts print locally. To push them to a phone, pick an unguessable [ntfy](https://ntfy.sh) topic (the server is Apache-2.0; you can also self-host it):

```bash
export WT_NTFY_URL='ntfy://ntfy.sh/replace-with-a-long-random-topic'
```

Other services go through [Apprise](https://github.com/caronc/apprise) (BSD-2-Clause), either in that env var as a comma-separated list or per indicator under `alert.channels`.

### Classifier

Jev is the primary classifier when a key is set. Kev is the offline fallback. The lexical decider is the offline demo. Set the key in the shell. It is never written into the repo:

```bash
export JEV_API_KEY='…'                       # from TypeSafe; do not commit it
export JEV_BASE_URL='https://api.typesafe.ai'  # optional; this is the default
export JEV_MODEL='jev-1.13.0'                # optional; pinned, not jev-latest
export WT_DECIDER=jev                        # optional; unset or auto already picks jev when the key is present
```

`wt doctor` prints `jev: OK` or `jev: FAIL`. OK means `GET /v1/models` answered with that bearer key. FAIL with no key does not call the network. The key is not printed.

The client speaks the official API directly (`POST /v1/systemone`, `Authorization: Bearer`). It does not use a third-party wrapper, and it will not send the key to `jevtypesafeai.com`. One item is one request: every noul, choice, and score question for that item goes in the same body. There is no separate batch endpoint. 429 and 529 are retried with backoff. 401 and 422 are not. Answers are cached on the artifact hash, the indicator, and `WT_JEV_PROMPT_VERSION`.

The decider is selected with `WT_DECIDER`:

| Value | What it calls |
| --- | --- |
| `auto` | Jev when `JEV_API_KEY` is set, else local Kev if it answers, else Ollama, else lexical |
| `jev` | `POST https://api.typesafe.ai/v1/systemone` with model `jev-1.13.0` |
| `kev` | Local Kev, `POST /v1/systemone` on `WT_DECIDER_URL` (default `http://127.0.0.1:8009`). `systemone` is the same |
| `ollama` | A local Ollama chat model forced to return JSON (`WT_OLLAMA_MODEL`) |
| `lexical` | Offline keyword / example overlap. This is what demo mode uses, even if a key is set |

`WT_DECIDER_FALLBACK=1` (the default) is the order Jev, then Kev, then lexical. A transport error on Jev uses local Kev when that server is up, and the lexical decider otherwise. The monthly cap uses that same local chain even when the fallback flag is off. Explicit `lexical` never calls Jev. `WT_DECIDER=jev` with no key does not call the network; it uses Kev or lexical.

Manual uploads (a paste on the catalog) and private files (`method: paste`, or a `file:` URL) set `local_only` and stay off Jev. A public URL rip can be scored by Jev. `WT_JEV_ALLOW_PRIVATE=1` sends the private ones too.

Spend is `usage.input_tokens` times the configured price. Output tokens are free. The default is `$0.042` per million input tokens, which is the TypeSafe list price (`$42` per billion). Change it with `WT_JEV_USD_PER_MTOK` or the Spend panel on the desk. The monthly cap defaults to `$10` (`WT_JEV_MONTHLY_USD`, `0` means no cap). A day at or over `$1` (`WT_JEV_DAILY_WARN_USD`) warns and still calls Jev until the monthly cap. The Spend panel reads `data/desk.db`. `wt run` writes `data/watchtower.db` unless `WT_DB` points at the desk file.

Jev 1.13 leans toward the first Choice option, so options are rotated per item. It is weak at dates and counts, so those are computed locally and attached as code facts. Long irrelevant text is left out; the request sends the matched windows. Injected text can still sway it, so the injection guard runs first and the spotlight fence stays on the state. A request is kept inside the published limits: 64k tokens total, 32k for the state plus the longest question, and at most 255 Choice options. The service limit is 100k tokens per second and 80 requests per second; 429 is retried.

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

Exact copies are still dropped (URL, content hash, simhash, or cosine). A second outlet telling the same story is grouped when the titles overlap, the simhash is close, or cosine is high. When the decider is Jev or Kev, one `noul` question asks whether it is the same event. The lexical decider answers that from the title overlap, so the offline demo groups a wire rewrite and still alerts a looser paraphrase on its own. A later item in an event that already pushed does not push that indicator again.

### Why an alert matched

Push and digest text, and the `matches` row, include the indicator name, matched keywords, a short snippet around each keyword, cosine, decision scores, backend, and event id.

### Injection guard, hidden text, spotlight

Before classification, Watch Tower strips zero-width and bidi characters, HTML comments, `hidden` / `aria-hidden`, `display:none`, `visibility:hidden`, `font-size:0`, and white-on-white text. A clean article with an injection only in that hidden text can still alert. A visible injection cannot push.

`WT_INJECTION_GUARD=heuristic` (the default, and what demo mode uses) is the offline pattern list. `WT_INJECTION_GUARD=deberta` also runs [`protectai/deberta-v3-base-prompt-injection-v2`](https://huggingface.co/protectai/deberta-v3-base-prompt-injection-v2) (Apache-2.0, DeBERTa-v3-base, about 184M parameters) when `pip install 'watch-tower[guard]'` is installed. That model is optional and was not downloaded here. Flagged items are held as digests and are not sent to Jev or Kev.

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
- **World Monitor feeds** (names and URLs only) live in `config/feeds/worldmonitor.yaml`. They stay off unless `worldmonitor.enabled` is true. `docs/worldmonitor-ui.md` notes what that briefing does. The desk borrows the idea of one wire and several audiences. No World Monitor code is included.
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

## Forensic extraction

SQLite is the source of truth: documents, mention spans, entities, aliases, reversible resolution judgments, relations, and events with roles. Every assertion points at a character span. The type list lives in `config/ontology.yaml` (a FollowTheMoney-compatible core, plus AI and cyber types). A graph view only reads that database. LadybugDB is not included. A local-LLM deep mode is a disabled proposer interface.

The stub proposer is the default (`WT_EXTRACTOR=stub`). It uses a gazetteer, a CVE pattern, and `dateparser` for dates and amounts. `pip install -e ".[forensics]"` adds spaCy, GLiNER, and fastcoref. `WT_EXTRACTOR=auto` uses those when they import, and falls back to the stub. Tests never download weights. REBEL, Maverick, GENRE, and GLiREL are not used.

Jev, when `JEV_API_KEY` is set and the item is not local-only, types and checks each candidate in one billed request per chunk. Choice options are rotated. Dates and numbers stay in code. The same monthly cap and cache apply. Wikidata linking is a stub. If the key is missing, the budget is spent, or the call fails, the local candidates are kept.

A high-value item is queued when an alert is pushed, the user stars or rips it, or the source is a paper (`arxiv`, `openalex`) or an advisory (`cisa_kev`, `nvd`). The rule and the 50-extraction daily cap are in `config/ontology.yaml`. `wt extract` drains the queue.

The Extract desk (nav on [http://127.0.0.1:47221](http://127.0.0.1:47221)) highlights spans by type, lists a review queue sorted by uncertainty, stores every accept, reject, edit, and merge as a label, and opens an entity page. The link chart is Sigma.js on a Graphology graph. A neighbourhood of at most 12 nodes uses Cytoscape.js. Highlights are a small React layer. `@recogito/text-annotator` is BSD-3-Clause and was left out so the desk stays on the existing stack.

Keyless sources: CISA KEV, NVD 2.0, the Hugging Face Hub models API, and the Federal Register JSON API. The Federal Register RSS feed is unchanged. GDELT GKG and events have parsers; live zip download is off (`gkg_enabled: false`) because those files are large. OpenAlex sends `WT_OPENALEX_KEY` as the `api_key` query parameter when the variable is set.

`wt extract-eval` scores exact span and type matches against `eval/extraction_gold.json`.

Not in this build, pending a later decision: LadybugDB, a local-LLM deep mode, Telegram, ACLED, and ReliefWeb.

## Layout

```text
config/sources.yaml          feeds, arXiv, GDELT, OpenAlex, KEV, NVD, Hub, Federal Register
config/ontology.yaml         forensic types, high-value rule, daily cap
config/feeds/worldmonitor.yaml  feed URLs mined as data, off by default
config/indicators/*.yaml     one indicator per file
config/desks.yaml            seed desks; live edits go to data/desks.yaml
wt/                          ingest, embed, store, prefilter, decider, policy, alert, desk API
web/                         the desk UI (Next.js)
wt/experimental/jepa_head.py optional linear predictor in embedding space
fixtures/                    offline demo items, canaries, parser samples
docs/worldmonitor-ui.md      briefing notes; no World Monitor code
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

- **Jev 1.13** (`jev-1.13.0` at `https://api.typesafe.ai/v1/systemone`) is the hosted classifier when `JEV_API_KEY` is set. This environment has no key, so the client was checked against the live response shape with mocks. `wt doctor` prints `jev: OK` or `jev: FAIL`. Kev 1.0 stays the offline fallback.
- **Kev 1.0** (`kev-0.8b`, `kev-4b`, `kev-9b`, `kev-27b`, tag `@v1.0`) is the local decision model. Its server already uses MLX on Apple Silicon, so there is no second MLX backend. jevmlx and SemIf exist and were rejected because they score generic Qwen checkpoints, not Kev 1.0. The weights were not downloaded in this environment, so the backend that actually ran end to end without a key is the **lexical** fallback. The System One client was checked against a mock of Kev's published JSON, including the same-event question. `wt doctor` tells you if a live server is up.
- **DeBERTa prompt-injection v2** is optional and was not downloaded. Canaries are blocked by the offline heuristic.
- **Qwen3-Embedding-0.6B** is the live default when sentence-transformers is installed. Demo mode and machines without the package use a hashing embedder so the pipeline runs with no download. Do not reuse hashing thresholds as if they were Qwen scores.
- **sqlite-vec** is used through `vec_distance_cosine` on float32 blob columns (the documented non-virtual-table path). `vec0` virtual tables want integer row ids and a frozen dimension, which fights text ids and switching embedders.
- **Entities** in indicator YAML are still literal phrases. Forensic extraction is a separate path (`config/ontology.yaml`, the Extract desk). GLiNER is an optional extra, not imported by the default install.
- **Feedback links** in an alert still use the standard-library server (`wt feedback-server`). The desk is a separate FastAPI process plus the Next.js screen.
- **Scheduler** is a sleep loop (`wt serve`) or cron, not APScheduler.
- **Bluesky** is a bounded sample per cycle on `jetstream2.us-east.bsky.network`, which is the current public subscribe host. The older path in the research note can be pasted into `endpoint` if you prefer it.
- **JEPA head** (`wt/experimental/jepa_head.py`) is a small ridge map. It is not on the alert path. There is still no production open text-JEPA embedder; the pipeline does not pretend otherwise.
- World Monitor's feed inventory is mined as URLs and names only (`config/feeds/worldmonitor.yaml`). Its AGPL code is not copied. The desk is Watch Tower's own screen. `docs/worldmonitor-ui.md` only records what that other briefing does.
- RSSHub and other AGPL projects mentioned in the research are not copied.
- Lexical probabilities are **not calibrated**. The fixture eval is a regression check for the heuristic, not a claim about Kev's precision.

## License

Apache-2.0. See `LICENSE`.

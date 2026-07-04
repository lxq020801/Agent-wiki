# obsidian-librarian Douyin Ingest Tool

This folder is the Douyin execution layer for the top-level
obsidian-librarian Agent skill. The Agent-facing entrypoint is:

```bash
python3 scripts/ingest_url.py "<douyin-url>"
```

The Chrome extension may submit a Douyin ingest task as an auxiliary entry.
It must not run the ingest itself; the Agent/local execution layer owns
download, analysis, vault writes, status, and git commits.

## Modules

| Module | Responsibility |
|---|---|
| `scripts/ingest.py` | Orchestrate download, analysis, vault write, index update, git commit |
| `scripts/downloader.py` | Resolve Douyin URL, inject Cookie in memory, download mp4 |
| `scripts/analyzer.py` | Choose Ark video input path, call Responses API, return analysis text |
| `scripts/config_loader.py` | Load `~/.obsidian-librarian/config.toml` |
| `scripts/status_writer.py` | Write diagnostic status JSON for Agent/debugging |
| `scripts/cost_estimator.py` | Estimate RMB cost from model usage |
| `vendor/` | Embedded Douyin crawler code; treat as read-only |

## Runtime Inputs

The WebSocket control server writes:

- `~/.obsidian-librarian/config.toml`
- `~/.obsidian-librarian/cookie/douyin.txt`

`config_loader.py` must be able to read the TOML written by
`server/websocket_server.py` without compatibility shims.

## Current Flow

1. `scripts/ingest_url.py` runs `install/bootstrap.py`.
2. `ingest.py --url` loads config and validates Ark API key, vault path, and
   Cookie path. P0 always uses the `quality` analysis profile.
3. `downloader.py` converts the extension's Netscape Cookie file into a header
   string and monkey-patches the vendor crawler in memory.
4. `analyzer.py` uses the ordinary Ark API path only: upload the local video
   through Files API with `preprocess_configs.video.fps` and
   `preprocess_configs.video.model`, wait for the file to become `active`, then
   call Responses API with `input_video.file_id` and `store=true`.
5. `ingest.py` chooses an intent-specific prompt and writes a SCHEMA-compliant
   Markdown note by asset purpose:
   - `knowledge_ingest` -> `知识资产/知识入库/` with `asset_family: knowledge_asset`
   - `viral_breakdown` -> `知识资产/创作模式/` with `asset_family: creative_pattern`
   It then updates `index.md` and commits only the files touched by this ingest.
   If a task contains both intents, the source downloads once. Non-long videos
   reuse one active `file_id` for both prompt runs; long videos reuse one
   download but create separate overview/chunk `file_id`s for the strategy and
   chunk analysis chain.

`--task` is used by the WebSocket task queue and remains useful for debugging.

## Ark Video Rules

- Ordinary Ark API must use Files API for local video upload. This is the
  official recommended path and supports up to 512 MB in Ark-managed storage.
- Agent Plan is not a runtime path. Real probes showed `/api/plan/v3/files`
  returns 404 and Agent Plan keys return 401 on ordinary `/api/v3/files`.
  Historical inline base64 success is documented in
  `docs/ark-video-understanding.md`, but the product path no longer uses it.
- In `file_id` mode, set `preprocess_configs.video.fps` during upload, not
  during analysis.
- In `file_id` mode, set `preprocess_configs.video.model` during upload so Ark
  applies the current video-understanding preprocessing strategy.
- In `file_id` mode, wait for official file status `active` before Responses
  API. `processing` means keep polling; `failed` means stop and surface the file
  error.
- Ordinary Ark Responses content uses `{"type": "input_video", "file_id": ...}`
  plus an `input_text` prompt.
- P0 fixes analysis to `quality` (1250 target frames). The Chrome extension must
  not expose quality, fps, or target-frame settings.
- Re-upload when fps/model preprocessing changes; do not cache `file_id`.
- Responses memory is short-term only. Store returned `response_id` under
  `~/.obsidian-librarian/responses-memory/` for 3 days; never write it into
  vault Markdown, task status, or strategy logs.
- Videos longer than 10 minutes first run a full-video overview at dynamic fps,
  capped at `1fps` and lowered against the 1250-frame safety target, with the
  strategy model (`models.strategy`, default mini). If even the official minimum
  `0.2fps` would exceed the safety target, skip the overview, log the reason,
  and fall back to conservative `5fps` chunks. The overview extracts rough
  content and a per-chunk strategy, then 240s chunks with 10s overlap are
  uploaded/analyzed independently at `2-5fps` by the main analyzer model with
  default 2-way concurrency, configurable from 1 to 4. Invalid JSON, missing
  segments, or missing required fields may be repaired once by the same strategy
  model via `previous_response_id`; missing evidence, low confidence, or high
  low-fps risk must fall back conservatively toward `5fps`. Text-only Responses
  then synthesizes the final asset body from the overview and chunk results.
- Strategy fallbacks and JSON repair results are logged to
  `~/.obsidian-librarian/logs/video-strategy-events.jsonl` without API keys,
  Cookies, Bearer tokens, or `response_id`.

## Output Contract

Markdown frontmatter must follow `SCHEMA.md`:

```yaml
id: "20260627-video-001"
type: video_analysis
asset_family: knowledge_asset
source_media: douyin_video
ingest_intent: knowledge_ingest
source_url: "https://v.douyin.com/..."
tags: [douyin, knowledge-asset, case-study, video-analysis]
confidence: medium
status: active
```

The note body should include source metadata, one-sentence summary, model output,
and analysis metadata. API keys and Cookies must never be written to Markdown,
logs, or final Agent replies.

## Verification

Use the top-level test suite:

```bash
python3.11 tests/test_p0_static.py
```

For real end-to-end validation, first sync config and Cookie through the
extension, then run:

```bash
python3 scripts/ingest_url.py "<douyin-url>"
```

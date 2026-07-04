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
   If a task contains both intents, the video path downloads once and ordinary
   Ark reuses one active `file_id` for both prompt runs.

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
  `~/.obsidian-librarian/responses-memory/`; never write it into vault Markdown.
- Videos longer than 10 minutes are split into 240s chunks with 10s overlap.
  Each chunk is uploaded/analyzed independently, then text-only Responses
  synthesizes the final asset body.

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

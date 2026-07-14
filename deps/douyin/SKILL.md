# Agent-wiki Douyin Ingest Tool

This folder is the Douyin execution layer for the top-level
Agent-wiki skill. The Agent-facing entrypoint is:

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
| `scripts/config_loader.py` | Load `~/.agent-wiki/config.toml` |
| `scripts/status_writer.py` | Write diagnostic status JSON for Agent/debugging |
| `scripts/cost_estimator.py` | Estimate RMB cost from model usage |
| `scripts/derive_strategy.py` | Score, dedupe, and record bounded derivation candidates |
| `scripts/derive_executor.py` | Resolve approved derived targets, generate child assets, and link parent/child notes |
| `vendor/` | Embedded Douyin crawler code; treat as read-only |

## Runtime Inputs

The WebSocket control server writes:

- `~/.agent-wiki/config.toml`
- `~/.agent-wiki/cookie/douyin.txt`

`config_loader.py` must be able to read the TOML written by
`server/websocket_server.py` without compatibility shims.

## Current Flow

1. `scripts/ingest_url.py` runs `install/bootstrap.py`.
2. `ingest.py --url` loads config and validates Ark API key, vault path, and
   Cookie path. The current runtime always uses the `quality` analysis profile.
3. `downloader.py` converts the extension's Netscape Cookie file into a header
   string and monkey-patches the vendor crawler in memory.
4. `analyzer.py` uses the ordinary Ark API path only: upload the local video
   through Files API with `preprocess_configs.video.fps` and
   `preprocess_configs.video.model`, wait for the file to become `active`, then
   call Responses API with `input_video.file_id` and `store=true`.
5. `ingest.py` chooses the media-specific knowledge prompt and writes one
   SCHEMA-compliant source note to `知识资产/知识入库/` with
   `asset_family: knowledge_asset` and `ingest_intent: knowledge_ingest`. It then
   updates `index.md` and commits only the files touched by this ingest.
6. For `knowledge_ingest`, `derive_strategy.py` turns model-discovered follow-up
   leads into bounded candidates. It writes full candidate records under
   `系统记录/派生任务候选/`; the parent Markdown only stores
   `derived_candidate_record` and `derived_candidate_ids` plus a readable
   summary table. High-confidence, low-risk, resolvable candidates may be queued
   as `derived_ingest` tasks by the WebSocket service after the parent asset is
   written. Ambiguous or missing-target candidates remain pending for extension
   confirmation. Debuggable process nodes live under
   `run-artifacts/{task_id}/05-derive/` and
   `run-artifacts/{child_task_id}/05-derive-executor/`, not in the asset body.

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
- The current runtime fixes analysis to `quality` (1250 target frames). The Chrome extension must
  not expose quality, fps, or target-frame settings.
- Re-upload when fps/model preprocessing changes; do not cache `file_id`.
- Responses memory is short-term only. Store returned `response_id` under
  `~/.agent-wiki/responses-memory/` for 3 days; never write it into
  vault Markdown, task status, or strategy logs.
- Videos longer than 10 minutes first run a full-video overview at `1fps` when
  duration is `<= 1230s` (20m30s), leaving about 20 frames of margin below the
  1250-frame safety target, with the strategy model (`models.strategy`, default
  mini). If duration is `> 1230s`, treat it as an ultra-long video: split the
  overview phase too, analyze each 240s chunk at `1fps`, synthesize those rough
  overviews into the same global strategy JSON, then continue through the normal
  long-video precision pass. This means duration scales by chunk count; the
  practical limits are still file size, download time, task timeout, and model
  context windows. The overview extracts rough content, information carriers,
  and a `lite_brief` for the main analyzer model; fps is for visual/OCR/action
  risk, while high concept density should be handled in the Lite prompt rather
  than automatically raising fps. Then
  240s chunks with 10s overlap are uploaded/analyzed independently at `2-5fps`
  by the main analyzer model with default 2-way concurrency, configurable from
  1 to 4. Invalid JSON, missing segments, or missing required fields may be
  repaired once by the same strategy model via `previous_response_id`; structural
  fallback and fps adjustment are tracked separately. Text-only Responses then
  synthesizes the final asset body from the overview and chunk results.
- Video ingest writes inspectable intermediate artifacts under
  `~/.agent-wiki/run-artifacts/{task_id}/`: mini chunk overview
  prompts/outputs, strategy synthesis and repair artifacts, Lite chunk
  prompts/outputs, and final synthesis prompt/output.
- Strategy fallbacks and JSON repair results are logged to
  `~/.agent-wiki/logs/video-strategy-events.jsonl` without API keys,
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

Derivation candidate contract:

- Only `knowledge_ingest` generates derivation candidates.
- Allowed target types: `github_project`, `official_doc`, `web_research`.
- Full candidate fields, scores, evidence, dedupe status, parent lineage, and
  acceptance criteria live in `系统记录/派生任务候选/*.json`.
- Raw candidate extraction, normalization, target resolution, source material,
  prompt/output, write result, and linkback records live in runtime
  `run-artifacts/`.
- Candidate-stage Markdown must not contain future `[[wikilink]]` targets. The
  derived executor writes child assets first, then updates parent/child links.
- GitHub candidates may omit URL when the project name and context are strong;
  `derive_executor.py` resolves them through GitHub API search plus README
  comparison before writing the child asset.
- Only high-confidence GitHub candidates are eligible for automatic enqueue in
  the current runtime. `official_doc` and `web_research` remain candidates that
  require manual confirmation or a supplied URL until official-domain and
  multi-source verification are implemented.
- Frontmatter only stores lightweight references:

```yaml
derived_candidate_record: "系统记录/派生任务候选/20260705-example.json"
derived_candidate_ids: ["dt-..."]
```

- Do not write full candidate objects, `scores`, `evidence`, `dedupe`, or
  execution status objects into asset frontmatter.

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

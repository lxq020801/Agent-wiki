# Agent-wiki Douyin Ingest Tool

This folder is the Douyin execution layer for the top-level
Agent-wiki skill. The Agent-facing entrypoint is:

```bash
python3 scripts/ingest_url.py "<douyin-url>"
```

The Chrome extension may submit a Douyin ingest task as an auxiliary entry.
It must not run the ingest itself; the Agent/local execution layer owns
download, analysis, vault writes, index updates, and status.

## Modules

| Module | Responsibility |
|---|---|
| `scripts/ingest.py` | Orchestrate download, analysis, source-note write, image raw-media write, and index update; video cache is task-private (`cache/videos/<task_id>/`) and removed after the run |
| `scripts/downloader.py` | Resolve Douyin URL, inject Cookie in memory, download mp4 |
| `scripts/analyzer.py` | Choose Ark video input path, call Responses API, return analysis text |
| `scripts/config_loader.py` | Load `~/.agent-wiki/config.toml` |
| `scripts/status_writer.py` | Write diagnostic status JSON for Agent/debugging |
| `scripts/cost_estimator.py` | Estimate RMB cost from model usage |
| `scripts/derive_strategy.py` | Parse, validate, deduplicate, and persist derivation candidates |
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
4. `analyzer.py` uses the task-selected fixed 1/2/5 FPS policy, then follows the
   ordinary Ark API path only: upload the local video through Files API
   with `preprocess_configs.video.fps` and
   `preprocess_configs.video.model`, wait for the file to become `active`, then
   call Responses API with `input_video.file_id` and `store=true`.
5. `ingest.py` chooses the media-specific knowledge prompt and writes one
   SCHEMA-compliant source note directly to `知识资产/` with
   `asset_family: knowledge_asset` and `ingest_intent: knowledge_ingest`. It then
   updates `index.md` without initializing, staging, or committing Git.
6. For `knowledge_ingest`, `derive_strategy.py` parses the model's internal JSON
   candidates without a fixed count limit. Candidate, pending, failure, and
   cancellation state stays in task JSON, `derived-actions/`, and
   `run-artifacts/`; it never enters the source Markdown. Structurally valid,
   safe, resolvable candidates may be queued as `derived_ingest` tasks after the
   parent asset is written. Ambiguous or missing-target candidates remain in the
   task system for confirmation. Only after a child asset really exists does the
   executor add `related` metadata and a `## 相关资产` link. Debuggable process
   nodes live under
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
- The current runtime has no adaptive quality/FPS configuration. Each task uses
  one explicit fixed 1, 2, or 5 FPS value (default 1) and does not run a local visual-change prescan.
  Legacy quality, target-frame, FPS range, and `video_fps_mode` keys are ignored
  and removed when config is rewritten.
- Re-upload when fps/model preprocessing changes; do not cache `file_id`.
- Responses memory is short-term only. Store returned `response_id` under
  `~/.agent-wiki/responses-memory/` for 3 days; never write it into
  vault Markdown, task status, or strategy logs.
- One configured model performs all video understanding. Mini remains an
  optional main-model preset; there is no separate Mini overview or strategy
  call.
- The whole video uses the same task-selected FPS value without prescan.
- A video is split only when `duration * selected_fps` exceeds the 1250-frame
  safety target. Each mechanical slice is `1250 / selected_fps` seconds and
  overlaps the previous slice by 10 seconds.
- Slices are uploaded and analyzed independently with default 2-way concurrency,
  configurable from 1 to 4. A text-only Responses call then removes overlap and
  synthesizes the final asset body. There is no semantic 240-second plan, rough
  analysis, per-slice FPS decision, or strategy JSON repair.
- Video ingest writes inspectable intermediate artifacts under
  `~/.agent-wiki/run-artifacts/{task_id}/`: the run manifest, original prompt,
  local sampling evidence, mechanical chunk plan, per-slice analysis
  prompts/outputs, and final synthesis prompt/output.
- `01-sampling/evidence.json` separates local reproduction frames and
  thumbnails, requested upload FPS/planned frame counts, and provider-returned
  facts. Ark does not return the exact frames consumed by the model, so that
  field remains explicitly unavailable instead of being inferred.
- Status JSON keeps an ordered, redacted `audit_events` timeline in addition
  to the latest progress snapshot.
- Cost estimation uses provider-returned usage only. It splits audio from
  non-audio input, preserves reasoning-token facts without double charging,
  applies the official input-length tier per model, and sums all analysis calls.
  Unknown pricing returns unavailable instead of using a fallback rate; the
  amount remains an estimate rather than the final invoice.
- Retry and sampling diagnostics never contain API keys, Cookies, Bearer tokens,
  or `response_id`.

## Output Contract

Markdown frontmatter must follow `SCHEMA.md`:

```yaml
id: "20260627-video-001"
type: video_analysis
asset_family: knowledge_asset
source_media: douyin_video
ingest_intent: knowledge_ingest
source_url: "https://v.douyin.com/..."
tags: [ai-agent, knowledge-asset, video-analysis, douyin]
confidence: medium
status: active
```

The note body uses `简洁概括`, `完整内容整理`, and clearly marked `AI 分析`.
All completed assets use the same `知识资产/` directory and three-section body. Derivation is provenance, not an asset family; preserve it with `ingest_intent`, `source_media`, source fields, and `derived_from`.
Model names, token counts, costs, and analysis parameters stay in status/audit
records. API keys and Cookies must never be written to Markdown, logs, or final
Agent replies.

Derivation candidate contract:

- Only `knowledge_ingest` generates derivation candidates.
- The only allowed derivation target is `github_project`.
- Full candidate fields, evidence, dedupe status, parent lineage, and
  acceptance criteria live in runtime `run-artifacts/`.
- Raw candidate extraction, normalization, target resolution, source material,
  prompt/output, write result, and linkback records live in runtime
  `run-artifacts/`.
- Candidate-stage Markdown must not contain future `[[wikilink]]` targets. The
  derived executor writes child assets first, then updates parent/child links.
- Candidate count has no fixed maximum. The model only extracts open-source
  project clues: optional name and organization, purpose, feature keywords,
  visual clues, direct open-source evidence, and a GitHub search query.
- GitHub candidates may omit both URL and project name when the source clearly
  presents an open-source project and provides enough distinctive search clues.
  `derive_executor.py` resolves them through GitHub API search plus repository
  description/README comparison before writing the child asset.
- Only a unique official API match can continue automatically. Missing or
  ambiguous targets remain `needs_target` for confirmation. Official documents,
  API documents, web research, ordinary products, companies, and generic concepts
  do not enter the derivation candidate list.
- Do not write candidate references, full candidate objects, `scores`,
  `evidence`, `dedupe`, or execution status objects into asset frontmatter.

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

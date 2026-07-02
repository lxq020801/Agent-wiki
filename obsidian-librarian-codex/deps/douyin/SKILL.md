# obsidian-librarian Douyin Ingest Tool

This folder is the Douyin execution layer for the top-level
obsidian-librarian Agent skill. The Agent-facing entrypoint is:

```bash
python3 scripts/ingest_url.py "<douyin-url>"
```

Do not make the Chrome extension the ingest trigger in P0. The extension only
syncs Ark config and Douyin Cookie.

## Modules

| Module | Responsibility |
|---|---|
| `scripts/ingest.py` | Orchestrate download, analysis, vault write, index update, git commit |
| `scripts/downloader.py` | Resolve Douyin URL, inject Cookie in memory, download mp4 |
| `scripts/analyzer.py` | Ark Files API upload, wait for active, Responses API analysis |
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

## P0 Flow

1. `scripts/ingest_url.py` runs `install/bootstrap.py`.
2. `ingest.py --url` loads config and validates Ark API key, vault path, and
   Cookie path. P0 always uses the `quality` analysis profile.
3. `downloader.py` converts the extension's Netscape Cookie file into a header
   string and monkey-patches the vendor crawler in memory.
4. `analyzer.py` uploads the local video through Ark Files API with
   `preprocess_configs.video.fps` and `preprocess_configs.video.model`, waits
   for the file to become `active`, then calls Responses API with
   `input_video` and the returned `file_id`.
5. `ingest.py` writes a SCHEMA-compliant Markdown note under
   `知识资产/视频分析/`, updates `index.md`, and commits only the files touched by
   this ingest.

`--task` remains as a compatibility/debug mode, but it is not the P0 main path.

## Ark Video Rules

- Use Files API for local video upload. This is the official recommended path
  for local videos and supports up to 512 MB in Ark-managed storage.
- Do not use base64 or public URL upload for P0 local videos; those paths are
  limited to 50 MB video / 64 MB request body.
- Set `preprocess_configs.video.fps` during upload, not during analysis.
- Set `preprocess_configs.video.model` during upload so Ark applies the current
  video-understanding preprocessing strategy.
- Wait for official file status `active` before Responses API. `processing`
  means keep polling; `failed` means stop and surface the file error.
- Use Responses API content `{"type": "input_video", "file_id": ...}` plus an
  `input_text` prompt.
- P0 fixes analysis to `quality` (1250 target frames). The Chrome extension must
  not expose quality, fps, or target-frame settings.
- Re-upload when fps/model preprocessing changes; no file_id cache in P0.
- Long-video slicing is not part of P0. If a video exceeds current single-file
  behavior, fail clearly or rely on Ark's uniform frame cap; do not silently
  create multi-part notes.

## Output Contract

Markdown frontmatter must follow `SCHEMA.md`:

```yaml
id: "20260627-video-001"
type: video_analysis
source_url: "https://v.douyin.com/..."
tags: [douyin, video-analysis, case-study]
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

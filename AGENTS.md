# Fusion Video Pipeline agent guide

This file applies to the entire `fusion-video-pipeline` repository.

## Project purpose and boundaries

- The primary product is the v7-style hierarchical knowledge tree. The detailed HTML report is a complementary reading view, not the project's core claim.
- Keep one canonical transcript, with stable source-unit IDs, as the shared input for the tree and report paths.
- The tree path uses one model in two stages: select and lock an organizing axis and skeleton, then fill the locked structure. Do not silently replace it with transcript-order summarization or a second evaluator model.
- Deterministic checks validate structure, references, coverage accounting, hierarchy, and layout. Never describe those checks as proof of semantic correctness.
- Bilibili ingestion uses existing CC subtitles. Missing subtitles must fail clearly; do not add a local Whisper fallback unless the task explicitly changes this product boundary.
- `video-report-agent` is an editable sibling dependency used for detailed-report generation. It is third-party code without a confirmed redistribution license. Do not copy its implementation into this repository or modify the sibling repository unless the task explicitly requires it; if modifying it, follow its own `AGENTS.md`.

## Repository map

- `src/fusion_video_pipeline/pipeline.py`: end-to-end orchestration, progress, retries, and publishing.
- `src/fusion_video_pipeline/notes.py`: Axis First tree generation, normalization, validation, and retry behavior.
- `src/fusion_video_pipeline/canonical.py`: canonical transcript and source-unit representation.
- `src/fusion_video_pipeline/report.py`: integration with the external report generator.
- `src/fusion_video_pipeline/interactive.py`: linked tree/report HTML behavior.
- `src/fusion_video_pipeline/web_server.py` and `web_ui.html`: local/public web application, history, progress, and previews.
- `src/fusion_video_pipeline/mindmap.py`: deterministic tree and mind-map rendering.
- `benchmark/`: human-gold-standard and blinded A/B evaluation workflow.
- `tests/`: unit and integration-style tests that do not require paid model calls.
- `runs/`, `cache/`, and `.runtime/`: generated local state. Treat them as data, not source files, and do not commit them.

## Editing rules

- Preserve the tree/report separation and the shared `source_units` contract when changing schemas or renderers.
- Prefer deterministic normalization only when intent can be inferred safely from existing fields. Reject ambiguous model output instead of guessing.
- Update tests whenever changing schemas, validation, retry accounting, history records, API behavior, security controls, or UI interactions.
- Keep API keys and access credentials in the process environment, an ignored `.env`, or request memory only. Never place secrets in source, fixtures, documentation, generated previews, logs, URLs, commits, or error messages.
- In public mode, preserve server-controlled model endpoints/providers, Obsidian paths, concurrency limits, HTML sandboxing, and security headers. Do not expose a raw HTTP listener directly to the internet.
- Do not claim that a real video, provider, tunnel, browser interaction, or Obsidian export works unless that path was actually exercised. Distinguish unit-test evidence from live integration evidence.
- Keep public examples free of private subtitles, run artifacts, human annotations, and local filesystem details.

## Local commands

Use the repository virtual environment or `uv`; do not install dependencies globally.

```powershell
uv sync --dev
uv run ruff check src tests
uv run pytest -q
git diff --check
```

When changing inline JavaScript in `web_ui.html`, also syntax-check the extracted script:

```powershell
$html = Get-Content src/fusion_video_pipeline/web_ui.html -Raw
$script = [regex]::Match($html, '(?s)<script>(.*)</script>').Groups[1].Value
$script | node --check -
```

For UI changes, start the local site and inspect the affected state in a browser. Check desktop and narrow layouts when sizing, splitting, overflow, or history-card behavior changes.

```powershell
.\run-fusion.ps1 serve
```

The managed local services are:

- Fusion site: `http://127.0.0.1:8766/`
- Benchmark site: `http://127.0.0.1:8765/`

```powershell
.\start-web-services.ps1
.\status-web-services.ps1
.\stop-web-services.ps1
```

Starting managed services also creates a temporary Cloudflare Quick Tunnel. Only start it when the task needs external preview, and do not treat its changing URL as a durable deployment.

## Verification by change type

- Documentation-only changes: run `git diff --check` and verify referenced paths and commands.
- Python changes: run Ruff, the focused tests, then the full test suite.
- Web UI changes: run Ruff and tests, run `node --check`, then inspect the relevant browser states.
- Pipeline or provider changes: complete the offline checks first. Make a paid or credentialed live call only when it is necessary for the requested verification, and report the provider/model actually exercised.
- Publishing or deployment changes: follow `SECURITY.md`, scan tracked content and the staged diff for secrets/private data, and verify the served result rather than relying only on process startup.

## Completion standard

A change is complete only when the relevant checks above pass, documentation matches observable behavior, generated/private artifacts remain untracked, and the final report separates verified results from untested assumptions.

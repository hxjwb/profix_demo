# Stall Review Implementation

## Goal

`stall_viewer.py` provides a local stall review tool for RTC profile analysis.

It combines:

- log parsing
- profile display
- local dashboard rendering
- LLM-based structured analysis
- optional thinking stream
- cache replay

## Entry

Main entry:

- `stall_viewer.py`

Related helper:

- `profile_time_formatter.py`

Prompt source:

- `prompt_simple.txt`

## Startup

Run:

```bash
python3 stall_viewer.py --model glm
```

Supported model presets:

- `gpt-5.2`
- `kimi`
- `glm`

The script:

1. parses CLI args
2. selects a model preset from env vars
3. parses the default log file or a user-provided log file
4. starts a Flask server on port `5000`

## Core Data Model

`parse_log()` scans the log file and extracts each `===PROFILE=== ... ===PROFILE_END===` block.

Each stall record contains:

- `stall_time`
- `gap_ms`
- `text_raw`: raw cleaned profile text
- `text`: profile text after relative-time formatting
- `elapsed_s`: relative position in the whole session

`text_raw` is used for numeric parsing.

`text` is used for:

- right-side profile display
- LLM prompt input

## Relative Time Formatting

This logic is intentionally split into a separate module:

- `profile_time_formatter.py`

Purpose:

- replace long absolute timeline numbers such as `3646865581`
- convert them into window-relative offsets such as `+362ms`

Rules:

- if `window_time_ms=[start,end]` exists, `start` is used as base timestamp
- otherwise the smallest leading numeric timestamp in the profile is used
- `window_time_ms=[a,b]` becomes `window_time_offset=[+xms,+yms]`
- leading timestamps in lines like `3646865581 captured...` become `+362ms captured...`

This keeps the profile easier for humans to read and makes the LLM input closer to the chart/dashboard time axis.

## Dashboard Parsing

`parse_dashboard_data()` extracts two series from `text_raw`:

1. frame timeline series from lines like:

```text
3646865581 captured, RTP TS: 2301000964, Frame Size: 5784, ...
```

Extracted fields:

- `ts_ms`
- `rtp_ts`
- `frame_size`

2. bitrate control series from lines like:

```text
3646867621 encoder_target_bps=587197
```

Extracted fields:

- `ts_ms`
- `target_bps`

It also finds:

- `stall_rtp_ts`
- `stall_frame_offset_ms`

All dashboard timestamps are normalized to `offset_ms` relative to the local window base time.

## HTTP API

### `GET /api/stalls`

Returns the global stall list for the top-left overview chart.

Fields:

- `idx`
- `stall_time`
- `gap_ms`
- `elapsed_s`

### `GET /api/profile/<idx>`

Returns formatted profile text (`text`), not raw text.

Used by the right-side `Profile` tab.

### `GET /api/dashboard/<idx>`

Returns parsed local dashboard data from `text_raw`.

Used by the left-bottom dashboard.

### `GET /api/ai_report/<idx>`

Streams LLM output as SSE.

Query params:

- `force=1`: ignore cache and regenerate
- `thinking=1`: enable reasoning/thinking stream

Cache is separated by:

- stall index
- cache version
- whether thinking is enabled

## LLM Prompt and Output

`build_prompt()` loads `prompt_simple.txt` and appends:

- formatted profile text
- `stall_time`
- `stall_gap_ms`
- strict JSON output schema

Current required JSON structure:

- `timeline`
- `network_judgment`
- `rate_control`
- `loss_recovery`
- `summary`

Each section contains:

- `headline`
- `confidence`
- `details`

The prompt explicitly requires:

- JSON only
- no markdown wrapper
- no extra explanation outside JSON

## Streaming Behavior

`stream_poe()` is the common streaming client for all configured providers.

It sends:

- `model`
- `stream=true`
- `messages`

For GLM:

- when `thinking` is disabled in the UI, request payload includes `thinking: {"type":"disabled"}`
- when `thinking` is enabled, reasoning chunks are passed through from `reasoning_content`

SSE payloads sent to frontend:

- `{ "token": "..." }`
- `{ "reasoning": "..." }`
- `{ "done": true }`
- `{ "error": "..." }`

## Cache

Cache directory:

- `ai_reports/`

Cache key includes:

- `CACHE_VERSION`
- `think1` or `think0`
- stall index
- stall timestamp

Cached fields:

- `markdown`
- `reasoning`
- `enable_thinking`

Replay behavior:

- cached reasoning is streamed first
- cached final text is streamed after that
- frontend code path stays identical to a live generation

## Frontend Layout

The page is a two-column layout.

### Left side

Left column is split vertically:

1. top ~1/3:
   global stall overview chart
2. bottom ~2/3:
   local dashboard for selected stall

Dashboard shows:

- `encoder_target_bps` line
- `frame_size` line
- a vertical marker for the selected stall frame

### Right side

Two tabs:

- `Profile`
- `AI Report`

`Profile` shows formatted relative-time text.

`AI Report` shows:

- toolbar
- `thinking` checkbox
- streaming thinking box when enabled
- final structured analysis cards

## Frontend AI Flow

When a stall is clicked:

1. load profile text
2. load dashboard data
3. reset AI state

When `生成 AI 报告` is clicked:

1. read `thinking` checkbox
2. call `/api/ai_report/<idx>?thinking=1` if checked
3. stream `reasoning` and `token`
4. render final JSON sections after completion

## Structured Rendering

`tryParseStructuredReport()` accepts:

- raw JSON string
- JSON inside a fenced ```json block

`renderStructuredReport()` renders 5 cards:

1. 帧时间线
2. 下层网络判断
3. 速率控制策略
4. 丢包恢复策略
5. 总结

Each card shows:

- section title
- highlighted headline
- confidence badge
- detailed explanation

## Current Design Choices

- raw numeric timestamps are preserved internally for parsing
- formatted relative timestamps are used for human reading and LLM input
- dashboard parsing is separated from profile formatting
- thinking is UI-controlled, not always-on
- cache is mode-aware
- helper formatting logic is extracted to a separate module to keep the main file manageable

## Known Extension Points

Natural next evolutions:

- add more dashboard series such as `DEC_GAP` or `Assemble2Decode`
- make the thinking box collapsible
- extract dashboard parsing into a separate module as well
- further split frontend HTML/JS from `stall_viewer.py`

# Design: `run_report_chunked`

**Date:** 2026-06-25
**Status:** Approved (pending spec review)

## Problem

ServiceTitan's reporting API times out / errors on long date ranges for
high-volume reports. A single report run covering many months can return
tens of thousands of rows and fail server-side before completing. The
established workaround (done manually) is to run the same report over many
short, disjoint date windows and stitch the results into one file.

This was validated by hand for report `726586383` (Marketing, "booked jobs
raw data") over Jan 2025 – May 2026: 17 monthly windows, 175,638 rows,
0 duplicate Job IDs, every chunk `hasMore=false`. The manual process is
mechanical and quota-expensive to repeat — it should be a single tool call.

## Goal

A `run_report_chunked` MCP tool that takes a report id, category, fixed
parameters, and a full date range, splits the range into disjoint windows,
runs the report per window (sequentially, respecting the reporting quota),
and concatenates the results into one file — with crash/failure resume so a
failed window never forces re-paying quota for already-pulled windows.

## Non-goals

- No aggregate-report support. This tool is for **detail/row-level** reports
  where concatenating disjoint windows is correct. Aggregate reports (totals,
  averages, distinct counts) cannot be stitched by concatenation and are out
  of scope. (Documented in the tool docstring as a "when NOT to use".)
- No deduplication. Windows are disjoint by construction, so detail rows
  appear in exactly one window. No dedup pass is performed.
- No parallelism. Reporting is quota-constrained (~3 req/min); windows run
  strictly sequentially, consistent with the project invariant.

## Chosen approach

**Orchestrate over the existing exporter** (Approach A of three considered).

The existing `run_report_to_file` already does, per single run: full
pagination, atomic `.partial` → `os.replace`, and metadata (row counts,
`hasMore`, live-shift `totalCount` warning). `run_report_chunked` reuses that
machinery once per window rather than re-implementing a page loop.

Refactor:
- Extract the current body of `run_report_to_file` into
  `async def _export_report_to_file(...) -> dict` returning the meta dict.
- `run_report_to_file` becomes a thin wrapper:
  `return json.dumps(await _export_report_to_file(...), indent=2, default=str)`.
  No behavior change; existing tests remain valid.

Approaches rejected:
- **B (standalone duplicate page loop):** copy-pasted pagination drifts from
  the original over time.
- **C (extract only the inner `while` loop):** less reuse — would re-implement
  path resolution, per-window metadata, and atomic replace.

## Components

### `servicetitan_mcp/report_export.py` (pure, unit-tested) — new helpers

- `iter_date_windows(from_date, to_date, chunk_by) -> list[tuple[date, date]]`
  - Splits an **inclusive** `[from_date, to_date]` range into disjoint,
    contiguous windows covering the whole range.
  - First and last windows may be partial — endpoints are honored exactly,
    not snapped to calendar boundaries.
  - `chunk_by`:
    - `month` — calendar-month boundaries (window ends on the last day of the
      month or `to_date`, whichever is earlier).
    - `quarter` — calendar-quarter boundaries (Jan–Mar, Apr–Jun, Jul–Sep,
      Oct–Dec).
    - `week` — 7-day stride starting at `from_date`.
    - `day` — one window per calendar day.
  - Raises `ValueError` if `to_date < from_date` or `chunk_by` is unknown.
  - Uses `datetime.date`; inputs are explicit, no wall-clock dependency.

- `concat_files(window_paths, final_path, fmt) -> tuple[list[int], list[str]]`
  - Concatenates per-window files into `final_path`, streaming line-by-line
    (no full in-memory load).
  - CSV: write the first file's header line, strip the single header line
    (first line) from every subsequent file.
  - JSONL: straight append of all lines.
  - Returns `(per_file_row_counts, columns)`: the data-row count of each input
    file (in order) and the column labels (CSV: first file's header row;
    JSONL: keys of the first object, or `[]` if empty). This is the single
    source of truth for per-window `row_count` and the aggregate `columns`,
    so the values are uniform whether a window was freshly fetched or resumed
    from cache.
  - Assumes window files are non-overlapping and share an identical schema
    (guaranteed by the cache signature, see below).

- Cache helpers:
  - `fixed_params_signature(report_id, category, parameters, from_param,
    to_param, chunk_by, fmt) -> str` — short stable hash (8 hex chars) of the
    fixed inputs.
  - `chunk_cache_dir(final_path, signature) -> Path` — returns
    `<final>.chunks-<sig8>/` sibling directory.
  - `window_filename(report_id, w_from, w_to, ext) -> str` —
    `report_<id>_<wfrom>_<wto>.<ext>` (reuses existing `_sanitize`).

### `servicetitan_mcp/server.py`

- `_export_report_to_file(...) -> dict` — extracted body of current
  `run_report_to_file` (returns meta dict).
- `run_report_to_file(...) -> str` — thin JSON wrapper over the above.
- `run_report_chunked(...) -> str` — new `@mcp.tool()`.

## `run_report_chunked` signature

```python
@mcp.tool()
async def run_report_chunked(
    tenant: str,
    report_id: int,
    category: str,
    from_date: str,                      # "YYYY-MM-DD", overall range start
    to_date: str,                        # "YYYY-MM-DD", overall range end (inclusive)
    parameters: dict | None = None,      # fixed params (DateType, BusinessUnitId, ...)
    chunk_by: str = "month",             # month | quarter | week | day
    format: str = "csv",
    output_path: str | None = None,
    output_dir: str | None = None,
    overwrite: bool = False,
    from_param: str = "From",            # name of the window-start param
    to_param: str = "To",                # name of the window-end param
    page_size: int = 5000,
) -> str:
```

- `parameters` holds everything that stays fixed across windows (e.g.
  `{"DateType": 2, "BusinessUnitId": [...], "IncludeAdjustmentInvoices": false}`).
- If `parameters` contains `from_param` or `to_param` (case-insensitive),
  return an error — those keys are owned by the splitter.
- Per window the tool injects `{from_param: w_from, to_param: w_to}` (dates as
  `YYYY-MM-DD` strings) into a copy of `parameters`.

## Data flow

1. **Validate.** Check `format` ∈ {csv, jsonl}; `parameters` is a dict; parse
   `from_date`/`to_date`; reject date params present in `parameters`; resolve
   the **final** output path via `resolve_output_path` (so `overwrite` /
   collision behavior is identical to `run_report_to_file`). Auto-name spans
   the full range: `report_<id>_<from>_<to>.<ext>`.
2. **Split.** `windows = iter_date_windows(from_date, to_date, chunk_by)`.
3. **Compute cache dir.** `sig = fixed_params_signature(...)`;
   `cache = chunk_cache_dir(final, sig)`; create it.
4. **Per window (sequential):**
   - Compute the deterministic per-window path inside `cache`.
   - If a complete file already exists → reuse it, record `source="cached"`.
   - Else call `_export_report_to_file(... output_path=<window path>,
     parameters=<fixed + window dates>, overwrite=True ...)`, record
     `source="fetched"` and bubble up any live-shift `warning` from its meta.
5. **Concatenate.** `counts, columns = concat_files(window_paths,
   final.partial, fmt)` then `os.replace(final.partial, final)`. Per-window
   `row_count` and the aggregate `columns` come from this return value
   (uniform for fetched and cached windows).
6. **Cleanup.** Remove the cache dir on full success.
7. **Return** aggregate metadata.

## Resume cache & stale-data guard

Per-window files live in `<final>.chunks-<sig8>/`.

The `<sig8>` signature is the safety net for a real footgun: auto-naming
encodes only `report_id` + range, so two runs differing only by `DateType`
(or business unit, etc.) would otherwise produce identically-named window
files and falsely resume **stale** data. Embedding a hash of the fixed inputs
in the cache-dir name guarantees a resume only reuses windows pulled with
identical fixed parameters.

Window-file completeness is guaranteed by `_export_report_to_file`'s existing
`.partial` → `os.replace` atomicity: only fully-written window files exist on
disk, so "file exists" reliably means "window complete".

On full success the cache dir is deleted. On a window failure it is left in
place so a re-run resumes cheaply.

## Error handling & return metadata

- A window failure (exception surfaced after the client's own retries) is
  **not swallowed** — it propagates, stopping the run. The error message names
  the failing window (`from`/`to`). Completed window files remain; a re-run
  with identical args skips them.
- Validation errors (bad format, date params in `parameters`, reversed range,
  path collision) return a friendly `"Error: ..."` string, matching
  `run_report_to_file`'s style.
- Aggregate JSON return:
  ```json
  {
    "file_path": "...",
    "format": "csv",
    "chunk_by": "month",
    "window_count": 17,
    "total_row_count": 175638,
    "columns": ["Job #", "..."],
    "windows": [
      {"from": "2025-01-01", "to": "2025-01-31", "row_count": 10280, "source": "fetched"},
      ...
    ],
    "warnings": ["..."]
  }
  ```
  `warnings[]` aggregates any per-window `totalCount`-vs-streamed mismatch
  warnings (live-shift detection) bubbled up from `_export_report_to_file`.

## Testing (TDD)

Pure-function tests carry the weight (no network):

- `iter_date_windows`:
  - month / quarter / week / day granularities.
  - partial first and last windows (range not aligned to boundaries).
  - single-day range; range entirely inside one month.
  - leap-year February (2024-02).
  - reversed range (`to < from`) → `ValueError`.
  - windows are contiguous and non-overlapping; union equals the input range.
- `concat_files`:
  - CSV header dedup across 3 files (one header in output, all rows present).
  - JSONL append (line count = sum of inputs).
  - empty window file contributes no rows.
  - rows with embedded commas / quotes / non-ASCII survive (CSV).
  - returned `(per_file_row_counts, columns)`: counts match each file's data
    rows in order; columns equal the header (CSV) / first-object keys (JSONL).
- `fixed_params_signature`: differing `parameters` → differing signature;
  identical inputs → identical signature.
- Refactor guard: `_export_report_to_file` via the existing fake-client
  pagination fixtures still produces the same shape; `run_report_to_file`
  wrapper still emits identical JSON.

Existing pagination regression tests (`tests/test_pagination.py`) must remain
green after the refactor.

## Invariants honored

- No silent default tenant (`_resolve(tenant)` as first step).
- Errors propagate; no swallowed exceptions in the handler path.
- Shared HTTP client / per-tenant limiters via `_resolve` and the reused
  exporter; no new `httpx.AsyncClient`.
- Reporting calls run strictly sequentially.
- No silent truncation: every window's status (`fetched`/`cached`) and a
  failing window are explicitly surfaced.

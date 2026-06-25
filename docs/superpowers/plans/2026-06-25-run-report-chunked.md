# run_report_chunked Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `run_report_chunked` MCP tool that runs a ServiceTitan report over many disjoint date windows and concatenates them into one file, with resume-on-failure.

**Architecture:** Reuse the existing single-run exporter. Refactor `run_report_to_file`'s body into `_export_report_to_file(...) -> dict`; the tool wraps it. `run_report_chunked` splits the range (pure `iter_date_windows`), runs `_export_report_to_file` once per window into a deterministic per-window cache file (skipping any that already exist), then concatenates (pure `concat_files`) into the final file. A signature-hashed cache dir prevents resuming stale windows across differing fixed params.

**Tech Stack:** Python 3.10+, `mcp[cli]` (FastMCP), `httpx`, `pytest`/`pytest-asyncio`. Stdlib `csv`, `json`, `hashlib`, `datetime`, `pathlib`.

**Design doc:** `docs/superpowers/specs/2026-06-25-run-report-chunked-design.md`

## Global Constraints

- `tenant: str` is always the first tool parameter; the docstring ends with the line `tenant: name of a configured ServiceTitan tenant (call list_tenants)`.
- Use `_resolve(tenant)` (never `_get_client` directly) in tool handlers.
- Never swallow exceptions in handlers — let them propagate to MCP. (Validation errors that mirror existing tools return `"Error: ..."` strings.)
- Reporting calls run strictly sequentially — never parallelize windows.
- No new `httpx.AsyncClient`; reuse the shared client via the exporter.
- No silent default tenant; no silent truncation — every window's status is surfaced.
- Pure helpers live in `servicetitan_mcp/report_export.py` (no async, no network) and are unit-tested in isolation.
- No linter/formatter configured — do not add one. Run tests with `pytest`.

---

### Task 1: Refactor `run_report_to_file` into `_export_report_to_file` + thin wrapper

Extract the working body of the existing tool into a reusable coroutine that **raises** on validation/IO errors and **returns the meta dict** on success. The public tool becomes a thin wrapper preserving its current `"Error: ..."` string behavior so existing tests stay green.

**Files:**
- Modify: `servicetitan_mcp/server.py` (the `run_report_to_file` function, currently ~lines 1958-2100)
- Test: `tests/test_report_to_file.py` (existing — must stay green), `tests/test_pagination.py` (existing — must stay green)

**Interfaces:**
- Produces: `async def _export_report_to_file(*, tenant: str, report_id: int, category: str, parameters: dict | None, fmt: str, output_path: str | None, output_dir: str | None, overwrite: bool, page_size: int) -> dict` — returns the meta dict (`file_path`, `format`, `row_count`, `pages_fetched`, `has_more`, `report_id`, `category`, `columns`, `total_count_reported`, `preview`, and optional `warning`). Raises `ValueError`/`FileExistsError` on bad format/parameters/collision and propagates fetch/IO errors after deleting the `.partial`.
- Produces: `async def run_report_to_file(...) -> str` (unchanged signature) — validates, calls `_export_report_to_file`, returns `json.dumps(meta, ...)`, converting `ValueError`/`FileExistsError` to `"Error: ..."` strings.

- [ ] **Step 1: Confirm the existing suite is green before refactoring**

Run: `pytest tests/test_report_to_file.py tests/test_pagination.py -q`
Expected: PASS (this is the regression baseline the refactor must preserve).

- [ ] **Step 2: Add `_export_report_to_file` above `run_report_to_file`**

Insert this coroutine immediately before the `@mcp.tool()` decorator of `run_report_to_file` in `servicetitan_mcp/server.py`:

```python
async def _export_report_to_file(
    *,
    tenant: str,
    report_id: int,
    category: str,
    parameters: dict | None,
    fmt: str,
    output_path: str | None,
    output_dir: str | None,
    overwrite: bool,
    page_size: int,
) -> dict:
    """Run a report and stream the COMPLETE result to one file; return meta dict.

    Shared core of `run_report_to_file` and `run_report_chunked`. Raises
    ValueError/FileExistsError on bad inputs/collision; propagates fetch/IO
    errors after removing the partial file. `fmt` must already be validated.
    """
    client = _resolve(tenant)
    final = resolve_output_path(
        output_path=output_path,
        output_dir=output_dir,
        report_id=report_id,
        parameters=parameters,
        fmt=fmt,
        overwrite=overwrite,
    )

    param_map = parameters or {}
    body: dict = {
        "parameters": [
            {"name": name, "value": value} for name, value in param_map.items()
        ]
    }

    tmp = final.with_name(final.name + ".partial")
    row_count = 0
    pages_fetched = 0
    columns: list[str] = []
    field_names: list[str] = []
    total_count_reported = None
    preview: list[dict] = []

    fh = open(tmp, "w", encoding="utf-8", newline="")
    try:
        writer = ReportFileWriter(fh, fmt)
        page = 1
        while True:
            path = (
                f"/reporting/v2/tenant/{client.tenant_id}/report-category/{category}"
                f"/reports/{report_id}/data?page={page}&pageSize={page_size}"
                f"&includeTotal=true"
            )
            data = await client.post(path, json_body=body)
            rows = data.get("data", [])

            if pages_fetched == 0:
                fields = data.get("fields", []) or []
                field_names = [f.get("name") for f in fields]
                columns = [(f.get("label") or f.get("name") or "") for f in fields]
                total_count_reported = data.get("totalCount")
                writer.write_header(fields)

            writer.write_rows(rows)
            row_count += len(rows)
            pages_fetched += 1
            for row in rows:
                if len(preview) >= 5:
                    break
                preview.append(dict(zip(field_names, row)))

            has_more = data.get("hasMore")
            if has_more is None:
                has_more = len(rows) >= page_size > 0
            if not has_more:
                break
            page += 1
        fh.close()
        os.replace(tmp, final)
    except BaseException:
        fh.close()
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise

    meta: dict = {
        "file_path": str(final),
        "format": fmt,
        "row_count": row_count,
        "pages_fetched": pages_fetched,
        "has_more": False,
        "report_id": report_id,
        "category": category,
        "columns": columns,
        "total_count_reported": total_count_reported,
        "preview": preview,
    }
    if total_count_reported is not None and total_count_reported != row_count:
        meta["warning"] = (
            f"Streamed {row_count} rows but the report reported "
            f"totalCount={total_count_reported}. The live dataset may have "
            f"shifted during pagination (point-in-time snapshot)."
        )
    return meta
```

- [ ] **Step 3: Replace the body of `run_report_to_file` with a thin wrapper**

Keep the existing signature and docstring of `run_report_to_file`. Replace everything after the docstring (the current lines 2002-2100) with:

```python
    if parameters is not None and not isinstance(parameters, dict):
        return "Error: 'parameters' must be a JSON object mapping report-param names to values."
    fmt = format.lower().strip()
    if fmt not in ("csv", "jsonl"):
        return "Error: 'format' must be 'csv' or 'jsonl'."

    try:
        meta = await _export_report_to_file(
            tenant=tenant,
            report_id=report_id,
            category=category,
            parameters=parameters,
            fmt=fmt,
            output_path=output_path,
            output_dir=output_dir,
            overwrite=overwrite,
            page_size=page_size,
        )
    except (ValueError, FileExistsError) as exc:
        return f"Error: {exc}"
    return json.dumps(meta, indent=2, default=str)
```

- [ ] **Step 4: Run the regression suite to confirm no behavior change**

Run: `pytest tests/test_report_to_file.py tests/test_pagination.py -q`
Expected: PASS — identical behavior; the wrapper still emits the same JSON and the same `"Error: ..."` strings.

- [ ] **Step 5: Commit**

```bash
git add servicetitan_mcp/server.py
git commit -m "refactor: extract _export_report_to_file from run_report_to_file

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: `iter_date_windows` pure helper

Split an inclusive date range into disjoint contiguous windows.

**Files:**
- Modify: `servicetitan_mcp/report_export.py`
- Test: `tests/test_report_chunked.py` (create)

**Interfaces:**
- Produces: `iter_date_windows(from_date, to_date, chunk_by) -> list[tuple[date, date]]`. Accepts `str` (`"YYYY-MM-DD"`) or `datetime.date` for the bounds. `chunk_by ∈ {"month","quarter","week","day"}`. Raises `ValueError` on reversed range or unknown `chunk_by`. Windows are contiguous, non-overlapping, cover `[from_date, to_date]`; first/last may be partial.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_report_chunked.py`:

```python
"""Tests for run_report_chunked helpers and the orchestrating tool."""

from __future__ import annotations

import json
from datetime import date

import httpx
import pytest

from servicetitan_mcp import auth, server
from servicetitan_mcp.client import RetryConfig, ServiceTitanClient, TokenBucket
from servicetitan_mcp.report_export import (
    chunk_cache_dir,
    concat_files,
    fixed_params_signature,
    iter_date_windows,
    window_filename,
)


def test_windows_month_partial_first_and_last():
    w = iter_date_windows("2025-01-15", "2025-03-10", "month")
    assert w == [
        (date(2025, 1, 15), date(2025, 1, 31)),
        (date(2025, 2, 1), date(2025, 2, 28)),
        (date(2025, 3, 1), date(2025, 3, 10)),
    ]


def test_windows_full_months():
    w = iter_date_windows("2025-01-01", "2025-03-31", "month")
    assert w == [
        (date(2025, 1, 1), date(2025, 1, 31)),
        (date(2025, 2, 1), date(2025, 2, 28)),
        (date(2025, 3, 1), date(2025, 3, 31)),
    ]


def test_windows_quarter():
    w = iter_date_windows("2025-02-10", "2025-08-15", "quarter")
    assert w == [
        (date(2025, 2, 10), date(2025, 3, 31)),
        (date(2025, 4, 1), date(2025, 6, 30)),
        (date(2025, 7, 1), date(2025, 8, 15)),
    ]


def test_windows_week_is_seven_day_stride():
    w = iter_date_windows("2025-01-01", "2025-01-16", "week")
    assert w == [
        (date(2025, 1, 1), date(2025, 1, 7)),
        (date(2025, 1, 8), date(2025, 1, 14)),
        (date(2025, 1, 15), date(2025, 1, 16)),
    ]


def test_windows_day():
    w = iter_date_windows("2025-01-01", "2025-01-03", "day")
    assert w == [
        (date(2025, 1, 1), date(2025, 1, 1)),
        (date(2025, 1, 2), date(2025, 1, 2)),
        (date(2025, 1, 3), date(2025, 1, 3)),
    ]


def test_windows_single_day_range():
    assert iter_date_windows("2025-05-05", "2025-05-05", "month") == [
        (date(2025, 5, 5), date(2025, 5, 5))
    ]


def test_windows_leap_february():
    w = iter_date_windows("2024-02-01", "2024-02-29", "month")
    assert w == [(date(2024, 2, 1), date(2024, 2, 29))]


def test_windows_contiguous_and_cover_range():
    w = iter_date_windows("2025-01-01", "2026-05-31", "month")
    assert w[0][0] == date(2025, 1, 1)
    assert w[-1][1] == date(2026, 5, 31)
    for (_, end), (nxt, _) in zip(w, w[1:]):
        assert (nxt - end).days == 1  # no gap, no overlap


def test_windows_reversed_range_raises():
    with pytest.raises(ValueError):
        iter_date_windows("2025-03-01", "2025-01-01", "month")


def test_windows_unknown_chunk_raises():
    with pytest.raises(ValueError):
        iter_date_windows("2025-01-01", "2025-02-01", "fortnight")
```

- [ ] **Step 2: Run the new window tests to verify they fail**

Run: `pytest tests/test_report_chunked.py -k windows -q`
Expected: FAIL with `ImportError` (helpers not defined yet).

- [ ] **Step 3: Implement `iter_date_windows` in `report_export.py`**

Add to the imports at the top of `servicetitan_mcp/report_export.py`:

```python
import hashlib
from datetime import date, timedelta
```

Append these functions to `servicetitan_mcp/report_export.py`:

```python
def _as_date(value) -> date:
    return value if isinstance(value, date) else date.fromisoformat(value)


def _last_of_month(d: date) -> date:
    if d.month == 12:
        return d.replace(day=31)
    return d.replace(month=d.month + 1, day=1) - timedelta(days=1)


def _quarter_end(d: date) -> date:
    end_month = ((d.month - 1) // 3) * 3 + 3  # 3, 6, 9, or 12
    return _last_of_month(d.replace(month=end_month, day=1))


def iter_date_windows(from_date, to_date, chunk_by: str) -> list[tuple[date, date]]:
    """Split inclusive [from_date, to_date] into disjoint contiguous windows.

    `chunk_by`: 'month'/'quarter' are calendar-aligned; 'week' is a 7-day
    stride from from_date; 'day' is one window per date. First/last windows may
    be partial (endpoints are honored exactly). Raises ValueError on a reversed
    range or unknown `chunk_by`.
    """
    start = _as_date(from_date)
    end = _as_date(to_date)
    if end < start:
        raise ValueError(f"to_date ({end}) is before from_date ({start}).")
    if chunk_by not in ("month", "quarter", "week", "day"):
        raise ValueError(
            f"chunk_by must be one of month, quarter, week, day; got {chunk_by!r}."
        )

    windows: list[tuple[date, date]] = []
    cur = start
    while cur <= end:
        if chunk_by == "month":
            w_end = _last_of_month(cur)
        elif chunk_by == "quarter":
            w_end = _quarter_end(cur)
        elif chunk_by == "week":
            w_end = cur + timedelta(days=6)
        else:  # day
            w_end = cur
        if w_end > end:
            w_end = end
        windows.append((cur, w_end))
        cur = w_end + timedelta(days=1)
    return windows
```

- [ ] **Step 4: Run the window tests to verify they pass**

Run: `pytest tests/test_report_chunked.py -k windows -q`
Expected: PASS (10 tests).

- [ ] **Step 5: Commit**

```bash
git add servicetitan_mcp/report_export.py tests/test_report_chunked.py
git commit -m "feat: add iter_date_windows date splitter

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: `concat_files` pure helper

Concatenate per-window files into one, returning per-file row counts and columns.

**Files:**
- Modify: `servicetitan_mcp/report_export.py`
- Test: `tests/test_report_chunked.py`

**Interfaces:**
- Produces: `concat_files(window_paths: list[str], final_path: str, fmt: str) -> tuple[list[int], list[str]]`. CSV: keeps the first file's header, strips it from the rest. JSONL: appends all non-blank lines. Returns `(per_file_row_counts, columns)` — `columns` is the CSV header row or the first JSONL object's keys (`[]` if empty). Raises `ValueError` on unknown `fmt`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_report_chunked.py`:

```python
def test_concat_csv_dedups_header_and_counts(tmp_path):
    a = tmp_path / "a.csv"
    b = tmp_path / "b.csv"
    a.write_text("ID,Name\n1,Acme\n2,Beta\n", encoding="utf-8")
    b.write_text("ID,Name\n3,Gamma\n", encoding="utf-8")
    out = tmp_path / "out.csv"
    counts, columns = concat_files([str(a), str(b)], str(out), "csv")
    assert counts == [2, 1]
    assert columns == ["ID", "Name"]
    text = out.read_text(encoding="utf-8")
    assert text.count("ID,Name") == 1  # one header only
    assert "1,Acme" in text and "3,Gamma" in text


def test_concat_csv_preserves_embedded_commas_and_unicode(tmp_path):
    a = tmp_path / "a.csv"
    a.write_text('ID,Note\n1,"Acme, Inc."\n2,Pet 🐶\n', encoding="utf-8")
    out = tmp_path / "out.csv"
    counts, columns = concat_files([str(a)], str(out), "csv")
    assert counts == [2]
    import csv as _csv

    rows = list(_csv.reader(out.open(encoding="utf-8")))
    assert rows[1] == ["1", "Acme, Inc."]
    assert rows[2] == ["2", "Pet 🐶"]


def test_concat_csv_empty_window_contributes_zero(tmp_path):
    a = tmp_path / "a.csv"
    b = tmp_path / "b.csv"
    a.write_text("ID\n1\n", encoding="utf-8")
    b.write_text("ID\n", encoding="utf-8")  # header only, no rows
    out = tmp_path / "out.csv"
    counts, columns = concat_files([str(a), str(b)], str(out), "csv")
    assert counts == [1, 0]
    assert columns == ["ID"]


def test_concat_jsonl_appends_and_counts(tmp_path):
    a = tmp_path / "a.jsonl"
    b = tmp_path / "b.jsonl"
    a.write_text('{"id": 1}\n{"id": 2}\n', encoding="utf-8")
    b.write_text('{"id": 3}\n', encoding="utf-8")
    out = tmp_path / "out.jsonl"
    counts, columns = concat_files([str(a), str(b)], str(out), "jsonl")
    assert counts == [2, 1]
    assert columns == ["id"]
    assert len(out.read_text(encoding="utf-8").splitlines()) == 3


def test_concat_unknown_format_raises(tmp_path):
    with pytest.raises(ValueError):
        concat_files([], str(tmp_path / "x"), "xml")
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_report_chunked.py -k concat -q`
Expected: FAIL with `ImportError` / `AttributeError` (function not defined).

- [ ] **Step 3: Implement `concat_files`**

Append to `servicetitan_mcp/report_export.py`:

```python
def concat_files(window_paths, final_path, fmt: str) -> tuple[list[int], list[str]]:
    """Concatenate per-window files into final_path.

    Returns (per_file_row_counts, columns). CSV keeps the first file's header
    and strips it from the rest; JSONL appends all non-blank lines. `columns`
    is the CSV header or the first JSONL object's keys ([] if empty).
    """
    if fmt not in _EXT:
        raise ValueError(f"format must be one of {sorted(_EXT)}, got {fmt!r}")

    counts: list[int] = []
    columns: list[str] = []

    if fmt == "csv":
        with open(final_path, "w", encoding="utf-8", newline="") as out:
            writer = csv.writer(out)
            for i, p in enumerate(window_paths):
                n = 0
                with open(p, encoding="utf-8", newline="") as f:
                    reader = csv.reader(f)
                    header = next(reader, None)
                    if i == 0 and header is not None:
                        writer.writerow(header)
                        columns = header
                    for row in reader:
                        writer.writerow(row)
                        n += 1
                counts.append(n)
    else:  # jsonl
        with open(final_path, "w", encoding="utf-8") as out:
            for i, p in enumerate(window_paths):
                n = 0
                with open(p, encoding="utf-8") as f:
                    for line in f:
                        if not line.strip():
                            continue
                        if i == 0 and not columns:
                            columns = list(json.loads(line).keys())
                        out.write(line if line.endswith("\n") else line + "\n")
                        n += 1
                counts.append(n)
    return counts, columns
```

- [ ] **Step 4: Run to verify they pass**

Run: `pytest tests/test_report_chunked.py -k concat -q`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add servicetitan_mcp/report_export.py tests/test_report_chunked.py
git commit -m "feat: add concat_files for stitching window exports

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Cache-signature + naming helpers

Deterministic per-window file naming and a signature that prevents resuming stale windows across differing fixed parameters.

**Files:**
- Modify: `servicetitan_mcp/report_export.py`
- Test: `tests/test_report_chunked.py`

**Interfaces:**
- Produces: `fixed_params_signature(report_id, category, parameters, from_param, to_param, chunk_by, fmt) -> str` (8 hex chars, stable across dict key order).
- Produces: `chunk_cache_dir(final_path, signature) -> Path` → sibling dir `<final>.chunks-<signature>`.
- Produces: `window_filename(report_id, w_from, w_to, ext) -> str` → `report_<id>_<wfrom>_<wto>.<ext>` (reuses existing `_sanitize`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_report_chunked.py`:

```python
def test_signature_is_stable_across_key_order():
    a = fixed_params_signature(9, "m", {"DateType": 2, "BU": [1, 2]}, "From", "To", "month", "csv")
    b = fixed_params_signature(9, "m", {"BU": [1, 2], "DateType": 2}, "From", "To", "month", "csv")
    assert a == b
    assert len(a) == 8


def test_signature_differs_on_changed_params():
    a = fixed_params_signature(9, "m", {"DateType": 2}, "From", "To", "month", "csv")
    b = fixed_params_signature(9, "m", {"DateType": 3}, "From", "To", "month", "csv")
    assert a != b


def test_chunk_cache_dir_is_sibling(tmp_path):
    final = tmp_path / "report_9_2025-01-01_2025-03-31.csv"
    d = chunk_cache_dir(str(final), "abc12345")
    assert d.parent == tmp_path
    assert d.name == "report_9_2025-01-01_2025-03-31.csv.chunks-abc12345"


def test_window_filename_sanitizes():
    assert window_filename(9, "2025-01-01", "2025-01-31", "csv") == (
        "report_9_2025-01-01_2025-01-31.csv"
    )
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_report_chunked.py -k "signature or cache_dir or window_filename" -q`
Expected: FAIL with `ImportError` (helpers not defined).

- [ ] **Step 3: Implement the helpers**

Append to `servicetitan_mcp/report_export.py`:

```python
def fixed_params_signature(
    report_id, category, parameters, from_param, to_param, chunk_by, fmt
) -> str:
    """Short stable hash of the inputs that must match for a resume to be safe."""
    payload = json.dumps(
        {
            "report_id": report_id,
            "category": category,
            "parameters": parameters or {},
            "from_param": from_param,
            "to_param": to_param,
            "chunk_by": chunk_by,
            "fmt": fmt,
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:8]


def chunk_cache_dir(final_path, signature: str) -> Path:
    """Sibling directory holding per-window files for a chunked run."""
    final = Path(final_path)
    return final.with_name(f"{final.name}.chunks-{signature}")


def window_filename(report_id, w_from, w_to, ext: str) -> str:
    """Deterministic per-window filename: report_<id>_<from>_<to>.<ext>."""
    return f"report_{report_id}_{_sanitize(w_from)}_{_sanitize(w_to)}.{ext}"
```

- [ ] **Step 4: Run to verify they pass**

Run: `pytest tests/test_report_chunked.py -k "signature or cache_dir or window_filename" -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add servicetitan_mcp/report_export.py tests/test_report_chunked.py
git commit -m "feat: add cache signature and window-naming helpers

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: `run_report_chunked` MCP tool

Wire the helpers and the exporter into the public tool, with resume and per-window failure context.

**Files:**
- Modify: `servicetitan_mcp/server.py`
- Test: `tests/test_report_chunked.py`

**Interfaces:**
- Consumes: `_export_report_to_file` (Task 1); `iter_date_windows`, `concat_files`, `fixed_params_signature`, `chunk_cache_dir`, `window_filename`, `resolve_output_path` (report_export).
- Produces: `async def run_report_chunked(tenant, report_id, category, from_date, to_date, parameters=None, chunk_by="month", format="csv", output_path=None, output_dir=None, overwrite=False, from_param="From", to_param="To", page_size=5000) -> str`. Returns aggregate JSON (`file_path`, `format`, `chunk_by`, `window_count`, `total_row_count`, `columns`, `windows[]`, optional `warnings[]`) or an `"Error: ..."` string for validation failures.

- [ ] **Step 1: Write the failing integration tests**

Append to `tests/test_report_chunked.py`:

```python
@pytest.fixture(autouse=True)
def _stub_token(monkeypatch):
    async def fake_get_token(*_a, **_kw):
        return "fake-token"

    monkeypatch.setattr(auth.token_manager, "get_token", fake_get_token)
    yield


def _wire_client(monkeypatch, handler) -> ServiceTitanClient:
    transport = httpx.MockTransport(handler)
    c = ServiceTitanClient(
        app_key="k",
        client_id="ci",
        client_secret="cs",
        tenant_id="123",
        transport=transport,
        retry=RetryConfig(max_retries=0, base_backoff=0.01),
        main_limiter=TokenBucket(rate=1000, capacity=1000),
        reporting_limiter=TokenBucket(rate=1000, capacity=1000),
    )
    monkeypatch.setattr(server, "_get_client", lambda _tenant: c)
    return c


_CFIELDS = [{"name": "id", "label": "ID", "dataType": "Number"}]


def _params_of(request: httpx.Request) -> dict:
    return {p["name"]: p["value"] for p in json.loads(request.content)["parameters"]}


async def test_chunked_three_months_concatenates(monkeypatch, tmp_path):
    def handler(request):
        params = _params_of(request)
        month = int(params["From"][5:7])
        return httpx.Response(
            200,
            json={
                "data": [[month]],
                "fields": _CFIELDS,
                "page": 1,
                "pageSize": 5000,
                "totalCount": 1,
                "hasMore": False,
            },
        )

    _wire_client(monkeypatch, handler)
    out = json.loads(
        await server.run_report_chunked(
            tenant="t",
            report_id=9,
            category="marketing",
            from_date="2025-01-01",
            to_date="2025-03-31",
            parameters={"DateType": 2},
            chunk_by="month",
            output_dir=str(tmp_path),
        )
    )
    assert out["window_count"] == 3
    assert out["total_row_count"] == 3
    assert [w["row_count"] for w in out["windows"]] == [1, 1, 1]
    assert all(w["source"] == "fetched" for w in out["windows"])
    lines = open(out["file_path"], encoding="utf-8").read().splitlines()
    assert lines[0] == "ID"
    assert sorted(lines[1:]) == ["1", "2", "3"]
    # cache dir removed on success
    assert [p for p in tmp_path.iterdir() if p.is_dir()] == []


async def test_chunked_resumes_existing_window(monkeypatch, tmp_path):
    called = []

    def handler(request):
        params = _params_of(request)
        called.append(params["From"])
        return httpx.Response(
            200,
            json={
                "data": [[int(params["From"][5:7])]],
                "fields": _CFIELDS,
                "page": 1,
                "pageSize": 5000,
                "totalCount": 1,
                "hasMore": False,
            },
        )

    _wire_client(monkeypatch, handler)
    # Pre-seed the January window file in the matching cache dir.
    final = tmp_path / "report_9_2025-01-01_2025-02-28.csv"
    sig = fixed_params_signature(9, "marketing", {"DateType": 2}, "From", "To", "month", "csv")
    cache = chunk_cache_dir(str(final), sig)
    cache.mkdir(parents=True, exist_ok=True)
    (cache / window_filename(9, "2025-01-01", "2025-01-31", "csv")).write_text(
        "ID\n99\n", encoding="utf-8"
    )

    out = json.loads(
        await server.run_report_chunked(
            tenant="t",
            report_id=9,
            category="marketing",
            from_date="2025-01-01",
            to_date="2025-02-28",
            parameters={"DateType": 2},
            chunk_by="month",
            output_dir=str(tmp_path),
        )
    )
    assert called == ["2025-02-01"]  # January was resumed, not refetched
    assert out["windows"][0]["source"] == "cached"
    assert out["windows"][0]["row_count"] == 1
    assert out["windows"][1]["source"] == "fetched"
    assert out["total_row_count"] == 2


async def test_chunked_rejects_date_param_in_parameters(monkeypatch, tmp_path):
    _wire_client(monkeypatch, lambda r: httpx.Response(200, json={"data": [], "fields": []}))
    out = await server.run_report_chunked(
        tenant="t",
        report_id=1,
        category="m",
        from_date="2025-01-01",
        to_date="2025-01-31",
        parameters={"from": "x"},  # case-insensitive clash with from_param
        output_dir=str(tmp_path),
    )
    assert out.startswith("Error:")
    assert "From" in out or "from" in out


async def test_chunked_reversed_range_errors(monkeypatch, tmp_path):
    _wire_client(monkeypatch, lambda r: httpx.Response(200, json={"data": [], "fields": []}))
    out = await server.run_report_chunked(
        tenant="t",
        report_id=1,
        category="m",
        from_date="2025-03-01",
        to_date="2025-01-01",
        output_dir=str(tmp_path),
    )
    assert out.startswith("Error:")


async def test_chunked_failed_window_keeps_completed(monkeypatch, tmp_path):
    def handler(request):
        params = _params_of(request)
        if params["From"] == "2025-02-01":
            return httpx.Response(500, json={"error": "boom"})
        return httpx.Response(
            200,
            json={
                "data": [[1]],
                "fields": _CFIELDS,
                "page": 1,
                "pageSize": 5000,
                "totalCount": 1,
                "hasMore": False,
            },
        )

    _wire_client(monkeypatch, handler)
    with pytest.raises(Exception):
        await server.run_report_chunked(
            tenant="t",
            report_id=9,
            category="m",
            from_date="2025-01-01",
            to_date="2025-02-28",
            parameters={"DateType": 2},
            chunk_by="month",
            output_dir=str(tmp_path),
        )
    # Final not written; January window survived for a cheap resume.
    assert not (tmp_path / "report_9_2025-01-01_2025-02-28.csv").exists()
    cache_dirs = [p for p in tmp_path.iterdir() if p.is_dir() and ".chunks-" in p.name]
    assert len(cache_dirs) == 1
    survived = [f.name for f in cache_dirs[0].iterdir()]
    assert window_filename(9, "2025-01-01", "2025-01-31", "csv") in survived
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_report_chunked.py -k chunked -q`
Expected: FAIL — `run_report_chunked` does not exist yet (`AttributeError`).

- [ ] **Step 3: Add imports and the tool to `server.py`**

Ensure the `report_export` import in `servicetitan_mcp/server.py` includes the new names. Find the existing import line (currently `from .report_export import ReportFileWriter, resolve_output_path`) and replace it with:

```python
from .report_export import (
    ReportFileWriter,
    chunk_cache_dir,
    concat_files,
    fixed_params_signature,
    iter_date_windows,
    resolve_output_path,
    window_filename,
)
```

Add the tool immediately after `run_report_to_file`:

```python
@mcp.tool()
async def run_report_chunked(
    tenant: str,
    report_id: int,
    category: str,
    from_date: str,
    to_date: str,
    parameters: dict | None = None,
    chunk_by: str = "month",
    format: str = "csv",
    output_path: str | None = None,
    output_dir: str | None = None,
    overwrite: bool = False,
    from_param: str = "From",
    to_param: str = "To",
    page_size: int = 5000,
) -> str:
    """Run a DETAIL report over many date windows and stitch them into one file.

    When to use: a report bombs out / times out over a long date range. This
    splits [from_date, to_date] into disjoint windows (default: calendar
    months), runs the report per window sequentially (respecting the reporting
    quota), and concatenates the rows into one file. Failed windows are
    resumable: completed windows are cached, so a re-run with identical args
    never re-pays quota for data already pulled.
    When NOT: aggregate/summary reports (totals, averages, distinct counts) —
    concatenating windows double-counts or mis-aggregates those. Only use for
    row-level detail reports. For a single short range, use run_report_to_file.

    Identify the report exactly like run_report_to_file (category, report_id,
    parameters; see its discovery flow). Put everything that stays FIXED across
    windows in `parameters` (e.g. {"DateType": 2, "BusinessUnitId": [...]}).
    The date range is owned by from_date/to_date and injected per window under
    `from_param`/`to_param` (default "From"/"To") — do NOT put those in
    `parameters`. `chunk_by`: month | quarter | week | day. `format`, output
    target, and overwrite behave as in run_report_to_file.

    tenant: name of a configured ServiceTitan tenant (call list_tenants)
    """
    if parameters is not None and not isinstance(parameters, dict):
        return "Error: 'parameters' must be a JSON object mapping report-param names to values."
    fmt = format.lower().strip()
    if fmt not in ("csv", "jsonl"):
        return "Error: 'format' must be 'csv' or 'jsonl'."
    if chunk_by not in ("month", "quarter", "week", "day"):
        return "Error: 'chunk_by' must be one of month, quarter, week, day."

    pm = parameters or {}
    lowered = {k.lower() for k in pm}
    if from_param.lower() in lowered or to_param.lower() in lowered:
        return (
            f"Error: '{from_param}'/'{to_param}' are managed per-window; remove "
            f"them from 'parameters' and pass the range via from_date/to_date."
        )

    try:
        windows = iter_date_windows(from_date, to_date, chunk_by)
    except ValueError as exc:
        return f"Error: {exc}"

    client = _resolve(tenant)  # validate tenant early (friendly error)
    _ = client

    ext = "csv" if fmt == "csv" else "jsonl"
    try:
        final = resolve_output_path(
            output_path=output_path,
            output_dir=output_dir,
            report_id=report_id,
            parameters={from_param: from_date, to_param: to_date},
            fmt=fmt,
            overwrite=overwrite,
        )
    except (ValueError, FileExistsError) as exc:
        return f"Error: {exc}"

    sig = fixed_params_signature(
        report_id, category, pm, from_param, to_param, chunk_by, fmt
    )
    cache = chunk_cache_dir(final, sig)
    cache.mkdir(parents=True, exist_ok=True)

    window_results: list[dict] = []
    window_paths: list = []
    for w_from, w_to in windows:
        wf, wt = w_from.isoformat(), w_to.isoformat()
        wpath = cache / window_filename(report_id, wf, wt, ext)
        window_paths.append(wpath)
        if wpath.exists():
            window_results.append({"from": wf, "to": wt, "source": "cached"})
            continue
        merged = dict(pm)
        merged[from_param] = wf
        merged[to_param] = wt
        try:
            meta = await _export_report_to_file(
                tenant=tenant,
                report_id=report_id,
                category=category,
                parameters=merged,
                fmt=fmt,
                output_path=str(wpath),
                output_dir=None,
                overwrite=True,
                page_size=page_size,
            )
        except Exception as exc:
            raise RuntimeError(
                f"run_report_chunked: window {wf}..{wt} failed; completed "
                f"windows are cached in {cache} — re-run with the same args to "
                f"resume. Cause: {exc}"
            ) from exc
        res = {"from": wf, "to": wt, "source": "fetched"}
        if meta.get("warning"):
            res["warning"] = meta["warning"]
        window_results.append(res)

    tmp_final = final.with_name(final.name + ".partial")
    try:
        counts, columns = concat_files(
            [str(p) for p in window_paths], str(tmp_final), fmt
        )
        os.replace(tmp_final, final)
    except BaseException:
        try:
            os.remove(tmp_final)
        except OSError:
            pass
        raise

    for res, n in zip(window_results, counts):
        res["row_count"] = n

    for p in window_paths:
        try:
            p.unlink()
        except OSError:
            pass
    try:
        cache.rmdir()
    except OSError:
        pass

    out: dict = {
        "file_path": str(final),
        "format": fmt,
        "chunk_by": chunk_by,
        "window_count": len(windows),
        "total_row_count": sum(counts),
        "columns": columns,
        "windows": window_results,
    }
    warnings = [r["warning"] for r in window_results if r.get("warning")]
    if warnings:
        out["warnings"] = warnings
    return json.dumps(out, indent=2, default=str)
```

- [ ] **Step 4: Run the chunked tests to verify they pass**

Run: `pytest tests/test_report_chunked.py -k chunked -q`
Expected: PASS (5 tests).

- [ ] **Step 5: Run the full suite**

Run: `pytest -q`
Expected: PASS (all pre-existing tests plus the new file).

- [ ] **Step 6: Commit**

```bash
git add servicetitan_mcp/server.py tests/test_report_chunked.py
git commit -m "feat: add run_report_chunked tool with resume

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Document the tool in the catalog

**Files:**
- Modify: `README.md` (the reporting section of the tool catalog)

**Interfaces:** none (docs only).

- [ ] **Step 1: Find the reporting tools in the catalog**

Run: `grep -n "run_report_to_file" README.md`
Expected: at least one line in the tool catalog table/section.

- [ ] **Step 2: Add a `run_report_chunked` entry next to `run_report_to_file`**

Match the surrounding format exactly (table row or bullet). Use this description text:

> `run_report_chunked` — Run a **detail** report over a long date range by splitting it into disjoint windows (month/quarter/week/day), running each sequentially, and stitching the rows into one file. Resumable: completed windows are cached so a re-run never re-pays quota. Not for aggregate reports.

- [ ] **Step 3: Verify the doc reads correctly**

Run: `grep -n "run_report_chunked" README.md`
Expected: the new entry is present and formatted like its neighbors.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: catalog run_report_chunked

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:**
- Refactor to `_export_report_to_file` + wrapper → Task 1. ✓
- `iter_date_windows` (month/quarter/week/day, partial ends, errors) → Task 2. ✓
- `concat_files` returning `(counts, columns)` → Task 3. ✓
- `fixed_params_signature` / `chunk_cache_dir` / `window_filename` → Task 4. ✓
- Tool signature, validation (date-param clash), data flow, resume, cache cleanup, per-window failure context, aggregate metadata + warnings → Task 5. ✓
- Docs → Task 6. ✓
- Invariants (no silent tenant, errors propagate, sequential, reuse client) honored in Task 5 code. ✓

**Placeholder scan:** No TBDs; every code/test step shows complete code. ✓

**Type consistency:** `_export_report_to_file` keyword args match between Task 1 definition and Task 5 call site. `concat_files` returns `(list[int], list[str])` in Task 3 and is unpacked as `counts, columns` in Task 5. `iter_date_windows` returns `list[tuple[date, date]]`, unpacked as `for w_from, w_to in windows` and `.isoformat()`-ed in Task 5. Helper names match the Task 5 import block. ✓

"""Tests for `run_report_to_file` and its serialization helpers.

Covers the bug this tool exists to fix: pulling a multi-page report to a file
without losing rows on the short final page. Unit-tests the CSV/JSONL writers
for null/bool/numeric/free-text fidelity, and integration-tests the
auto-paginating tool (multi-page, empty, collision, atomic partial-failure)
against a MockTransport, mirroring tests/test_reporting_tools.py.
"""

from __future__ import annotations

import csv
import io
import json

import httpx
import pytest

from servicetitan_mcp import auth, server
from servicetitan_mcp.client import RetryConfig, ServiceTitanClient, TokenBucket
from servicetitan_mcp.report_export import ReportFileWriter, resolve_output_path


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


_FIELDS = [
    {"name": "id", "label": "ID", "dataType": "Number"},
    {"name": "name", "label": "Customer Name", "dataType": "String"},
    {"name": "active", "label": "Active", "dataType": "Boolean"},
    {"name": "notes", "label": "Notes", "dataType": "String"},
]


# ── Unit: CSV serialization ───────────────────────────────────────────


def test_csv_writer_handles_null_bool_numeric_and_freetext():
    """csv module must quote commas/quotes/newlines and emit None as empty."""
    buf = io.StringIO()
    w = ReportFileWriter(buf, "csv")
    w.write_header(_FIELDS)
    w.write_rows(
        [
            [1, "Acme, Inc.", True, 'He said "hi"\nthen left'],
            [2, None, False, "trailing space "],
        ]
    )
    rows = list(csv.reader(io.StringIO(buf.getvalue())))

    assert rows[0] == ["ID", "Customer Name", "Active", "Notes"]
    assert rows[1] == ["1", "Acme, Inc.", "True", 'He said "hi"\nthen left']
    # None -> empty cell; trailing space preserved.
    assert rows[2] == ["2", "", "False", "trailing space "]


def test_csv_header_falls_back_to_name_when_label_missing():
    buf = io.StringIO()
    w = ReportFileWriter(buf, "csv")
    w.write_header([{"name": "raw_id", "label": "", "dataType": "Number"}])
    assert list(csv.reader(io.StringIO(buf.getvalue())))[0] == ["raw_id"]


# ── Unit: JSONL serialization ─────────────────────────────────────────


def test_jsonl_writer_keys_by_field_name_and_preserves_types():
    buf = io.StringIO()
    w = ReportFileWriter(buf, "jsonl")
    w.write_header(_FIELDS)
    w.write_rows([[1, "Acme", True, None], [2, "Beta", False, "x"]])

    lines = [json.loads(line) for line in buf.getvalue().splitlines()]
    assert lines[0] == {"id": 1, "name": "Acme", "active": True, "notes": None}
    assert lines[1] == {"id": 2, "name": "Beta", "active": False, "notes": "x"}


# ── Unit: path resolution ─────────────────────────────────────────────


def test_resolve_output_path_auto_names_with_from_to(tmp_path):
    final = resolve_output_path(
        output_path=None,
        output_dir=str(tmp_path),
        report_id=99,
        parameters={"From": "2024-01-01", "To": "2024-12-31"},
        fmt="csv",
        overwrite=False,
    )
    assert final.name == "report_99_2024-01-01_2024-12-31.csv"


def test_resolve_output_path_rejects_both_path_and_dir(tmp_path):
    with pytest.raises(ValueError):
        resolve_output_path(
            output_path=str(tmp_path / "a.csv"),
            output_dir=str(tmp_path),
            report_id=1,
            parameters=None,
            fmt="csv",
            overwrite=False,
        )


def test_resolve_output_path_collision_without_overwrite(tmp_path):
    existing = tmp_path / "report_1.csv"
    existing.write_text("old")
    with pytest.raises(FileExistsError):
        resolve_output_path(
            output_path=None,
            output_dir=str(tmp_path),
            report_id=1,
            parameters=None,
            fmt="csv",
            overwrite=False,
        )


# ── Integration: multi-page pull ──────────────────────────────────────


def _paged_handler(pages):
    """Return a MockTransport handler that serves `pages` keyed by ?page=N."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        page = int(request.url.params["page"])
        return httpx.Response(200, json=pages[page])

    return handler


async def test_multipage_pull_captures_short_final_page(monkeypatch, tmp_path):
    """The original bug dropped rows on the short final page — guard it."""
    pages = {
        1: {
            "page": 1,
            "pageSize": 2,
            "hasMore": True,
            "totalCount": 3,
            "fields": _FIELDS,
            "data": [[1, "A", True, "n1"], [2, "B", False, "n2"]],
        },
        2: {
            "page": 2,
            "pageSize": 2,
            "hasMore": False,
            "totalCount": 3,
            "fields": _FIELDS,
            "data": [[3, "C", True, "n3"]],  # short final page
        },
    }
    _wire_client(monkeypatch, _paged_handler(pages))

    out = await server.run_report_to_file(
        tenant="t",
        report_id=3749,
        category="operations",
        output_dir=str(tmp_path),
    )
    meta = json.loads(out)

    assert meta["row_count"] == 3
    assert meta["pages_fetched"] == 2
    assert meta["has_more"] is False
    assert meta["columns"] == ["ID", "Customer Name", "Active", "Notes"]
    assert "warning" not in meta

    rows = list(csv.reader(open(meta["file_path"], encoding="utf-8")))
    assert rows[0] == ["ID", "Customer Name", "Active", "Notes"]
    assert len(rows) == 4  # header + 3 data rows
    assert rows[-1] == ["3", "C", "True", "n3"]  # final page row present


async def test_drift_warning_when_total_disagrees(monkeypatch, tmp_path):
    pages = {
        1: {
            "page": 1,
            "pageSize": 5000,
            "hasMore": False,
            "totalCount": 99,  # claims 99 but only 1 row streamed
            "fields": _FIELDS,
            "data": [[1, "A", True, "n1"]],
        }
    }
    _wire_client(monkeypatch, _paged_handler(pages))

    meta = json.loads(
        await server.run_report_to_file(
            tenant="t", report_id=1, category="ops", output_dir=str(tmp_path)
        )
    )
    assert meta["row_count"] == 1
    assert "warning" in meta


async def test_empty_report_writes_header_only(monkeypatch, tmp_path):
    pages = {
        1: {
            "page": 1,
            "pageSize": 5000,
            "hasMore": False,
            "totalCount": 0,
            "fields": _FIELDS,
            "data": [],
        }
    }
    _wire_client(monkeypatch, _paged_handler(pages))

    meta = json.loads(
        await server.run_report_to_file(
            tenant="t", report_id=1, category="ops", output_dir=str(tmp_path)
        )
    )
    assert meta["row_count"] == 0
    rows = list(csv.reader(open(meta["file_path"], encoding="utf-8")))
    assert rows == [["ID", "Customer Name", "Active", "Notes"]]


async def test_jsonl_format_end_to_end(monkeypatch, tmp_path):
    pages = {
        1: {
            "page": 1,
            "pageSize": 5000,
            "hasMore": False,
            "fields": _FIELDS,
            "data": [[1, "A", True, None]],
        }
    }
    _wire_client(monkeypatch, _paged_handler(pages))

    meta = json.loads(
        await server.run_report_to_file(
            tenant="t",
            report_id=1,
            category="ops",
            format="jsonl",
            output_dir=str(tmp_path),
        )
    )
    assert meta["format"] == "jsonl"
    line = json.loads(open(meta["file_path"], encoding="utf-8").read().strip())
    assert line == {"id": 1, "name": "A", "active": True, "notes": None}


# ── Integration: failure handling ─────────────────────────────────────


async def test_partial_failure_leaves_no_file(monkeypatch, tmp_path):
    """A non-retryable error on page 2 must not leave a final or .partial file."""

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        if page == 1:
            return httpx.Response(
                200,
                json={
                    "page": 1,
                    "pageSize": 1,
                    "hasMore": True,
                    "fields": _FIELDS,
                    "data": [[1, "A", True, "n1"]],
                },
            )
        return httpx.Response(500, json={"error": "boom"})

    _wire_client(monkeypatch, handler)

    with pytest.raises(Exception):
        await server.run_report_to_file(
            tenant="t", report_id=7, category="ops", output_dir=str(tmp_path)
        )

    assert list(tmp_path.iterdir()) == []  # no final, no .partial


async def test_collision_errors_then_overwrite_succeeds(monkeypatch, tmp_path):
    pages = {
        1: {
            "page": 1,
            "pageSize": 5000,
            "hasMore": False,
            "fields": _FIELDS,
            "data": [[1, "A", True, "n1"]],
        }
    }
    _wire_client(monkeypatch, _paged_handler(pages))

    (tmp_path / "report_5.csv").write_text("old contents")

    blocked = await server.run_report_to_file(
        tenant="t", report_id=5, category="ops", output_dir=str(tmp_path)
    )
    assert blocked.startswith("Error:") and "already exists" in blocked

    ok = json.loads(
        await server.run_report_to_file(
            tenant="t",
            report_id=5,
            category="ops",
            output_dir=str(tmp_path),
            overwrite=True,
        )
    )
    assert ok["row_count"] == 1
    assert "old contents" not in open(ok["file_path"], encoding="utf-8").read()


# ── Schema lock ───────────────────────────────────────────────────────


async def test_run_report_to_file_schema_exposes_expected_params():
    tools = await server.mcp.list_tools()
    by_name = {t.name: t for t in tools}
    assert "run_report_to_file" in by_name
    props = by_name["run_report_to_file"].inputSchema["properties"]
    for expected in ("tenant", "report_id", "category", "format", "output_dir"):
        assert expected in props, f"missing param {expected}"

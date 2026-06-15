"""Helpers for `run_report_to_file`: output-path resolution and streaming
CSV/JSONL serialization of ServiceTitan report data.

These are deliberately pure (no async, no network) so the serialization and
filename logic can be unit-tested in isolation. The reporting `/data` response
returns rows as positional arrays aligned to `fields[]` (each field a dict with
`name`, `label`, `dataType`), so the writer keys/headers off `fields[]`.
"""

from __future__ import annotations

import csv
import json
import os
import re
from pathlib import Path

# Repo root = parent of the `servicetitan_mcp` package dir. Resolved from this
# file's location (not cwd) so the default export dir is stable regardless of
# where the server process was launched.
_DEFAULT_EXPORT_DIR = Path(__file__).resolve().parents[1] / "report_exports"

_EXT = {"csv": "csv", "jsonl": "jsonl"}


def _sanitize(value: object) -> str:
    """Make an arbitrary parameter value safe for use in a filename."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", str(value))


def _find_param(parameters: dict | None, key: str) -> object | None:
    """Case-insensitive lookup of a report parameter (e.g. 'from'/'to')."""
    if not parameters:
        return None
    for name, value in parameters.items():
        if name.lower() == key:
            return value
    return None


def _auto_filename(report_id: int, parameters: dict | None, ext: str) -> str:
    """Deterministic, descriptive default filename.

    `report_{id}_{from}_{to}.{ext}` when the report has from/to params,
    else `report_{id}.{ext}`. Deterministic (no timestamp) so collision
    detection is meaningful. Uses report_id rather than report name to avoid
    an extra reporting-quota call.
    """
    frm = _find_param(parameters, "from")
    to = _find_param(parameters, "to")
    if frm is not None and to is not None:
        base = f"report_{report_id}_{_sanitize(frm)}_{_sanitize(to)}"
    else:
        base = f"report_{report_id}"
    return f"{base}.{ext}"


def resolve_output_path(
    *,
    output_path: str | None,
    output_dir: str | None,
    report_id: int,
    parameters: dict | None,
    fmt: str,
    overwrite: bool,
) -> Path:
    """Resolve the final destination file, creating the parent dir.

    Precedence: explicit `output_path` (exact file) > explicit `output_dir`
    (auto-named) > `ST_OUTPUTS_DIR` env (auto-named) > default
    `<repo>/report_exports/` (auto-named). `output_path` and `output_dir`
    are mutually exclusive. Raises FileExistsError if the target exists and
    `overwrite` is False.
    """
    if fmt not in _EXT:
        raise ValueError(f"format must be one of {sorted(_EXT)}, got {fmt!r}")
    if output_path and output_dir:
        raise ValueError(
            "Pass at most one of `output_path` (exact file) or `output_dir` "
            "(directory); they are mutually exclusive."
        )

    ext = _EXT[fmt]
    if output_path:
        final = Path(output_path).expanduser()
    else:
        if output_dir:
            directory = Path(output_dir).expanduser()
        elif os.environ.get("ST_OUTPUTS_DIR"):
            directory = Path(os.environ["ST_OUTPUTS_DIR"]).expanduser()
        else:
            directory = _DEFAULT_EXPORT_DIR
        final = directory / _auto_filename(report_id, parameters, ext)

    final.parent.mkdir(parents=True, exist_ok=True)
    if final.exists() and not overwrite:
        raise FileExistsError(
            f"{final} already exists. Pass overwrite=True to replace it, or "
            f"choose a different output_path/output_dir."
        )
    return final


class ReportFileWriter:
    """Streaming writer for report rows in CSV or JSONL.

    Rows are positional arrays aligned to `fields[]`. Call `write_header(fields)`
    once (on the first page) before `write_rows`. CSV emits a header row of field
    labels; JSONL emits one field-name-keyed JSON object per row.
    """

    def __init__(self, fileobj, fmt: str):
        if fmt not in _EXT:
            raise ValueError(f"format must be one of {sorted(_EXT)}, got {fmt!r}")
        self.fmt = fmt
        self._fileobj = fileobj
        self._names: list = []
        self._csv = csv.writer(fileobj) if fmt == "csv" else None

    def write_header(self, fields: list[dict]) -> None:
        self._names = [f.get("name") for f in fields]
        if self.fmt == "csv":
            labels = [(f.get("label") or f.get("name") or "") for f in fields]
            self._csv.writerow(labels)

    def write_rows(self, rows: list[list]) -> None:
        if self.fmt == "csv":
            # Python's csv handles None->empty cell and quoting of commas,
            # quotes, and newlines; UTF-8 comes from the file's encoding.
            self._csv.writerows(rows)
        else:
            write = self._fileobj.write
            for row in rows:
                obj = dict(zip(self._names, row))
                write(json.dumps(obj, default=str))
                write("\n")

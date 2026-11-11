"""
Data export utilities.

Export collected API data to JSON, CSV, or Excel (xlsx) formats.
For CSV/xlsx, nested JSON is flattened into dot-notation columns.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from .logger import get_logger
from .models import CollectReport, CollectResult, ApiResponse

_log = get_logger("exporter")


def _flatten(data: Any, prefix: str = "") -> dict[str, Any]:
    """Flatten nested dict/list into dot-notation keys."""
    flat: dict[str, Any] = {}
    if isinstance(data, dict):
        for k, v in data.items():
            key = f"{prefix}.{k}" if prefix else k
            flat.update(_flatten(v, key))
    elif isinstance(data, list):
        for i, item in enumerate(data):
            key = f"{prefix}[{i}]" if prefix else f"[{i}]"
            flat.update(_flatten(item, key))
    else:
        flat[prefix] = data
    return flat


def export_json(
    data: list[dict] | dict | CollectReport,
    filepath: str | Path,
    indent: int = 2,
    ensure_ascii: bool = False,
) -> Path:
    """Export data to a JSON file."""
    path = Path(filepath)
    path.parent.mkdir(parents=True, exist_ok=True)

    if isinstance(data, CollectReport):
        payload = data.to_dict()
    elif isinstance(data, dict):
        payload = data
    else:
        payload = data

    path.write_text(
        json.dumps(payload, indent=indent, ensure_ascii=ensure_ascii, default=str),
        encoding="utf-8",
    )
    _log.info("exported JSON: %s (%d items)", path, len(payload) if isinstance(payload, list) else 1)
    return path


def export_csv(
    results: list[CollectResult] | list[dict],
    filepath: str | Path,
    flatten: bool = True,
) -> Path:
    """
    Export collected results to CSV.

    Each result's ``data`` field is flattened into columns.
    Non-data metadata (platform, endpoint, params, success, etc.) is
    always included as leading columns.
    """
    path = Path(filepath)
    path.parent.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, Any]] = []
    all_keys: set[str] = set()

    for item in results:
        if isinstance(item, CollectResult):
            row = {
                "platform": item.platform,
                "endpoint": item.endpoint,
                "success": item.success,
                "code": item.response.code if item.response else "",
                "duration_ms": round(item.duration_ms, 2),
                "cached": item.cached,
            }
            data = item.response.data if item.response and item.response.success else {}
            if flatten:
                flat_data = _flatten(data)
                row.update(flat_data)
                all_keys.update(flat_data.keys())
            else:
                row["data"] = json.dumps(data, ensure_ascii=False, default=str)
                all_keys.add("data")
            rows.append(row)
        elif isinstance(item, dict):
            rows.append(item)
            all_keys.update(item.keys())

    # Build column order: metadata first, then data columns
    meta_cols = ["platform", "endpoint", "success", "code", "duration_ms", "cached"]
    data_cols = sorted(all_keys - set(meta_cols))
    fieldnames = [c for c in meta_cols if c in (rows[0] if rows else {})] + data_cols

    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    _log.info("exported CSV: %s (%d rows)", path, len(rows))
    return path


def export_excel(
    results: list[CollectResult],
    filepath: str | Path,
    sheet_name: str = "data",
    flatten: bool = True,
) -> Path:
    """Export collected results to an Excel (.xlsx) file."""
    try:
        import openpyxl
    except ImportError:
        raise ImportError(
            "openpyxl is required for Excel export. Install with: pip install openpyxl"
        )

    path = Path(filepath)
    path.parent.mkdir(parents=True, exist_ok=True)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet_name

    rows: list[dict[str, Any]] = []
    all_keys: set[str] = set()

    for item in results:
        row = {
            "platform": item.platform,
            "endpoint": item.endpoint,
            "success": item.success,
            "code": item.response.code if item.response else "",
            "duration_ms": round(item.duration_ms, 2),
            "cached": item.cached,
        }
        data = item.response.data if item.response and item.response.success else {}
        if flatten:
            flat_data = _flatten(data)
            row.update(flat_data)
            all_keys.update(flat_data.keys())
        else:
            row["data"] = json.dumps(data, ensure_ascii=False, default=str)
            all_keys.add("data")
        rows.append(row)

    meta_cols = ["platform", "endpoint", "success", "code", "duration_ms", "cached"]
    data_cols = sorted(all_keys - set(meta_cols))
    fieldnames = meta_cols + data_cols

    ws.append(fieldnames)
    for row in rows:
        ws.append([row.get(c, "") for c in fieldnames])

    wb.save(path)
    _log.info("exported Excel: %s (%d rows)", path, len(rows))
    return path


def export(
    results: list[CollectResult],
    filepath: str | Path,
    fmt: str = "json",
    **kwargs: Any,
) -> Path:
    """
    Auto-dispatch export based on file extension or fmt argument.

    Args:
        results: List of CollectResult objects.

        fmt: 'json', 'csv', or 'xlsx'. If omitted, inferred from extension.
        **kwargs: Passed to the specific exporter.
    """
    path = Path(filepath)
    if fmt == "auto":
        ext = path.suffix.lower()
        if ext == ".csv":
            fmt = "csv"
        elif ext in (".xlsx", ".xls"):
            fmt = "xlsx"
        else:
            fmt = "json"

    if fmt == "json":
        report = CollectReport()
        for r in results:
            report.add(r)
        return export_json(report, path, **kwargs)
    elif fmt == "csv":
        return export_csv(results, path, **kwargs)
    elif fmt == "xlsx":
        return export_excel(results, path, **kwargs)
    else:
        raise ValueError(f"Unknown export format: {fmt}")

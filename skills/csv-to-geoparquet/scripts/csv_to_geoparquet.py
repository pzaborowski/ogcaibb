#!/usr/bin/env python3
"""Convert a CSVW-annotated tabular source to semantic GeoParquet.

Reads a GeoParquet header JSON document (CSVW-style `propertyUrl` per
column, plus a GeoParquet `geo` block) and a data file in whatever dialect
the source uses (comma CSV, whitespace-delimited, etc.), and writes a
GeoParquet file whose physical columns and file/field metadata come
entirely from the header — nothing about the source project is hardcoded.

If an MCP server command is supplied, the script asks that server to resolve
column meanings first. The header's inline JSON-LD `@context` is used as the
deterministic fallback, or as the source of truth when no MCP command is
supplied.

This script intentionally does not invent per-row geometry: it broadcasts a
single example geometry declared in the header's `geo.columns.<primary>`
block (`exampleGeometryGeoJSON`) to every row. That fits the common case of a
non-spatial tabular source being given a placeholder/representative
footprint. Deriving true per-row geometry (from lat/lon columns, WKT, etc.)
is a natural extension point, not implemented here — see the skill's
"Limitations" section before assuming it's covered.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import struct
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

pa: Any = None
pq: Any = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert a CSVW/GeoParquet-header-annotated tabular source to GeoParquet."
    )
    parser.add_argument("--csv", type=Path, required=True, help="Source data file.")
    parser.add_argument(
        "--header",
        type=Path,
        required=True,
        help="GeoParquet header JSON (CSVW propertyUrl per column + geo block + inline @context).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help=(
            "Output .geoparquet path. Defaults to header['fileName'] resolved next to "
            "--header, or <csv>.geoparquet if the header doesn't declare one."
        ),
    )
    parser.add_argument(
        "--delimiter",
        default=",",
        help="Single-character field delimiter. Use a space (' ') for whitespace-delimited sources.",
    )
    parser.add_argument("--quotechar", default='"', help="Quote character for the source dialect.")
    parser.add_argument(
        "--compression",
        default="zstd",
        help="Parquet compression codec (zstd, snappy, gzip, none, ...).",
    )
    parser.add_argument(
        "--column-order",
        choices=["strict", "by-name"],
        default="strict",
        help=(
            "'strict' requires the source header to match the declared parquetSchema order "
            "exactly (fails loudly on drift). 'by-name' maps columns by name regardless of order."
        ),
    )
    parser.add_argument(
        "--strict-crs",
        action="store_true",
        help=(
            "Fail instead of warn when geo.columns.<primary>.crs doesn't look like valid "
            "PROJJSON. Off by default because this is a common, easy-to-miss authoring mistake "
            "that otherwise only surfaces later as a reader-side crash (e.g. geopandas)."
        ),
    )
    parser.add_argument(
        "--client-name",
        default="csv-to-geoparquet-skill",
        help="Client name reported to an MCP resolver, if used.",
    )
    parser.add_argument(
        "--mcp-command",
        help=(
            "Optional MCP server command. The command is started over stdio and "
            "called with --mcp-tool to resolve column propertyUrl values."
        ),
    )
    parser.add_argument(
        "--mcp-tool",
        default="resolve_headers",
        help="MCP tool name used to resolve header meanings. Default: resolve_headers.",
    )
    parser.add_argument(
        "--require-mcp",
        action="store_true",
        help="Fail instead of falling back to the inline JSON-LD context if MCP fails.",
    )
    return parser.parse_args()


def require_pyarrow() -> None:
    global pa, pq
    try:
        import pyarrow as pyarrow
        import pyarrow.parquet as parquet
    except ImportError as exc:
        raise SystemExit(
            "This script requires pyarrow. Install it with: python3 -m pip install pyarrow"
        ) from exc
    pa = pyarrow
    pq = parquet


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


# --- JSON-LD context expansion -----------------------------------------------

def context_terms(context: Any) -> dict[str, Any]:
    if isinstance(context, list):
        terms: dict[str, Any] = {}
        for item in context:
            terms.update(context_terms(item))
        return terms
    if isinstance(context, dict):
        nested = context.get("@context")
        if nested is not None:
            merged = {k: v for k, v in context.items() if k != "@context"}
            merged.update(context_terms(nested))
            return merged
        return context
    return {}


def expand_curie_or_iri(value: str, context: Any) -> str:
    if re.match(r"^[a-z][a-z0-9+.-]*://", value):
        return value
    if value.startswith("_:"):
        return value

    terms = context_terms(context)
    if value in terms:
        mapped = terms[value]
        if isinstance(mapped, str):
            return expand_curie_or_iri(mapped, context)
        if isinstance(mapped, dict) and isinstance(mapped.get("@id"), str):
            return expand_curie_or_iri(mapped["@id"], context)

    prefix, sep, suffix = value.partition(":")
    if sep and prefix in terms and isinstance(terms[prefix], str):
        return terms[prefix] + suffix

    vocab = terms.get("@vocab")
    if isinstance(vocab, str):
        return vocab + value

    return value


def local_header_meanings(header: dict[str, Any]) -> dict[str, str]:
    context = header.get("@context", {})
    meanings = {}
    for column in header["parquetSchema"]:
        name = column["name"]
        property_url = column.get("propertyUrl")
        if property_url:
            meanings[name] = expand_curie_or_iri(property_url, context)
    return meanings


# --- optional MCP resolver ---------------------------------------------------

def read_mcp_message(stream: Any) -> dict[str, Any]:
    headers: dict[str, str] = {}
    while True:
        line = stream.readline()
        if not line:
            raise RuntimeError("MCP server closed stdout")
        line = line.decode("utf-8").strip()
        if not line:
            break
        key, _, value = line.partition(":")
        headers[key.lower()] = value.strip()

    length = int(headers.get("content-length", "0"))
    if length <= 0:
        raise RuntimeError("MCP response missing Content-Length")
    return json.loads(stream.read(length).decode("utf-8"))


def write_mcp_message(stream: Any, message: dict[str, Any]) -> None:
    payload = json.dumps(message, separators=(",", ":")).encode("utf-8")
    stream.write(f"Content-Length: {len(payload)}\r\n\r\n".encode("utf-8"))
    stream.write(payload)
    stream.flush()


def mcp_request(proc: subprocess.Popen[bytes], message: dict[str, Any]) -> dict[str, Any]:
    assert proc.stdin is not None
    assert proc.stdout is not None
    write_mcp_message(proc.stdin, message)
    while True:
        response = read_mcp_message(proc.stdout)
        if response.get("id") == message.get("id"):
            if "error" in response:
                raise RuntimeError(json.dumps(response["error"], indent=2))
            return response["result"]


def extract_mcp_mapping(result: dict[str, Any]) -> dict[str, str]:
    if "mapping" in result and isinstance(result["mapping"], dict):
        return {str(k): str(v) for k, v in result["mapping"].items()}

    content = result.get("content")
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("type") == "json":
                data = item.get("json")
                if isinstance(data, dict):
                    return extract_mcp_mapping(data)
            if isinstance(item, dict) and item.get("type") == "text":
                text = item.get("text", "")
                try:
                    data = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if isinstance(data, dict):
                    return extract_mcp_mapping(data)

    if all(isinstance(k, str) and isinstance(v, str) for k, v in result.items()):
        return {str(k): str(v) for k, v in result.items()}

    raise RuntimeError("MCP resolver did not return a column-to-URI mapping")


def mcp_header_meanings(
    command: str,
    tool_name: str,
    client_name: str,
    header: dict[str, Any],
) -> dict[str, str]:
    proc = subprocess.Popen(
        command,
        shell=True,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        mcp_request(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": client_name, "version": "1.0.0"},
                },
            },
        )
        assert proc.stdin is not None
        write_mcp_message(
            proc.stdin,
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        )
        result = mcp_request(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": tool_name,
                    "arguments": {
                        "columns": [
                            {
                                "name": column["name"],
                                "propertyUrl": column.get("propertyUrl"),
                                "type": column.get("type"),
                            }
                            for column in header["parquetSchema"]
                        ],
                        "context": header.get("@context", {}),
                    },
                },
            },
        )
        return extract_mcp_mapping(result)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()


def resolve_header_meanings(args: argparse.Namespace, header: dict[str, Any]) -> dict[str, str]:
    local = local_header_meanings(header)
    if not args.mcp_command:
        return local

    try:
        resolved = mcp_header_meanings(args.mcp_command, args.mcp_tool, args.client_name, header)
    except Exception as exc:
        if args.require_mcp:
            raise
        print(f"warning: MCP resolver failed, using inline context: {exc}", file=sys.stderr)
        return local

    missing = [column["name"] for column in header["parquetSchema"] if column["name"] not in resolved]
    if missing:
        if args.require_mcp:
            raise RuntimeError(f"MCP resolver missed columns: {', '.join(missing)}")
        print(
            "warning: MCP resolver missed columns, filling from inline context: " + ", ".join(missing),
            file=sys.stderr,
        )
        resolved = {**local, **resolved}

    return resolved


# --- Arrow type mapping (extend as new parquet logical types show up) -------

_ARROW_TYPE_FACTORIES: dict[str, Callable[[], Any]] = {
    "INT32": lambda: pa.int32(),
    "INT64": lambda: pa.int64(),
    "FLOAT": lambda: pa.float32(),
    "DOUBLE": lambda: pa.float64(),
    "BOOLEAN": lambda: pa.bool_(),
    "BYTE_ARRAY/WKB": lambda: pa.binary(),
    "BYTE_ARRAY/UTF8": lambda: pa.string(),
    "STRING": lambda: pa.string(),
}


def arrow_type(parquet_type: str) -> Any:
    normalized = parquet_type.upper()
    for prefix, factory in _ARROW_TYPE_FACTORIES.items():
        if normalized.startswith(prefix):
            return factory()
    raise ValueError(
        f"Unsupported parquet type: {parquet_type}. "
        f"Supported prefixes: {sorted(_ARROW_TYPE_FACTORIES)}"
    )


def parse_value(value: str, data_type: Any) -> Any:
    if value == "":
        return None
    if pa.types.is_string(data_type):
        return value
    if pa.types.is_boolean(data_type):
        return value.lower() == "true"
    if pa.types.is_integer(data_type):
        return int(value)
    if pa.types.is_floating(data_type):
        parsed = float(value)
        return None if math.isnan(parsed) else parsed
    return value


# --- source reading -----------------------------------------------------------

def read_rows(csv_path: Path, delimiter: str, quotechar: str) -> tuple[list[str], list[list[str]]]:
    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle, delimiter=delimiter, quotechar=quotechar, skipinitialspace=True)
        if delimiter == " ":
            # Whitespace-delimited sources commonly use variable run-length spacing;
            # collapse the empty cells that produces instead of erroring on them.
            rows = [[cell for cell in row if cell != ""] for row in reader if row]
        else:
            rows = [row for row in reader if row]
    if not rows:
        raise ValueError(f"Source file is empty: {csv_path}")
    return rows[0], rows[1:]


def _delimiter_mismatch_hint(csv_header: list[str], delimiter: str) -> str:
    # A common failure mode: the source is actually whitespace-delimited (or uses
    # some other delimiter) but --delimiter was left at its default, so the entire
    # header line comes back as a single cell containing every column name
    # separated by spaces. Surface that directly instead of a wall of missing/extra.
    if delimiter != " " and len(csv_header) == 1 and " " in csv_header[0]:
        return (
            " This looks like a delimiter mismatch: the whole header came back as one "
            f"column (current --delimiter is {delimiter!r}), which usually means the "
            "source uses a different delimiter — try --delimiter \" \" for whitespace-"
            "delimited sources."
        )
    return ""


def align_columns(
    csv_header: list[str], source_names: list[str], mode: str, delimiter: str
) -> dict[str, int]:
    if mode == "strict":
        if csv_header != source_names:
            missing = sorted(set(source_names) - set(csv_header))
            extra = sorted(set(csv_header) - set(source_names))
            hint = _delimiter_mismatch_hint(csv_header, delimiter)
            raise ValueError(
                "Source header does not match header parquetSchema (--column-order=strict). "
                f"Missing: {missing or 'none'}; extra: {extra or 'none'}.{hint}"
            )
        return {name: index for index, name in enumerate(csv_header)}

    if mode == "by-name":
        missing = sorted(set(source_names) - set(csv_header))
        if missing:
            hint = _delimiter_mismatch_hint(csv_header, delimiter)
            raise ValueError(f"Source file is missing declared columns: {missing}.{hint}")
        return {name: csv_header.index(name) for name in source_names}

    raise ValueError(f"Unknown --column-order mode: {mode}")


# --- geometry: GeoJSON -> WKB, dispatched by declared type ------------------

def _wkb_header(geom_type_code: int) -> bytes:
    return struct.pack("<BI", 1, geom_type_code)  # little-endian, uint32 geometry type


def _wkb_point(coordinates: list[float]) -> bytes:
    x, y = coordinates
    return _wkb_header(1) + struct.pack("<dd", float(x), float(y))


def _wkb_linestring(coordinates: list[list[float]]) -> bytes:
    body = struct.pack("<I", len(coordinates))
    for x, y in coordinates:
        body += struct.pack("<dd", float(x), float(y))
    return _wkb_header(2) + body


def _wkb_polygon(rings: list[list[list[float]]]) -> bytes:
    body = struct.pack("<I", len(rings))
    for ring in rings:
        body += struct.pack("<I", len(ring))
        for x, y in ring:
            body += struct.pack("<dd", float(x), float(y))
    return _wkb_header(3) + body


def _wkb_multipolygon(polygons: list[list[list[list[float]]]]) -> bytes:
    body = struct.pack("<I", len(polygons))
    for rings in polygons:
        body += _wkb_polygon(rings)  # each element is a full standalone WKB Polygon
    return _wkb_header(6) + body


_GEOJSON_TO_WKB: dict[str, Callable[[Any], bytes]] = {
    "Point": lambda geom: _wkb_point(geom["coordinates"]),
    "LineString": lambda geom: _wkb_linestring(geom["coordinates"]),
    "Polygon": lambda geom: _wkb_polygon(geom["coordinates"]),
    "MultiPolygon": lambda geom: _wkb_multipolygon(geom["coordinates"]),
}


def geometry_to_wkb(geometry: dict[str, Any]) -> bytes:
    geom_type = geometry.get("type")
    encoder = _GEOJSON_TO_WKB.get(geom_type)
    if encoder is None:
        raise ValueError(
            f"Unsupported geometry type for WKB encoding: {geom_type}. "
            f"Supported: {sorted(_GEOJSON_TO_WKB)}"
        )
    return encoder(geometry)


# --- CRS sanity check --------------------------------------------------------
# GeoParquet 1.1 requires `crs` to be either omitted (defaults to OGC:CRS84) or
# a full PROJJSON object. A common authoring mistake is a hand-written
# shorthand (e.g. {"type": "GeographicCRS", "name": "WGS 84", "id": {...}})
# that *looks* plausible but is missing required PROJJSON keys, so it parses
# as JSON but real readers (proj/GDAL-backed, e.g. geopandas) reject it at
# read time. This is a structural heuristic, not a full PROJJSON validator.

_PROJJSON_CRS_TYPES = {"GeographicCRS", "ProjectedCRS", "GeodeticCRS", "CompoundCRS", "VerticalCRS"}
_PROJJSON_REQUIRED_ANY_KEYS = ("datum", "datum_ensemble", "base_crs", "source_crs")


def looks_like_projjson(crs: Any) -> bool:
    if crs is None:
        return True  # omitted/null is valid: GeoParquet readers default to OGC:CRS84
    if isinstance(crs, str):
        return True  # e.g. "OGC:CRS84" — not our concern here
    if not isinstance(crs, dict):
        return False
    if crs.get("type") not in _PROJJSON_CRS_TYPES:
        return False
    return any(key in crs for key in _PROJJSON_REQUIRED_ANY_KEYS)


def check_crs(header: dict[str, Any], strict: bool) -> None:
    geo = header.get("geo", {})
    primary = geo.get("primary_column")
    column_meta = geo.get("columns", {}).get(primary, {})
    crs = column_meta.get("crs")
    if looks_like_projjson(crs):
        return
    message = (
        f"geo.columns.{primary}.crs does not look like valid PROJJSON "
        "(missing datum/datum_ensemble/base_crs). Real GeoParquet readers "
        "(e.g. geopandas, via proj) are likely to reject this file at read "
        "time. Generate a valid value with, e.g.: "
        "python3 -c \"from pyproj import CRS; import json; "
        "print(json.dumps(CRS.from_user_input('OGC:CRS84').to_json_dict()))\""
    )
    if strict:
        raise SystemExit(f"error: {message}")
    print(f"warning: {message}", file=sys.stderr)


# --- table construction -------------------------------------------------------

def build_table(
    csv_path: Path,
    header: dict[str, Any],
    meanings: dict[str, str],
    delimiter: str,
    quotechar: str,
    column_order: str,
) -> Any:
    csv_header, rows = read_rows(csv_path, delimiter, quotechar)
    columns = header["parquetSchema"]
    geometry_column = header["geo"]["primary_column"]
    source_names = [column["name"] for column in columns if column["name"] != geometry_column]

    csv_index = align_columns(csv_header, source_names, column_order, delimiter)

    geometry_meta = header["geo"]["columns"][geometry_column]
    example_geometry = geometry_meta.get("exampleGeometryGeoJSON")
    if example_geometry is None:
        raise ValueError(
            f"geo.columns.{geometry_column}.exampleGeometryGeoJSON is required — this converter "
            "broadcasts one example geometry to every row; it does not derive per-row geometry."
        )
    geometry_wkb = geometry_to_wkb(example_geometry)

    values: dict[str, list[Any]] = {column["name"]: [] for column in columns}
    for row_number, row in enumerate(rows, start=2):
        if len(row) != len(csv_header):
            raise ValueError(f"Row {row_number} has {len(row)} values, expected {len(csv_header)}")
        for column in columns:
            name = column["name"]
            data_type = arrow_type(column["type"])
            if name == geometry_column:
                values[name].append(geometry_wkb)
            else:
                values[name].append(parse_value(row[csv_index[name]], data_type))

    arrays = []
    fields = []
    for column in columns:
        name = column["name"]
        data_type = arrow_type(column["type"])
        nullable = bool(column.get("nullable", True))
        metadata = {b"csvw:propertyUrl": meanings[name].encode("utf-8")}
        if "description" in column:
            metadata[b"description"] = column["description"].encode("utf-8")
        fields.append(pa.field(name, data_type, nullable=nullable, metadata=metadata))
        arrays.append(pa.array(values[name], type=data_type))

    return pa.Table.from_arrays(arrays, schema=pa.schema(fields))


def csvw_metadata(header: dict[str, Any], meanings: dict[str, str]) -> dict[str, Any]:
    columns = []
    for column in header["parquetSchema"]:
        entry = {
            "name": column["name"],
            "datatype": column["type"],
            "propertyUrl": meanings[column["name"]],
        }
        if "description" in column:
            entry["description"] = column["description"]
        columns.append(entry)
    return {"@context": header.get("@context", {}), "tableSchema": {"columns": columns}}


def with_file_metadata(table: Any, header: dict[str, Any], meanings: dict[str, str]) -> Any:
    metadata = dict(table.schema.metadata or {})
    metadata.update(
        {
            b"geo": json.dumps(header["geo"], separators=(",", ":")).encode("utf-8"),
            b"csvw": json.dumps(csvw_metadata(header, meanings), indent=2).encode("utf-8"),
            b"geoparquet-header": json.dumps(header, indent=2).encode("utf-8"),
        }
    )
    return table.replace_schema_metadata(metadata)


def resolve_output_path(args: argparse.Namespace, header: dict[str, Any]) -> Path:
    if args.output is not None:
        return args.output
    file_name = header.get("fileName")
    if file_name:
        return args.header.parent / file_name
    return args.csv.with_suffix(".geoparquet")


def resolve_compression(value: str) -> str | None:
    return None if value.lower() == "none" else value


def main() -> int:
    args = parse_args()
    require_pyarrow()
    header = load_json(args.header)

    check_crs(header, args.strict_crs)

    meanings = resolve_header_meanings(args, header)
    table = build_table(
        args.csv, header, meanings, args.delimiter, args.quotechar, args.column_order
    )
    table = with_file_metadata(table, header, meanings)

    output_path = resolve_output_path(args, header)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, output_path, compression=resolve_compression(args.compression))

    print(f"wrote {output_path}")
    print(f"rows: {table.num_rows}")
    print(f"columns: {table.num_columns}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

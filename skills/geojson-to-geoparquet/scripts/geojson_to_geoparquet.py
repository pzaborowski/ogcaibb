#!/usr/bin/env python3
"""Convert a GeoJSON FeatureCollection to semantic GeoParquet.

Unlike a CSV source (which carries no native types or geometry and so needs
a hand-authored header describing both), GeoJSON already carries real
per-feature geometry and typed property values. This script derives as much
as possible directly from the data:

- the property schema (Arrow types, nullability) from a union scan of every
  feature's properties (and the top-level `id` member, if present)
- the geometry column's actual WKB encoding, per feature (not a broadcast
  placeholder)
- the GeoParquet `geo` block: geometry_types actually observed, bbox computed
  from the real coordinates, and a CRS that is always valid PROJJSON (never
  the "looks like JSON but isn't PROJJSON" shorthand that breaks real
  readers)

A GeoParquetHeader JSON document (matching the `GeoParquetHeader` schema
shape used across SEADOTS building blocks) can optionally be emitted
alongside the Parquet file with --write-header, ready to drop straight into
a building block's examples/ directory.

Semantic (`propertyUrl`) annotation is optional enrichment, not required for
the conversion to run: pass --context to attach one, matching the mechanism
used by the sibling csv-to-geoparquet skill.
"""

from __future__ import annotations

import argparse
import json
import re
import struct
import sys
from pathlib import Path
from typing import Any, Callable

pa: Any = None
pq: Any = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert a GeoJSON FeatureCollection to GeoParquet, deriving schema/geometry/CRS from the data."
    )
    parser.add_argument("--input", type=Path, required=True, help="Source GeoJSON file.")
    parser.add_argument(
        "--output", type=Path, default=None, help="Output .geoparquet path. Defaults to <input>.geoparquet."
    )
    parser.add_argument(
        "--write-header",
        type=Path,
        default=None,
        help="Also write a GeoParquetHeader JSON document (parquetSchema + geo block) to this path.",
    )
    parser.add_argument(
        "--source-href",
        default=None,
        help="Value for the emitted header's source.href. Defaults to --input's path as given.",
    )
    parser.add_argument("--geometry-column", default="geometry", help="Output column name for geometry.")
    parser.add_argument(
        "--id-field",
        default="auto",
        help=(
            "Output column name for each feature's top-level `id` member. 'auto' (default) includes it "
            "as 'id' if every feature has one; 'none' skips it even if present."
        ),
    )
    parser.add_argument(
        "--crs",
        default="OGC:CRS84",
        help=(
            "'OGC:CRS84' (default) or 'EPSG:4326' use a built-in valid PROJJSON constant. Any other value "
            "is treated as a path to a JSON file containing a full PROJJSON object, which is validated "
            "(not just parsed) before use."
        ),
    )
    parser.add_argument(
        "--compression",
        default="zstd",
        help="Parquet compression codec (zstd, snappy, gzip, none, ...).",
    )
    parser.add_argument(
        "--context",
        type=Path,
        default=None,
        help=(
            "Optional JSON-LD @context (or flat {property: IRI} dict) used to attach a resolved "
            "propertyUrl to each output field's Arrow metadata and to the emitted header, if any."
        ),
    )
    parser.add_argument(
        "--strict-types",
        action="store_true",
        help=(
            "Fail if a property key has incompatible JSON value types across features (e.g. sometimes "
            "a number, sometimes a string) instead of silently widening the column to string."
        ),
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


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_features(geojson: Any) -> list[dict[str, Any]]:
    if geojson.get("type") == "FeatureCollection":
        return geojson.get("features", [])
    if geojson.get("type") == "Feature":
        return [geojson]
    raise ValueError(f"Unsupported top-level GeoJSON type: {geojson.get('type')!r}")


# --- property type inference --------------------------------------------------

def infer_column(values: list[Any], key: str, strict: bool) -> tuple[Any, bool, list[Any]]:
    """Return (arrow_type, nullable, normalized_values) for one property key."""
    any_null = any(v is None for v in values)
    seen_bool = any(isinstance(v, bool) for v in values if v is not None)
    seen_int = any(isinstance(v, int) and not isinstance(v, bool) for v in values if v is not None)
    seen_float = any(isinstance(v, float) for v in values if v is not None)
    seen_str = any(isinstance(v, str) for v in values if v is not None)
    seen_other = any(
        v is not None and not isinstance(v, (bool, int, float, str)) for v in values
    )

    incompatible = seen_str and (seen_bool or seen_int or seen_float)
    incompatible = incompatible or (seen_other and (seen_bool or seen_int or seen_float or seen_str))
    incompatible = incompatible or (seen_bool and (seen_int or seen_float))
    if incompatible and strict:
        raise ValueError(
            f"Property {key!r} has incompatible value types across features "
            f"(bool={seen_bool}, int={seen_int}, float={seen_float}, str={seen_str}, other={seen_other}). "
            "Re-run without --strict-types to widen to string, or clean the source."
        )

    if seen_str or seen_other or (incompatible):
        arrow_type = pa.string()
        normalized = [None if v is None else (v if isinstance(v, str) else json.dumps(v)) for v in values]
        return arrow_type, any_null, normalized
    if seen_float:
        arrow_type = pa.float64()
        normalized = [None if v is None else float(v) for v in values]
        return arrow_type, any_null, normalized
    if seen_int:
        arrow_type = pa.int64()
        normalized = [None if v is None else int(v) for v in values]
        return arrow_type, any_null, normalized
    if seen_bool:
        arrow_type = pa.bool_()
        normalized = [None if v is None else bool(v) for v in values]
        return arrow_type, any_null, normalized
    # every value was null
    return pa.string(), True, [None] * len(values)


def parquet_type_name(arrow_type: Any) -> str:
    if pa.types.is_string(arrow_type):
        return "BYTE_ARRAY/UTF8"
    if pa.types.is_int64(arrow_type):
        return "INT64"
    if pa.types.is_float64(arrow_type):
        return "DOUBLE"
    if pa.types.is_boolean(arrow_type):
        return "BOOLEAN"
    if pa.types.is_binary(arrow_type):
        return "BYTE_ARRAY/WKB"
    raise ValueError(f"No parquet type name mapping for arrow type: {arrow_type}")


# --- geometry: GeoJSON -> WKB, dispatched by declared type ------------------

def _wkb_header(geom_type_code: int) -> bytes:
    return struct.pack("<BI", 1, geom_type_code)  # little-endian, uint32 geometry type


def _flatten_xy(coords: Any, out: list[tuple[float, float]]) -> None:
    if not coords:
        return
    if isinstance(coords[0], (int, float)):
        out.append((float(coords[0]), float(coords[1])))
    else:
        for item in coords:
            _flatten_xy(item, out)


def _wkb_point(coordinates: list[float]) -> bytes:
    x, y = coordinates[0], coordinates[1]
    return _wkb_header(1) + struct.pack("<dd", float(x), float(y))


def _wkb_linestring(coordinates: list[list[float]]) -> bytes:
    body = struct.pack("<I", len(coordinates))
    for point in coordinates:
        body += struct.pack("<dd", float(point[0]), float(point[1]))
    return _wkb_header(2) + body


def _wkb_polygon(rings: list[list[list[float]]]) -> bytes:
    body = struct.pack("<I", len(rings))
    for ring in rings:
        body += struct.pack("<I", len(ring))
        for point in ring:
            body += struct.pack("<dd", float(point[0]), float(point[1]))
    return _wkb_header(3) + body


def _wkb_multipoint(coordinates: list[list[float]]) -> bytes:
    body = struct.pack("<I", len(coordinates))
    for point in coordinates:
        body += _wkb_point(point)
    return _wkb_header(4) + body


def _wkb_multilinestring(lines: list[list[list[float]]]) -> bytes:
    body = struct.pack("<I", len(lines))
    for line in lines:
        body += _wkb_linestring(line)
    return _wkb_header(5) + body


def _wkb_multipolygon(polygons: list[list[list[list[float]]]]) -> bytes:
    body = struct.pack("<I", len(polygons))
    for rings in polygons:
        body += _wkb_polygon(rings)
    return _wkb_header(6) + body


_GEOJSON_TO_WKB: dict[str, Callable[[Any], bytes]] = {
    "Point": lambda geom: _wkb_point(geom["coordinates"]),
    "LineString": lambda geom: _wkb_linestring(geom["coordinates"]),
    "Polygon": lambda geom: _wkb_polygon(geom["coordinates"]),
    "MultiPoint": lambda geom: _wkb_multipoint(geom["coordinates"]),
    "MultiLineString": lambda geom: _wkb_multilinestring(geom["coordinates"]),
    "MultiPolygon": lambda geom: _wkb_multipolygon(geom["coordinates"]),
}


def geometry_to_wkb(geometry: dict[str, Any] | None) -> bytes | None:
    if geometry is None:
        return None
    geom_type = geometry.get("type")
    encoder = _GEOJSON_TO_WKB.get(geom_type)
    if encoder is None:
        raise ValueError(
            f"Unsupported geometry type for WKB encoding: {geom_type!r}. "
            f"Supported: {sorted(_GEOJSON_TO_WKB)} (GeometryCollection is not supported)."
        )
    return encoder(geometry)


# --- CRS: always valid PROJJSON, never a shorthand --------------------------
# These are the two PROJJSON constants covering the overwhelming majority of
# GeoJSON sources (which are, by RFC 7946 default, always lon/lat WGS84).
# Anything else must come from a file the caller generated with a real CRS
# library (e.g. pyproj) rather than being hand-typed — that's exactly the
# mistake this script exists to prevent.

_OGC_CRS84 = {
    "$schema": "https://proj.org/schemas/v0.7/projjson.schema.json",
    "type": "GeographicCRS",
    "name": "WGS 84 (CRS84)",
    "datum_ensemble": {
        "name": "World Geodetic System 1984 ensemble",
        "members": [
            {"name": "World Geodetic System 1984 (Transit)"},
            {"name": "World Geodetic System 1984 (G730)"},
            {"name": "World Geodetic System 1984 (G873)"},
            {"name": "World Geodetic System 1984 (G1150)"},
            {"name": "World Geodetic System 1984 (G1674)"},
            {"name": "World Geodetic System 1984 (G1762)"},
            {"name": "World Geodetic System 1984 (G2139)"},
            {"name": "World Geodetic System 1984 (G2296)"},
        ],
        "ellipsoid": {"name": "WGS 84", "semi_major_axis": 6378137, "inverse_flattening": 298.257223563},
        "accuracy": "2.0",
        "id": {"authority": "EPSG", "code": 6326},
    },
    "coordinate_system": {
        "subtype": "ellipsoidal",
        "axis": [
            {"name": "Geodetic longitude", "abbreviation": "Lon", "direction": "east", "unit": "degree"},
            {"name": "Geodetic latitude", "abbreviation": "Lat", "direction": "north", "unit": "degree"},
        ],
    },
    "scope": "Not known.",
    "area": "World.",
    "bbox": {"south_latitude": -90, "west_longitude": -180, "north_latitude": 90, "east_longitude": 180},
    "id": {"authority": "OGC", "code": "CRS84"},
}

_EPSG_4326 = {
    **{k: v for k, v in _OGC_CRS84.items() if k not in ("name", "coordinate_system", "id")},
    "name": "WGS 84",
    "coordinate_system": {
        "subtype": "ellipsoidal",
        "axis": [
            {"name": "Geodetic latitude", "abbreviation": "Lat", "direction": "north", "unit": "degree"},
            {"name": "Geodetic longitude", "abbreviation": "Lon", "direction": "east", "unit": "degree"},
        ],
    },
    "id": {"authority": "EPSG", "code": 4326},
}

_PROJJSON_CRS_TYPES = {"GeographicCRS", "ProjectedCRS", "GeodeticCRS", "CompoundCRS", "VerticalCRS"}
_PROJJSON_REQUIRED_ANY_KEYS = ("datum", "datum_ensemble", "base_crs", "source_crs")


def looks_like_projjson(crs: Any) -> bool:
    if not isinstance(crs, dict):
        return False
    if crs.get("type") not in _PROJJSON_CRS_TYPES:
        return False
    return any(key in crs for key in _PROJJSON_REQUIRED_ANY_KEYS)


def resolve_crs(value: str) -> dict[str, Any]:
    if value == "OGC:CRS84":
        return _OGC_CRS84
    if value == "EPSG:4326":
        return _EPSG_4326
    crs_path = Path(value)
    if not crs_path.is_file():
        raise SystemExit(
            f"--crs {value!r} is neither 'OGC:CRS84', 'EPSG:4326', nor an existing file path. "
            "Generate a PROJJSON file first, e.g.: python3 -c \"from pyproj import CRS; import json; "
            "print(json.dumps(CRS.from_user_input('EPSG:3857').to_json_dict()))\" > my-crs.json"
        )
    crs = load_json(crs_path)
    if not looks_like_projjson(crs):
        raise SystemExit(
            f"--crs file {value!r} does not look like valid PROJJSON (missing datum/datum_ensemble/"
            "base_crs/source_crs). This is the exact shape that parses as JSON but real readers "
            "(e.g. geopandas, via proj) reject at read time — regenerate it with a real CRS library."
        )
    return crs


# --- optional JSON-LD context expansion (shared shape with csv-to-geoparquet) -

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


def load_property_urls(context_path: Path | None, property_names: list[str]) -> dict[str, str]:
    if context_path is None:
        return {}
    raw = load_json(context_path)
    context = raw.get("@context", raw) if isinstance(raw, dict) else raw
    urls = {}
    for name in property_names:
        if name in context_terms(context):
            urls[name] = expand_curie_or_iri(name, context)
    return urls


# --- table construction -------------------------------------------------------

def build_table(
    features: list[dict[str, Any]],
    geometry_column: str,
    id_field: str,
    strict_types: bool,
) -> tuple[Any, dict[str, Any]]:
    include_id = False
    if id_field != "none":
        has_id = [("id" in feature and feature["id"] is not None) for feature in features]
        if id_field == "auto":
            include_id = all(has_id) and len(features) > 0
        else:
            include_id = any(has_id)
            if not all(has_id):
                print(f"warning: not every feature has an 'id'; {id_field!r} will be nullable", file=sys.stderr)

    property_keys: list[str] = []
    seen_keys = set()
    for feature in features:
        for key in feature.get("properties", {}) or {}:
            if key not in seen_keys:
                seen_keys.add(key)
                property_keys.append(key)

    arrays: dict[str, Any] = {}
    fields: list[Any] = []
    geo_stats = {"types": set(), "xmin": None, "ymin": None, "xmax": None, "ymax": None}

    id_name = "id" if id_field in ("auto", "none") else id_field
    if include_id:
        raw_id_values = [feature.get("id") for feature in features]
        arrow_type, nullable, normalized = infer_column(raw_id_values, id_name, strict_types)
        fields.append(pa.field(id_name, arrow_type, nullable=nullable))
        arrays[id_name] = pa.array(normalized, type=arrow_type)

    fields.append(pa.field(geometry_column, pa.binary(), nullable=True, metadata={b"propertyUrl": b"geo:hasGeometry"}))
    geometry_values = []
    for feature in features:
        geometry = feature.get("geometry")
        wkb = geometry_to_wkb(geometry)
        geometry_values.append(wkb)
        if geometry is not None:
            geo_stats["types"].add(geometry["type"])
            points: list[tuple[float, float]] = []
            _flatten_xy(geometry.get("coordinates"), points)
            for x, y in points:
                geo_stats["xmin"] = x if geo_stats["xmin"] is None else min(geo_stats["xmin"], x)
                geo_stats["ymin"] = y if geo_stats["ymin"] is None else min(geo_stats["ymin"], y)
                geo_stats["xmax"] = x if geo_stats["xmax"] is None else max(geo_stats["xmax"], x)
                geo_stats["ymax"] = y if geo_stats["ymax"] is None else max(geo_stats["ymax"], y)
    arrays[geometry_column] = pa.array(geometry_values, type=pa.binary())

    for key in property_keys:
        raw_values = [
            (feature.get("properties", {}) or {}).get(key) for feature in features
        ]
        arrow_type, nullable, normalized = infer_column(raw_values, key, strict_types)
        fields.append(pa.field(key, arrow_type, nullable=nullable))
        arrays[key] = pa.array(normalized, type=arrow_type)

    column_order = ([id_name] if include_id else []) + [geometry_column] + property_keys
    ordered_fields = [f for name in column_order for f in fields if f.name == name]
    table = pa.Table.from_arrays([arrays[name] for name in column_order], schema=pa.schema(ordered_fields))
    return table, geo_stats


def attach_property_urls(table: Any, property_urls: dict[str, str]) -> Any:
    if not property_urls:
        return table
    new_fields = []
    for field in table.schema:
        url = property_urls.get(field.name)
        if url:
            metadata = dict(field.metadata or {})
            metadata[b"propertyUrl"] = url.encode("utf-8")
            new_fields.append(field.with_metadata(metadata))
        else:
            new_fields.append(field)
    return table.cast(pa.schema(new_fields))


def build_geo_metadata(
    geometry_column: str, geo_stats: dict[str, Any], crs: dict[str, Any], version: str = "1.1.0"
) -> dict[str, Any]:
    bbox = None
    if geo_stats["xmin"] is not None:
        bbox = [geo_stats["xmin"], geo_stats["ymin"], geo_stats["xmax"], geo_stats["ymax"]]
    return {
        "version": version,
        "primary_column": geometry_column,
        "columns": {
            geometry_column: {
                "encoding": "WKB",
                "geometry_types": sorted(geo_stats["types"]),
                "crs": crs,
                **({"bbox": bbox} if bbox is not None else {}),
            }
        },
    }


def build_header_document(
    output_path: Path,
    source_href: str,
    table: Any,
    property_urls: dict[str, str],
    geo_metadata: dict[str, Any],
    row_count: int,
) -> dict[str, Any]:
    parquet_schema = []
    for field in table.schema:
        entry: dict[str, Any] = {
            "name": field.name,
            "type": parquet_type_name(field.type),
            "nullable": field.nullable,
        }
        url = property_urls.get(field.name) or (
            "geo:hasGeometry" if field.name == geo_metadata["primary_column"] else None
        )
        if url:
            entry["propertyUrl"] = url
        parquet_schema.append(entry)

    return {
        "fileName": output_path.name,
        "encoding": f"GeoParquet {geo_metadata['version']}; Parquet logical types; geometry encoded as WKB",
        "source": {
            "href": source_href,
            "sourceFormat": "application/geo+json",
            "rowCount": row_count,
            "columnCount": len(parquet_schema),
        },
        "parquetSchema": parquet_schema,
        "geo": geo_metadata,
    }


def main() -> int:
    args = parse_args()
    require_pyarrow()

    geojson = load_json(args.input)
    features = load_features(geojson)
    if not features:
        raise SystemExit("No features found in input GeoJSON.")

    crs = resolve_crs(args.crs)

    property_keys = sorted(
        {key for feature in features for key in (feature.get("properties", {}) or {})}
    )
    id_name_for_context = "id" if args.id_field in ("auto", "none") else args.id_field
    property_urls = load_property_urls(
        args.context, property_keys + [args.geometry_column, id_name_for_context]
    )

    table, geo_stats = build_table(features, args.geometry_column, args.id_field, args.strict_types)
    table = attach_property_urls(table, property_urls)

    output_path = args.output or args.input.with_suffix(".geoparquet")
    geo_metadata = build_geo_metadata(args.geometry_column, geo_stats, crs)

    table_metadata = dict(table.schema.metadata or {})
    table_metadata[b"geo"] = json.dumps(geo_metadata, separators=(",", ":")).encode("utf-8")
    table = table.replace_schema_metadata(table_metadata)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    compression = None if args.compression.lower() == "none" else args.compression
    pq.write_table(table, output_path, compression=compression)

    print(f"wrote {output_path}")
    print(f"rows: {table.num_rows}")
    print(f"columns: {table.num_columns}")
    print(f"geometry_types observed: {sorted(geo_stats['types'])}")

    if args.write_header:
        header = build_header_document(
            output_path,
            args.source_href or str(args.input),
            table,
            property_urls,
            geo_metadata,
            table.num_rows,
        )
        args.write_header.parent.mkdir(parents=True, exist_ok=True)
        with args.write_header.open("w", encoding="utf-8") as handle:
            json.dump(header, handle, indent=2)
            handle.write("\n")
        print(f"wrote header {args.write_header}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

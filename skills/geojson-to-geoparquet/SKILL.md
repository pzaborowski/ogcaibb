---
name: geojson-to-geoparquet
description: Use when converting a GeoJSON Feature or FeatureCollection into semantic GeoParquet. Unlike a CSV source, GeoJSON already carries real per-feature geometry and typed property values, so this skill derives as much as possible directly from the data instead of requiring a hand-authored header — property Arrow types and nullability (union-scanned across all features), per-feature WKB geometry (Point/LineString/Polygon/MultiPoint/MultiLineString/MultiPolygon), the actual geometry_types observed, a real bbox computed from the coordinates, and a CRS that is always valid PROJJSON (built-in OGC:CRS84/EPSG:4326 constants, or a caller-supplied PROJJSON file that gets validated, never a hand-typed shorthand). Can also emit a companion GeoParquetHeader JSON document (--write-header) ready to drop into a building block's examples/, and optionally attach propertyUrl semantic annotations from a JSON-LD @context (--context). Sibling to csv-to-geoparquet, which needs an external header because CSV carries no native types/geometry; this skill needs one only for optional semantic enrichment.
---

**Status**: stable · **Scope**: generic — no building-block coupling; reads whatever GeoJSON path it's given.<br>
_Sibling to [[csv-to-geoparquet]]; the roles invert because GeoJSON already carries real types and geometry that CSV doesn't. Built alongside the shared `geoparquet-header` building block that both the swedish-DT-simulations-output and harvest-timeseries-scen-m3 GeoParquet profiles depend on._

# GeoJSON to GeoParquet Conversion Skill

## Purpose

Convert a GeoJSON FeatureCollection into a GeoParquet file whose column set, types, and `geo` metadata are all *derived from the actual data* — not declared in an external header the way `csv-to-geoparquet` requires. Use this whenever you have real GeoJSON (not a tabular source with no native geometry) and need a spec-conformant GeoParquet file, optionally along with a GeoParquetHeader document to embed as a building block example.

## Activation

Use this skill when:

- converting a GeoJSON source into GeoParquet for a building block's example, and you want the `geo` block (geometry_types, bbox, CRS) computed from the real features rather than hand-typed
- producing a `GeoParquetHeader` JSON document to embed in a schema block's `examples/`, without writing one by hand first
- validating that a GeoJSON source's geometry types and property values are actually internally consistent (mixed types, mixed geometry types, null geometries) before committing to a GeoParquet profile for it

Do not use this skill for:

- CSV/whitespace-delimited tabular sources with no native geometry — use `csv-to-geoparquet`, which broadcasts a declared placeholder geometry instead
- GeometryCollection geometries — not supported (see Limitations)
- checking whether property names resolve to real vocabulary concepts — use `bblock-vocab-alignment`; `--context` here only attaches whatever mapping you already give it, it doesn't validate it against a vocabulary

## Required input

- `--input` — path to the source GeoJSON (`Feature` or `FeatureCollection`)

## Optional input (everything else is derived from the data)

| Flag | Default | Meaning |
|---|---|---|
| `--output` | `<input>.geoparquet` | output path |
| `--write-header` | none | also write a `GeoParquetHeader` JSON document to this path — `parquetSchema` and `geo` are built from the same derivation the Parquet file uses |
| `--source-href` | `str(--input)` | value for the emitted header's `source.href` (use e.g. `examples/foo.geojson` for a repo-relative path rather than an absolute one) |
| `--geometry-column` | `geometry` | output column name for geometry |
| `--id-field` | `auto` | `auto` includes each feature's top-level `id` as a column named `id` if every feature has one; `none` skips it even if present; any other value renames the output column |
| `--crs` | `OGC:CRS84` | `OGC:CRS84`/`EPSG:4326` use built-in valid PROJJSON constants; anything else is a path to a PROJJSON file, which is validated (not just parsed) before use |
| `--compression` | `zstd` | Parquet compression codec |
| `--context` | none | JSON-LD `@context` (or flat `{property: IRI}` dict) used to attach a resolved `propertyUrl` per field, including to `id` and the geometry column if the context defines them |
| `--strict-types` | off | fail if a property key has incompatible JSON value types across features (number vs. string, etc.) instead of silently widening to string |

## Process

### Phase 1 — load and scan

Load the GeoJSON, extract its feature list. Scan every feature's `properties` once to build the union of property keys (order of first appearance is preserved) — no property is assumed present in every feature.

### Phase 2 — infer property types

For each property key, look at every value across all features (including `None` for features missing the key) and infer the narrowest safe Arrow type: `bool_()` only if every non-null value is a bool; `int64()`/`float64()` for numeric (float wins if any value is a float); `string()` if any value is a string or a nested list/dict (serialized via `json.dumps`) or if types are otherwise incompatible. Under `--strict-types`, incompatible types raise instead of silently widening.

### Phase 3 — encode geometry per feature

Each feature's actual geometry is encoded to WKB individually — not broadcast from a single example the way `csv-to-geoparquet` does, because GeoJSON has real per-feature geometry to use. `Point`, `LineString`, `Polygon`, `MultiPoint`, `MultiLineString`, `MultiPolygon` are supported; `null` geometry is preserved as a null WKB value. While encoding, `geo.columns.<primary>.geometry_types` is built from the *actual* distinct types observed and the bbox is computed from the *actual* coordinates — neither is a guess or a copy of a declared value.

### Phase 4 — resolve CRS

Never emit a hand-typed CRS shorthand. `OGC:CRS84` and `EPSG:4326` resolve to built-in, complete PROJJSON constants. Any other `--crs` value is treated as a path to a file the caller generated with a real CRS library; it's checked against the same structural heuristic as `csv-to-geoparquet`'s `looks_like_projjson` (must claim a CRS type and have `datum`/`datum_ensemble`/`base_crs`/`source_crs`) and rejected with a clear message if it doesn't look valid — this is a hard failure here, not a warning, because there's no legacy header to be lenient about.

### Phase 5 — optional semantic enrichment

If `--context` is given, resolve every property key (plus the geometry column and the id column, under whatever name they end up with) against it the same way `csv-to-geoparquet` expands compact IRIs, and attach the result as `propertyUrl` Arrow field metadata. Without `--context`, the file is still fully valid GeoParquet — this is enrichment, not a requirement.

### Phase 6 — write

Write the Arrow table with the given `--compression`, attaching `geo` (the derived GeoParquet metadata block) as file-level metadata. If `--write-header` is given, also emit a `GeoParquetHeader` JSON document (`fileName`, `encoding`, `source.{href,sourceFormat,rowCount,columnCount}`, `parquetSchema[]`, `geo`) built from the same table and geo-stats — ready to use as a building block example alongside `schema.yaml`'s `GeoParquetHeader` definition (see the `geoparquet-header` building block).

## Script

`scripts/geojson_to_geoparquet.py` — standalone, `pyarrow`-only dependency. Run it directly.

```bash
python3 skills/geojson-to-geoparquet/scripts/geojson_to_geoparquet.py --help
```

## Command line usage

```bash
SCRIPT=skills/geojson-to-geoparquet/scripts/geojson_to_geoparquet.py

# Minimal: everything derived from the data, default OGC:CRS84
python3 "$SCRIPT" --input features.geojson

# Also emit a GeoParquetHeader document for a building block example, with a repo-relative source href
python3 "$SCRIPT" --input examples/source.geojson --write-header examples/source-header.json --source-href examples/source.geojson

# Attach propertyUrl annotations from an existing block's context.jsonld
python3 "$SCRIPT" --input examples/source.geojson --context context.jsonld

# Fail loudly on inconsistent property types instead of widening to string
python3 "$SCRIPT" --input features.geojson --strict-types
```

## Limitations

- **No `GeometryCollection` support.** The six core single/multi geometry types are supported; a `GeometryCollection` feature raises. Adding it would mean encoding heterogeneous nested WKB geometries — a real feature addition, not a config knob.
- **Property type inference is per-key, across the whole file.** A property that's an integer in most features and a free-text string in one becomes a string column for all of them (or raises under `--strict-types`) — there's no per-feature-group type splitting.
- **CRS validation is the same structural heuristic** `csv-to-geoparquet` uses, not a full PROJJSON validator: it catches "missing required keys," not "well-formed but wrong."
- **`--context` only attaches what you give it.** It doesn't check the mapping against a vocabulary — run `bblock-vocab-alignment` separately for that.

## Validate the output

```python
import geopandas as gpd
gdf = gpd.read_parquet(output_path)   # raises CRSError if the crs metadata is malformed
print(gdf.shape, gdf.crs, gdf.geom_type.unique())
```

If `--write-header` was used, the emitted document should validate against the `geoparquet-header` building block's `GeoParquetHeader` schema (or a dataset-specific block's `$ref` to it):

```python
import json, jsonschema, yaml
schema_doc = yaml.safe_load(open(".../geoparquet-header/schema.yaml"))
jsonschema.validate(json.load(open("header.json")), schema_doc)
```

## Interactions with other skills

- Sibling to `csv-to-geoparquet` — same CRS-safety philosophy and `propertyUrl`/context-expansion mechanism, inverted responsibility (derive vs. declare).
- Depends conceptually on the `geoparquet-header` building block's schema shape when `--write-header` output is meant to validate against it.
- Run `bblock-vocab-alignment` on the emitted header's `propertyUrl`s (or on `--context` before using it) if they haven't been checked against a vocabulary bblock yet.
- Compose with `context-completeness-checker` if the source block also renders JSON-LD elsewhere and needs full `@context` completeness checked, not just the flat property-to-IRI resolution this skill does.

## References

- GeoParquet 1.1.0 spec — https://geoparquet.org/releases/v1.1.0/
- GeoJSON (RFC 7946) — https://datatracker.ietf.org/doc/html/rfc7946
- PROJJSON schema — https://proj.org/en/stable/schemas/index.html
- WKB (Well-Known Binary) geometry encoding — https://libgeos.org/specifications/wkb/

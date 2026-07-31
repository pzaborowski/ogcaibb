---
name: csv-to-geoparquet
description: Use when converting a tabular source (CSV, whitespace-delimited, or any single-character-delimited dialect) into semantic GeoParquet, driven entirely by a GeoParquet header JSON document that declares the parquetSchema, CSVW propertyUrl per column, an inline JSON-LD @context, and a geo block. No project-specific defaults are hardcoded — delimiter, column order, compression, output path, and MCP client identity are all CLI parameters. Broadcasts a single example geometry declared in the header to every row (Point/LineString/Polygon/MultiPolygon supported); does not derive per-row geometry from lat/lon or WKT columns. Includes a CRS sanity check that catches the common "looks like JSON, isn't valid PROJJSON" authoring mistake before a real reader (e.g. geopandas) rejects the file. Generalized from a one-off converter written for the swedish-DT-simulations-output building block; validated against both that project and a synthetic unrelated dataset.
---

**Status**: stable · **Scope**: generic — no building-block coupling; reads whatever header/CSV path it's given.<br>
_Generalized from `swedish-DT-simulations-output/examples/convert_csv_to_geoparquet.py`; see [[bblock-vocab-alignment]] for the vocabulary-alignment pass that produced the header this script consumes._

# CSV to GeoParquet Conversion Skill

## Purpose

Convert a tabular data source into a GeoParquet file whose column set, types, semantic (`propertyUrl`) annotations, and GeoParquet `geo` block all come from a single header JSON document — not from anything hardcoded about a particular project. Use this after a data-source building block's header/schema has already been vocabulary-aligned (see `bblock-vocab-alignment`) and you need to prove the declared header actually produces a working, spec-conformant GeoParquet file.

## Activation

Use this skill when:

- a building block ships a `*-header.json` (CSVW `propertyUrl` per column + GeoParquet `geo` block) and a companion CSV/whitespace-delimited example, and you need to actually produce the GeoParquet file it describes
- validating that a header's declared schema, column order, and geometry block are internally consistent and produce a file real GeoParquet readers can open
- converting a new tabular source into GeoParquet for the first time, where a header document already exists or is being authored alongside it

Do not use this skill for:

- deriving per-row geometry from non-geometry columns (lat/lon pairs, WKT strings) — not implemented; see Limitations
- JSON-LD `@context` completeness auditing on the header itself — use `context-completeness-checker`
- checking whether the header's `propertyUrl` values resolve to real vocabulary concepts — use `bblock-vocab-alignment` first; this skill trusts the header's `@context` as given

## Required input

- `csv` — path to the source data file
- `header` — path to the GeoParquet header JSON (must declare `parquetSchema`, `geo.primary_column`, `geo.columns.<primary>.exampleGeometryGeoJSON`, and an inline `@context` or rely on an MCP resolver)

## Optional input (all CLI flags — nothing here is hardcoded per project)

| Flag | Default | Meaning |
|---|---|---|
| `--output` | `header['fileName']` resolved next to `--header`, else `<csv>.geoparquet` | output path |
| `--delimiter` | `,` | single-character field delimiter; use `" "` for whitespace-delimited sources (variable run-length spacing is collapsed automatically) |
| `--quotechar` | `"` | quote character for the source dialect |
| `--compression` | `zstd` | Parquet compression codec (`snappy`, `gzip`, `none`, ...) |
| `--column-order` | `strict` | `strict` requires the source header to match `parquetSchema` order exactly; `by-name` maps by column name regardless of order |
| `--strict-crs` | off (warn only) | fail instead of warn when `geo.columns.<primary>.crs` doesn't look like valid PROJJSON |
| `--client-name` | `csv-to-geoparquet-skill` | identity reported to an MCP resolver, if used |
| `--mcp-command` / `--mcp-tool` / `--require-mcp` | none / `resolve_headers` / off | optional MCP server to resolve column meanings instead of the header's inline context |

## Process

### Phase 1 — load and sanity-check the header

Load the header JSON. Run the **CRS check** immediately, before doing any conversion work: if `geo.columns.<primary_column>.crs` is present and is a dict claiming to be a `GeographicCRS`/`ProjectedCRS`/etc. but lacks any of `datum`, `datum_ensemble`, `base_crs`, `source_crs`, warn (or fail under `--strict-crs`). This exact failure mode — a hand-written CRS shorthand that parses as JSON but isn't valid PROJJSON — silently produces files that `geopandas.read_parquet()` rejects at read time with a `CRSError`; catching it here is cheap and the fix is one line (`pyproj.CRS.from_user_input(...).to_json_dict()`).

### Phase 2 — resolve column meanings

Expand every column's `propertyUrl` through the header's inline `@context` (walks `@context` arrays/nesting, resolves compact IRIs and `@vocab` fallback). If `--mcp-command` is given, ask that MCP server first and fall back to the inline context for anything it misses (or fail under `--require-mcp`).

### Phase 3 — read the source and align columns

Parse the source with the given `--delimiter`/`--quotechar`. Under `--column-order strict` (default), the source header must match `parquetSchema`'s declared order exactly (minus the geometry column) — this is a deliberate strict check, not silently reordering data. Under `by-name`, columns are located by name so source column order doesn't have to match the header.

**Common mistake:** running against a whitespace-delimited source without `--delimiter " "` (the default is `,`). With no commas present, the entire header line parses as a single cell, so `align_columns` sees one giant "extra" column and every real column as "missing". The error message detects this specific pattern (one cell containing spaces) and appends a hint to try a different `--delimiter` instead of just dumping the raw missing/extra lists.

### Phase 4 — build geometry

Take `geo.columns.<primary_column>.exampleGeometryGeoJSON` and encode it to WKB once, dispatched by its declared GeoJSON `type` (`Point`, `LineString`, `Polygon`, `MultiPolygon` supported — see Limitations for why this is a broadcast, not per-row, geometry). Broadcast that single WKB value to every row's geometry column. This fits the common case: a non-spatial tabular source given a placeholder/representative footprint by whoever authored the header.

### Phase 5 — write

Build the Arrow table (types mapped from `parquetSchema[].type` — `INT32`, `INT64`, `FLOAT`, `DOUBLE`, `BOOLEAN`, `BYTE_ARRAY/WKB`, `BYTE_ARRAY/UTF8`/`STRING`), attach `csvw:propertyUrl` (+ `description` if present) as per-field Arrow metadata, and attach file-level metadata: `geo` (the header's `geo` block, verbatim — so whatever was checked in Phase 1 is exactly what ships), `csvw` (CSVW table-schema-shaped column list with resolved `propertyUrl`s), and `geoparquet-header` (the full source header, for provenance/round-tripping). Write with the given `--compression`.

## Script

`scripts/csv_to_geoparquet.py` — a standalone, dependency-light (`pyarrow` only; `pyproj` only needed if you want to *generate* a valid CRS block, not to run the check) CLI tool. Run it directly; nothing needs to be copied out of this document first.

```bash
python3 skills/csv-to-geoparquet/scripts/csv_to_geoparquet.py --help
```

## Command line usage

```bash
SCRIPT=skills/csv-to-geoparquet/scripts/csv_to_geoparquet.py

# Whitespace-delimited source, strict column order (matches a header's declared parquetSchema exactly)
python3 "$SCRIPT" --csv simulation_1.csv --header simulation_1.geoparquet-header.json --delimiter " "

# Standard comma CSV, columns in any order, explicit output path
python3 "$SCRIPT" --csv stations.csv --header header.json --column-order by-name --output stations.geoparquet

# Fail the build instead of warning if the CRS block isn't valid PROJJSON
python3 "$SCRIPT" --csv stations.csv --header header.json --strict-crs

# Resolve column meanings via an MCP server instead of the header's inline context
python3 "$SCRIPT" --csv stations.csv --header header.json --mcp-command "my-mcp-server --stdio"
```

## What changed vs. the original one-off script

This is a generalization of `swedish-DT-simulations-output/examples/convert_csv_to_geoparquet.py`. Everything that was hardcoded to that one project is now a parameter or a data-driven default:

| Was hardcoded | Now |
|---|---|
| `DEFAULT_CSV`/`DEFAULT_HEADER`/`DEFAULT_OUTPUT` pointed at `simulation_1.*` next to the script | `--csv`/`--header` required; `--output` defaults from `header['fileName']`, not a fixed name |
| CSV dialect fixed to space-delimited, `"` quoted | `--delimiter`/`--quotechar`, defaulting to standard comma CSV |
| Compression fixed to `zstd` | `--compression`, still defaulting to `zstd` |
| MCP client identity hardcoded to `"swedish-dt-csv-to-geoparquet"` | `--client-name`, generic default |
| Geometry encoder only handled `Polygon` (function was literally named `polygon_wkb`) | dispatch table over `Point`/`LineString`/`Polygon`/`MultiPolygon` by declared GeoJSON type |
| Arrow type map covered only the 5 types the one source used | extended with `INT32`/`FLOAT`/`STRING` aliases, still simple to extend further |
| Source-column-order check was implicitly strict with no alternative | `--column-order strict|by-name` |
| File metadata key `seadots:geoparquet-header` | renamed to `geoparquet-header` (no project prefix) |
| No CRS sanity check — the exact bug that broke `geopandas.read_parquet()` on the original project would have shipped silently | `check_crs()` / `--strict-crs`, added directly because of that bug |

Validated both against the original `swedish-DT-simulations-output` source (whitespace-delimited, `Polygon` placeholder geometry, strict column order, 60,000 rows) and a synthetic unrelated dataset (comma CSV, `Point` geometry, `by-name` order, no `crs` declared at all — correctly defaults to `OGC:CRS84`).

## Limitations

- **No per-row geometry derivation.** The header's `exampleGeometryGeoJSON` is broadcast to every row. If a source actually has per-row lat/lon, WKT, or other geometry columns, this script will not use them — that's a real feature gap, not a config knob, and would need a `--geometry-from` style option added deliberately.
- **CRS check is a structural heuristic**, not a full PROJJSON validator. It catches the common "looks like JSON, missing required keys" mistake; it won't catch a PROJJSON object that's well-formed but semantically wrong (e.g. correct shape, wrong EPSG code).
- **`covering.bbox`** and other optional GeoParquet metadata blocks are passed through verbatim from the header, not validated against the physical Arrow schema. If a header claims a `covering.bbox` struct column that doesn't exist, this script won't catch it — check separately (see `bblock-vocab-alignment`'s validation phase for how that was caught previously).

## Validate the output

After conversion, don't stop at "it wrote a file" — the CRS bug this skill guards against only shows up when a real reader opens the file:

```python
import geopandas as gpd
gdf = gpd.read_parquet(output_path)   # raises CRSError if crs metadata is malformed
```

Also worth checking, depending on what the header is meant to guarantee:
- the embedded `geoparquet-header` metadata still validates against the block's `schema.yaml` (`jsonschema.validate`)
- every column's `propertyUrl` still resolves to a full IRI (this script's own `local_header_meanings()` is reusable for that check)
- `geo.columns.<primary>.geometry_types` matches what was actually written (here, always the single declared type, since geometry is broadcast)

## Interactions with other skills

- Run `bblock-vocab-alignment` first if the header's `propertyUrl`s haven't been checked against the vocabulary bblock yet — this skill trusts the header's semantics as given.
- Compose with `bblock-container-validation` / `response-schema-validator` to validate the embedded header against the owning block's `schema.yaml` after conversion.
- Compose with `context-completeness-checker` if the block also renders JSON-LD elsewhere (e.g. via `pygeoapi-jsonld-template`) and needs `@context` completeness checked beyond this script's own header-context resolution.

## References

- GeoParquet 1.1.0 spec — https://geoparquet.org/releases/v1.1.0/
- PROJJSON schema — https://proj.org/en/stable/schemas/index.html
- pyproj `CRS.to_json_dict()` — https://pyproj4.github.io/pyproj/stable/api/crs/crs.html
- CSVW (CSV on the Web) — https://www.w3.org/TR/tabular-data-primer/

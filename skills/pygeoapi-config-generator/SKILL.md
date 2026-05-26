---
name: pygeoapi-config-generator
description: Use when generating a local pygeoapi configuration (pygeoapi-config.yml) from an OGC building block source folder. Inspects the bblock's example files, detects whether the block should be served as OGC API - Features or OGC API - Records, and emits a single-collection config ready to mount into a pygeoapi Docker container. Records-relevant blocks are configured with provider type `record` as required by pygeoapi. Use as the first step of the pygeoapi test harness; not for production pygeoapi deployments.
---

**Status**: pre-release · **Scope**: generic · **Building-block coupling**: none.  
_Just authored; generates pygeoapi config from a bblock package._

# pygeoapi Config Generator Skill

## Purpose

Generate a `pygeoapi-config.yml` that serves the example data of one OGC building block as a local OGC API - Features or OGC API - Records collection. The config is the input to `pygeoapi-local-runner` and is consumed by `pygeoapi-jsonld-template` to know which collection to template.

## Activation

Use this skill when:

- preparing a local pygeoapi test for a bblock
- rendering bblock examples through `?f=jsonld` to validate context coverage
- assembling artefacts for `pygeoapi-test-harness`

Do not use this skill for:

- production pygeoapi configs
- multi-collection / multi-tenant configs
- non-vector/non-record data (NetCDF / CoverageJSON) — those need different providers and are out of scope

## Required input

- `block_path` — absolute or repo-relative path to `_sources/<block-name>/`

## Optional input

| Parameter | Default | Meaning |
|---|---|---|
| `collection_id` | `<block-name>` slugified | OGC API collection identifier |
| `sample` | first `*.geojson` / `*.gpkg` / `*.parquet` under `examples/` | concrete file to serve |
| `host` | `localhost` | server hostname for OpenAPI |
| `port` | `5000` | server port |
| `out` | `build-local/test-harness/<block>/pygeoapi-config.yml` | output path |

## Process

### Phase 1 — discover the bblock

1. Read `<block>/bblock.json` to extract `name`, `abstract`, `itemIdentifier`.
2. Read `<block>/description.md` (bblocks convention) for the long description.
3. Classify the collection profile as `record` or `feature`.
4. Locate example files under `<block>/examples/`. Prefer order: `.geojson` / individual GeoJSON `Feature` `.json` files → `.jsonfg` → `.gpkg` → `.parquet` → CSV with WKT.
5. If examples are individual `.json` GeoJSON Features, combine all schema-valid Feature files into `build-local/test-harness/<block>/data/<collection_id>.geojson` as a `FeatureCollection`; do not modify the source examples.
6. For `record` profile blocks, also materialize a TinyDB catalogue at `build-local/test-harness/<block>/data/<collection_id>.tinydb` from the normalized Records GeoJSON Features.
7. **Fail fast** if no usable example file exists. Surface:
   > No vector or record example found in `<block>/examples/`. The harness requires at least one `.geojson`, GeoJSON Feature `.json`, `.gpkg` or `.parquet` example. Add one or supply `sample=` explicitly.

Record profile signals:

Before applying these, check for OIM/SOSA observation signals. If a block depends on `ogc.hosted.iliad.api.features.oim-obs` or uses observation terms such as `SOSA`, `observation`, `observedProperty`, `phenomenonTime`, or `hasResult`, classify it as `feature` with an OIM observation profile. Observation/OIM signals override record/catalogue signals; blocks such as `benthic-biomass-density-imr` and `benthic-biomass-density-mareano` must not be configured as `type: record`.

- `bblock.json` `abstract`, `name`, or `tags[]` says `OGC API Records`, `records`, `catalog`, `catalogue`, `metadata`, `record`, or `GeoDCAT`
- `dependsOn[]` contains `ogc.geo.geodcat.geodcat-records`, `ogc.geo.geodcat.geodcat-records-prov`, or another OGC API Records/geodcat record block
- `schema.yaml|json` references `recordGeoJSON`, `geodcat-ogcapi-records`, `bblocks-ogcapi-records`, `api/records`, or `ogc-api/records`
- examples are GeoJSON Features whose properties/conformance clearly identify an OGC API Records record

Do not serve records-relevant blocks with a `feature` provider just because OGC API Records records are encoded as GeoJSON Features.

### Phase 2 — detect provider type

First choose the collection profile:

```
OIM/SOSA observation signals present → providers[].type: feature, name based on vector format
record signals present               → providers[].type: record, name: TinyDBCatalogue
otherwise                            → providers[].type: feature, name based on vector format
```

For `record` profile blocks:

```
normalized Records GeoJSON Features → data/<collection_id>.tinydb
provider type                       → record
provider name                       → TinyDBCatalogue
id_field                            → properties.identifier if consistently present, else id
```

For `feature` profile blocks:

```
.geojson  → name: GeoJSON
.json     → if GeoJSON Feature(s), derive FeatureCollection `.geojson`, name: GeoJSON
.gpkg     → name: GeoPackage
.parquet  → name: GeoParquet
.csv      → name: CSV  (with x_field / y_field / geom_field detected from headers)
.fgb      → name: FlatGeobuf
otherwise → name: OGR  with the OGR driver autodetected
```

### Phase 3 — emit YAML

Write the file using this template (Jinja-rendered with the gathered values):

```yaml
server:
  bind:
    host: 0.0.0.0
    port: ${port}
  url: http://${host}:${port}
  mimetype: application/json
  encoding: utf-8
  language: en-US
  cors: true
  pretty_print: true
  limit: 10                    # required so bare /items works without ?limit=
  templates:
    path: /pygeoapi/templates           # mounted by pygeoapi-local-runner
  manager:
    name: TinyDB
    connection: /tmp/pygeoapi-tinydb.json
  map:
    url: https://tile.openstreetmap.org/{z}/{x}/{y}.png
    attribution: Map data (c) OpenStreetMap contributors

logging:
  level: ERROR

metadata:
  identification:
    title: ${block.name} — local test harness
    description: ${block.abstract}
    keywords: [ogc, bblock, ${block-name}, local-test]
    keywords_type: theme
    terms_of_service: local test harness only
    url: ${block.itemIdentifier}
  license:
    name: CC-BY 4.0
    url: https://creativecommons.org/licenses/by/4.0/
  provider:
    name: ILIAD APIs Features (test harness)
    url: https://github.com/ogcincubator/iliad-apis-features
  contact:
    name: local
    email: local@example.org

resources:
  ${collection_id}:
    type: collection
    title: ${block.name}
    description: ${block.abstract}
    keywords: [${block-name}]
    links: []                  # pygeoapi OpenAPI generation expects this key
    extents:
      spatial:
        bbox: [-180, -90, 180, 90]
        crs: http://www.opengis.net/def/crs/OGC/1.3/CRS84
    providers:
      - type: ${provider_type}          # feature or record
        name: ${provider_name}          # TinyDBCatalogue for records
        data: /pygeoapi/data/${sample_basename_or_tinydb}
        id_field: ${id_field}           # record: identifier or id; feature: id/fid/OBJECTID/row index
    linked-data:
      context: ${inline_context_array}            # parsed bblock @context as a YAML list
      schema: /pygeoapi/data/schema.json          # bblock schema (for validator skill)
```

### Phase 4 — verify

After writing the file, run a quick YAML-parse + structural check:

- top-level keys: `server`, `logging`, `metadata`, `resources` present
- exactly one collection declared
- `data:` path is reachable in the mount layout
- records-relevant blocks have `providers[0].type: record` and `providers[0].name: TinyDBCatalogue`
- feature blocks have `providers[0].type: feature`
- `server.limit`, `server.map`, `metadata.identification.terms_of_service`, and `resources.<id>.links` are present
- `linked-data.context` is a list/object, not a string path

## Outputs

- `${out}` — the generated `pygeoapi-config.yml`
- A small manifest returned to the caller:

```json
{
  "config":         "build-local/test-harness/<block>/pygeoapi-config.yml",
  "block":          "_sources/<block>",
  "collection_id":  "<block-name>",
  "sample":         "examples/<file> or derived data/<collection_id>.geojson",
  "profile":        "feature|record",
  "provider":       "GeoJSON|TinyDBCatalogue",
  "mount_data":     "build-local/test-harness/<block>/data",
  "mount_context":  "_sources/<block>/context.jsonld",
  "mount_schema":   "_sources/<block>/schema.json"   // or schema.yaml normalised
}
```

## Edge cases

| Situation | Handling |
|---|---|
| `schema.yaml` instead of `schema.json` | Convert to JSON and emit `data/schema.json` under `build-local/test-harness/<block>/` |
| Multiple individual GeoJSON Feature `.json` files | Combine all schema-valid Features into one derived FeatureCollection and surface the count in the manifest |
| Multiple full vector datasets | Pick the first by priority order; surface the list and the chosen one in the manifest |
| `description.md` missing | Use `bblock.json.abstract` as the description |
| `id` field missing in features | Fall back to `id_field: fid` then `id_field: row_number`; surface a warning |
| Records block has no stable `properties.identifier` | Use top-level GeoJSON `id`; if absent, derive deterministic IDs before writing the TinyDB catalogue |
| pygeoapi image lacks TinyDBCatalogue support | Fail clearly with container logs; do not silently switch records to `type: feature` |
| Block has `schema:` block already in `bblock.json` linking external schemas | Use those URIs as `additional-properties` references; do not embed them |

## Interactions with other skills

- **Output → `pygeoapi-jsonld-template`** uses the manifest to know which collection to template.
- **Output → `pygeoapi-local-runner`** consumes the config + mount paths.
- **`bblock-register-resolution`** is called when the bblock declares external `dependsOn` schemas that need to be fetched before this skill can finish.

## References

- pygeoapi configuration reference — https://docs.pygeoapi.io/en/latest/configuration.html
- pygeoapi providers — https://docs.pygeoapi.io/en/latest/data-publishing/ogcapi-features.html
- pygeoapi OGC API - Records publishing — https://docs.pygeoapi.io/en/stable/publishing/ogcapi-records.html
- OGC API – Features — https://ogcapi.ogc.org/features/

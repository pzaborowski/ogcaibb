# Agent Index

Agents are grouped by role. Agent `name:` values remain stable; use these paths when editing or cross-referencing agent docs.

## Integrator Agents

| Agent | Path | Purpose |
|---|---|---|
| `marine-workflow-orchestrator` | `agents/integrator/marine-workflow-orchestrator.md` | Routes metadata, validation, and support tasks across marine workflows. |
| `marine-content-specialist` | `agents/integrator/marine-content-specialist.md` | Discovers marine content, profiles datasets, and prepares bblock specifications. |
| `marine-data-agent` | `agents/integrator/marine-data-agent.md` | Retrieves samples from authoritative marine services and vocabularies. |
| `gis-marine-data-specialist` | `agents/integrator/gis-marine-data-specialist.md` | Models geospatial vector data with semantic annotations and bblock structure. |
| `data-usability-checkin-agent` | `agents/integrator/data-usability-checkin-agent.md` | Performs usability assessment and cross-block alignment checks. |

## Converter Agents

| Agent | Path | Purpose |
|---|---|---|
| `metadata-dispatcher` | `agents/converters/metadata-dispatcher.md` | Detects input format and routes STAC/DCAT generation. |
| `stac-metadata-generator` | `agents/converters/stac-metadata-generator.md` | Generates STAC Items and Collections. |
| `dcat-metadata-generator` | `agents/converters/dcat-metadata-generator.md` | Generates DCAT and GeoDCAT records. |
| `csv-to-stac-converter` | `agents/converters/csv-to-stac-converter.md` | Converts CSV/tabular data to STAC metadata. |
| `csv-to-dcat-converter` | `agents/converters/csv-to-dcat-converter.md` | Converts CSV/tabular data to DCAT metadata. |
| `geojson-to-jsonfg-converter` | `agents/converters/geojson-to-jsonfg-converter.md` | Converts GeoJSON examples to JSON-FG. |

## Generator Agents

| Agent | Path | Purpose |
|---|---|---|
| `building-block-generator` | `agents/generators/building-block-generator.md` | Creates OGC building block source packages. |

## Validator Agents

| Agent | Path | Purpose |
|---|---|---|
| `validation-agent` | `agents/validators/validation-agent.md` | Runs static/container building block validation. |
| `pygeoapi-test-harness` | `agents/validators/pygeoapi-test-harness.md` | Serves a block through pygeoapi and validates runtime responses. |

## Slash Command Routing

| Command | Primary agent |
|---|---|
| `/marine-bblock` | `agents/integrator/marine-content-specialist.md` plus `agents/generators/building-block-generator.md` |
| `/generate-metadata` | `agents/converters/metadata-dispatcher.md` |
| `/geojson-to-jsonfg` | `agents/converters/geojson-to-jsonfg-converter.md` |
| `/validate-bblock` | `agents/validators/validation-agent.md` |
| `/test-bblock-rendering` | `agents/validators/pygeoapi-test-harness.md` |
| `/check-in` | `agents/integrator/data-usability-checkin-agent.md` |

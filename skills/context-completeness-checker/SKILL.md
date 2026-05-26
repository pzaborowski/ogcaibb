---
name: context-completeness-checker
description: Use when checking and repairing JSON-LD context completeness for an OGC building block example. Walks the JSON-LD instance recursively, lists property keys without @id mappings, flags ambiguous mappings and unused declared terms, resolves missing terms against local/imported building blocks and known public vocabularies, writes a mapping table back to the block description when requested, and returns an actionable list of remaining unmapped terms. Catches the silent "property without semantic mapping" failure that JSON Schema validation alone misses.
---

**Status**: pre-release · **Scope**: bblock-aware JSON-LD context audit and repair.<br>
_Works standalone on any JSON-LD instance + context; becomes resolver-aware when run inside a bblocks repository._

# Context Completeness Checker Skill

## Purpose

Given a JSON-LD instance and a `@context` (either inline or by path), assert that every property key in the instance has a semantic mapping (`@id`) defined in the context. Then resolve missing mappings against existing building blocks and public vocabularies before producing a patch-ready report. Reports:

- **unmapped**: keys present in the instance but with no context entry
- **ambiguous**: keys mapped to multiple `@id`s (typically a context bug)
- **context_unused**: context terms that no instance key uses (cleanup candidate)
- **resolved_mappings**: suggested or accepted mappings for previously unmapped keys, including source and confidence
- **mapping_documentation**: a table suitable for `description.md` or `bblock.json` description fields
- **remaining_unmapped**: the final actionable list that still needs human choice
- **schema_context_gaps**: schema properties that are not covered by the effective context stack
- **inherited_context_gaps**: schema `$ref`s whose corresponding inherited context was not referenced
- **local_context_extras**: locally declared context terms that appear to belong to an inherited context or are unused by local schema/example terms

## Activation

Use this skill when:

- validating a rendered pygeoapi JSON-LD output against a bblock context
- auditing a published example file against its `context.jsonld`
- ensuring every property in a STAC/DCAT JSON-LD has an authoritative vocabulary mapping
- resolving unmapped example keys against local/imported bblocks and known vocabularies
- documenting a block's JSON-LD term mappings for review
- checking that a schema block context mirrors inherited schema references and maps every inline schema property

Do not use this skill for:

- JSON Schema validation (use `response-schema-validator`)
- SHACL validation
- syntactic JSON-LD validity (use `pyld` for context expansion)

## Required input

- `instance` — JSON-LD object (dict or list of dicts) OR a URL to fetch
- `context` — either:
  - inline `@context` object (preferred; what pygeoapi embedded)
  - URL to a `context.jsonld`
  - file path
- `block` — optional bblock source directory or identifier. Required only for write-back/documentation updates.

## Optional input

| Parameter | Default | Meaning |
|---|---|---|
| `ignore_keywords` | `["@context", "@id", "@type", "@graph", "@list", "@set", "@value", "@language", "@reverse", "@nest", "type", "id"]` | JSON-LD keywords and trivial aliases that don't need an explicit `@id` |
| `descend` | `true` | recurse into nested objects and arrays |
| `feature_path` | `properties` | when checking pygeoapi features, descend into `properties` and each feature in `features[]` |
| `resolve` | `true` | attempt to resolve unmapped terms against bblocks and public vocabularies |
| `resolution_order` | `["context","local_bblocks","imported_bblocks","imported_vocabularies","public_vocabularies","similarity"]` | lookup priority |
| `public_vocab_priority` | `["NERC","CF","Darwin Core","OBIS","ICES","EMODnet","SOSA","OIM","OGC/ISO","schema.org"]` | preferred public vocabulary order for marine/OGC data |
| `write_back` | `auto` | update block documentation with accepted mappings when `block` is supplied; read-only when running standalone |
| `write_target` | `description.md` | where to write mapping documentation. Prefer `description.md`; use `bblock.json` only if that block already keeps descriptions there |
| `accept_confidence` | `exact` | auto-accept only `exact` matches unless the user explicitly permits `high`/`medium` suggestions |
| `refresh_catalog` | `false` | bypass cached imported register data when composing with `bblock-catalog` |
| `schema` | `auto` | optional schema path/URL; when `auto` and `block` is supplied, load `schema.yaml` or `schema.json` |
| `check_inherited_contexts` | `true` | compare schema `$ref`s to referenced context URLs and report inherited context gaps |
| `local_only_terms` | `true` | flag local context terms that duplicate terms better supplied by inherited contexts |

## Process

### Phase 1 — load both

If `instance` is a URL, fetch it:

```bash
curl -sfL -H "Accept: application/ld+json" "${instance}"
```

If `context` is a URL, fetch and parse. If it's a file path, read. If inline, use directly.

If `schema` is supplied, or `schema=auto` with `block`, load the local schema too. Collect:

- every `$ref` URI, preserving its schema path
- every inline property key declared under `properties`
- every inherited bblock schema reference that appears to have a matching published `context.jsonld`

### Phase 2 — collect declared terms

Walk the parsed `@context` (single object or array of objects). Preserve order because JSON-LD 1.1 context arrays are normally used as:

1. inherited/public context URL(s)
2. local context object

Fetch referenced context URLs when possible. Build:

```python
declared = {
  "submergedInfrastructureArea": "indo:submerged-infrastructure-area",
  "symbol": "prop-rel:hasSymbol",
  # ...
}
```

Handle nested `@context` blocks inside term definitions (JSON-LD 1.1) by tracking the path: a key inside `symbols[i]` is checked against the inner `@context` first, then the outer.

Also track `declared_source` for each term:

- `inherited:<url>` for terms that came from referenced context URLs
- `local:<path>` for terms declared in the block's own context object
- `inline` for terms embedded directly in an instance

### Phase 3 — walk the instance

For every object encountered (recursively):

1. for each key not in `ignore_keywords`:
   - is it declared in the current `@context` scope? → ok
   - declared multiple times in nested scopes with **different** `@id`s? → `ambiguous`
   - not declared anywhere? → `unmapped`
2. recurse into nested values when `descend=true`.

Track `used_terms` so that, after the walk, `context_unused = declared.keys() - used_terms`.

If a schema was loaded, also compute `schema_context_gaps`:

- inline schema properties with no mapping in the effective context stack
- schema properties inherited through `$ref` and used in examples but missing from inherited contexts
- example keys that only pass because of a broad `@vocab`, where a more precise inherited or public mapping should exist

Compute `local_context_extras` as local terms that are unused by both examples and inline schema properties. Report these as cleanup warnings unless they are explicitly documented as forward-compatible extension terms.

### Phase 3b — compare inherited schema refs to inherited contexts

When `check_inherited_contexts=true`:

1. For every schema `$ref` that points to an imported or local bblock annotated schema, derive the likely sibling context URL by replacing the final `schema.yaml` or `schema.json` with `context.jsonld`.
2. If that URL is resolvable and not present in the context array, add an `inherited_context_gaps` entry.
3. If the inherited context is empty or not resolvable, do not fail automatically; record a warning and require the local context to map any inherited terms that appear in examples.
4. If the local context duplicates many terms from an inherited context, suggest replacing those local definitions with a context URL reference.

### Phase 4 — resolve missing terms

If `resolve=true`, resolve every `unmapped` key in this order. Keep provenance for every candidate: `source`, `source_block` or `vocabulary`, `matched_term`, `suggested_id`, `confidence`, and `rationale`.

1. **Current context and prefixes**
   - If the key is already compact IRI-like (`prefix:suffix`) and `prefix` is declared, mark it mapped by prefix.
   - If the same normalized key appears under another local alias in the context, suggest that alias' `@id`.

2. **Local building blocks**
   - Compose with `bblock-catalog` over `source=local`.
   - For each local block, inspect `context.jsonld`, `schema.yaml|json`, `ontology.ttl`, `rules.shacl`, and `description.md` when available.
   - Prefer exact term-name matches in `context.jsonld`; then exact property-name matches in schemas; then vocabulary IRIs from ontology files.

3. **Imported building blocks**
   - Compose with `bblock-catalog` over `source=imported` using `bblocks-config.yaml` imports.
   - Use imported `register.json` records first. Fetch source `context.jsonld` or schema only when needed for candidate evidence.
   - Compose with `bblock-relevance` for fuzzy or thematic matches when exact catalog lookup does not resolve the key.

4. **Imported vocabularies already used by this block**
   - Extract namespaces and IRIs from the current `context.jsonld`, `ontology.ttl`, `rules.shacl`, examples, and imported contexts.
   - Prefer terms from vocabularies already present in the block before introducing a new namespace.

5. **Known public vocabularies**
   - Search in this priority order unless the user overrides it: NERC, CF Standard Names, Darwin Core, OBIS, ICES, EMODnet, SOSA, OIM, OGC/ISO, schema.org.
   - Use official vocabulary endpoints or already imported/generated local vocabulary blocks when available.
   - For online lookup, compose with `web-browsing-mcp` if configured; otherwise report that public lookup was skipped.

6. **Similarity fallback**
   - Normalize camelCase, snake_case, kebab-case, labels, and URI fragments.
   - Treat exact normalized match as `exact`, close label/fragment match as `high`, theme-only match as `medium`.
   - Never auto-accept `medium` or `low` matches; list them as candidates only.

Conflict handling:

- If multiple candidates have the same key and same `@id`, collapse them into one candidate with multiple evidence sources.
- If multiple candidates propose different `@id`s, mark the key `needs_decision` even when one candidate looks best.
- Prefer project/local block mappings over public vocabulary mappings when confidence is equal.
- Prefer public standard vocabularies over newly invented local IRIs unless the term is project-specific.

### Phase 5 — document mappings

When `block` is supplied and `write_back` is `auto` or `true`, update the owning block documentation with the accepted mapping set and any unresolved terms that need review. Prefer appending or replacing a clearly delimited table in `<block>/description.md`:

```markdown
## JSON-LD Term Mappings

| JSON key | @id | Source | Status | Notes |
|---|---|---|---|---|
| scientificName | dwc:scientificName | Darwin Core | accepted | exact public vocabulary match |
| aphiaID | obis:aphiaID | OBIS | needs-review | candidate from imported OBIS profile |
```

Use these markers when updating an existing generated table:

```markdown
<!-- context-completeness-checker:mappings:start -->
...
<!-- context-completeness-checker:mappings:end -->
```

Only write into `bblock.json` if the block already uses a JSON field for this documentation or `write_target=bblock.json` is explicitly supplied. Keep `bblock.json` valid JSON and avoid large prose there; prefer a compact `mappingSummary` or equivalent existing local pattern.

### Phase 6 — report

```json
{
  "pass":           false,
  "checked_keys":   142,
  "unmapped": [
    { "key": "windEnergyOutputMW", "first_seen_at": "features[3].properties.windEnergyOutputMW" },
    { "key": "turbineCountAdjusted", "first_seen_at": "features[3].properties.turbineCountAdjusted" }
  ],
  "ambiguous": [],
  "context_unused": [
    "fishingExclusionFraction",
    "areaUseByWindPark"
  ],
  "resolved_mappings": [
    {
      "key": "scientificName",
      "suggested_id": "dwc:scientificName",
      "source": "public_vocabularies",
      "vocabulary": "Darwin Core",
      "confidence": "exact",
      "status": "accepted",
      "rationale": "exact term match"
    }
  ],
  "needs_decision": [
    {
      "key": "aphiaID",
      "candidates": [
        {"suggested_id": "dwc:taxonID", "source": "Darwin Core", "confidence": "medium"},
        {"suggested_id": "obis:aphiaID", "source": "OBIS", "confidence": "high"}
      ]
    }
  ],
  "remaining_unmapped": [
    {
      "key": "turbineCountAdjusted",
      "first_seen_at": "features[3].properties.turbineCountAdjusted",
      "action": "choose an existing vocabulary term or mint a project-local term"
    }
  ],
  "mapping_documentation": {
    "target": "_sources/<block>/description.md",
    "written": false,
    "table": "| JSON key | @id | Source | Status | Notes | ..."
  },
  "schema_context_gaps": [
    {
      "key": "applicationPackage",
      "schema_path": "properties/properties/properties/applicationPackage",
      "action": "add a local context term or inherit the context that defines it"
    }
  ],
  "inherited_context_gaps": [
    {
      "schema_ref": "https://example.org/build/annotated/foo/schema.yaml",
      "suggested_context": "https://example.org/build/annotated/foo/context.jsonld",
      "status": "missing-from-context-array"
    }
  ],
  "local_context_extras": [
    {
      "term": "bbox",
      "reason": "already supplied by inherited GeoJSON/OGC Records context"
    }
  ],
  "suggestions": [
    { "key": "windEnergyOutputMW", "suggested_id": "qudt:value", "rationale": "matches QUDT pattern" }
  ]
}
```

`pass = (remaining_unmapped == []) and (ambiguous == []) and (needs_decision == []) and (schema_context_gaps == []) and (blocking inherited_context_gaps == [])`.

`context_unused` and `local_context_extras` are **informational** unless they hide a real ambiguity; surface them as cleanup hints.

## Outputs

The report dict above, plus documentation edits when `block` is supplied and `write_back` is `auto` or `true`.

Always include a short human-readable summary:

- number of keys checked
- missing before resolution
- auto-resolved
- needs decision
- remaining unmapped
- documentation target written or skipped

## Edge cases

| Situation | Handling |
|---|---|
| Context declares a term with the same `@id` twice (consistent) | Allow; not ambiguous |
| Instance has no `@context` (plain JSON) and `context=` not supplied | Fail with "no context available — supply context= or embed @context inline" |
| Context is itself an array of contexts (JSON-LD 1.1) | Walk each in order; later entries override earlier on key collisions |
| Instance is an OGC API FeatureCollection (`type=FeatureCollection`) | Auto-recurse into `features[].properties` |
| Property key uses a JSON-LD prefix (e.g. `"dwc:scientificName"`) | Pass if the prefix is declared and the suffix isn't required to be a context term |
| Public vocabulary lookup is unavailable | Continue with local/imported bblock resolution and list skipped vocabularies |
| Candidate mapping is plausible but not exact | Put it in `needs_decision`; do not silently modify context |
| A term is intentionally unmapped payload data | Allow an explicit ignore list with a reason and include it in the report |

## Interactions with other skills

- Compose with `bblock-catalog` to enumerate local + imported blocks and harvest existing context/schema/ontology mappings.
- Compose with `bblock-relevance` when an unmapped term has no exact match and nearby blocks may define a candidate concept.
- Compose with `vocprez-annotation` only for Turtle/SKOS/DCAT annotation work; this skill owns JSON-LD context mapping and description-table documentation.
- Called by `pygeoapi-test-harness` after `response-schema-validator`. Combined report is returned to the user.
- Can run standalone against any `example.json + context.jsonld` pair in a bblock — useful as a pre-commit check.

## References

- JSON-LD 1.1 (context evaluation) — https://www.w3.org/TR/json-ld11/#context-definitions
- `pyld` — https://github.com/digitalbazaar/pyld
- NERC Vocabulary Server — https://vocab.nerc.ac.uk/

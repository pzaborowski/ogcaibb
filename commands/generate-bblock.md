---
description: Generate a new OGC Building Block via the building-block-generator agent, with interactive kind selection and reuse-of-existing-blocks confirmation
argument-hint: [theme-or-description] [--kind=metadata|data|conceptual] [--name=<bb-name>] [--id=<bb-id>] [--build-on=<bb-id>[,<bb-id>...]]
---

**Status**: experimental · **Scope**: generic · **Wraps**: `building-block-generator`.
_Dispatches the building-block-generator agent with a mandatory kind decision and a mandatory reuse confirmation step._

Run the `building-block-generator` subagent on `$ARGUMENTS`. The command's job is to:

1. Determine the **kind** of building block to generate.
2. Resolve the **set of existing building blocks to build on** (i.e., `dependsOn`) by surfacing the catalog/relevance match and asking the user to confirm before any files are written.

Then hand control to `building-block-generator` so it can author the bblock package under `_sources/<slug>/` (or `_sources/_staging/<slug>/` if the user prefers staging).

## Required interactive steps

### Step 1 — Block kind (skip only when `--kind=` is set in `$ARGUMENTS`)

If the kind is not already supplied via `--kind=`, ask the user **before any other work**:

> Is this building block for **metadata**, **data**, or **conceptual** (schemaless data model)?

Map the answer to the agent's input as follows:

| User answer  | `itemClass` | Primary artifacts                                | Notes                                                                 |
| ------------ | ----------- | ------------------------------------------------ | --------------------------------------------------------------------- |
| metadata     | `schema`    | `schema.yaml`, `context.jsonld`, JSON examples   | Catalogue/record block (DCAT, STAC, OGC API Records). Examples are metadata records. |
| data         | `schema`    | `schema.yaml`, `context.jsonld`, JSON/GeoJSON examples | Instance-data block. Examples are real source samples per the agent's Quality Contract. |
| conceptual   | `model`     | `ontology.ttl`, optional `rules.shacl`, RDF examples | RDF-first / schemaless data model. Do **not** invent `schema.json` or `context.jsonld` unless the user explicitly asks for a schema companion. |

Use the AskUserQuestion tool with these three options (plus the implicit "Other" for custom direction). Persist the answer in the agent's input as `item_class` and as a hint for `vocabulary_preferences` and artifact layout.

### Step 2 — Reuse / build-on confirmation (mandatory)

Before generation:

1. Invoke the `bblock-catalog` skill with a category filter matching the kind from Step 1 (`metadata` / `vector` or `gridded` / `ontology` or `model`) and a free-text query built from `$ARGUMENTS` (theme/description).
2. Rank catalog hits with the `bblock-relevance` skill (six dimensions, weights from `.claude/bblock-relevance.yaml`).
3. Present the top-ranked existing blocks to the user in a single AskUserQuestion call:

   > These existing building blocks look most relevant. Do you want to **build on** them (declare as `dependsOn`), **reuse one** instead of generating a new block, or **proceed with no dependencies**?

   - Offer up to 4 options:
     - `Build on top-N (recommended)` — declare the surfaced bblocks as `dependsOn` and continue to generation.
     - `Reuse <bb-id>` — stop generation, point the user at the existing block, and recommend extending its examples or profile instead of creating a duplicate.
     - `Pick a different set` — let the user enumerate `--build-on=<bb-id>[,<bb-id>...]` manually.
     - `Proceed with no dependencies` — only allowed after the user has acknowledged the catalog result is empty or irrelevant.

4. If `--build-on=` was already supplied in `$ARGUMENTS`, **still** show the catalog ranking and ask the user to confirm that the requested dependencies are the intended set. Do not silently override the user.
5. Quote the catalog result (matched bblocks **or** explicit empty result) in the final report so reuse-vs-generate decisions are auditable.

Skip Step 2 only if the user has answered both:
- "I have already reviewed the catalog" **and**
- supplied `--build-on=` explicitly.

In every other case, the reuse confirmation is mandatory — this command exists specifically to enforce it.

## After both steps

Hand off to `building-block-generator` with a structured input that includes:

```json
{
  "block_name": "<from --name or asked>",
  "block_id": "<from --id or asked>",
  "title": "<inferred from theme or asked>",
  "abstract": "<inferred from theme or asked>",
  "item_class": "schema | model",                  // from Step 1
  "kind": "metadata | data | conceptual",          // from Step 1, for prompt routing only
  "dependencies": ["<bb-id>", "..."],              // from Step 2 confirmation
  "vocabulary_preferences": ["NERC", "CF", "Darwin Core", "OBIS", "ICES", "EMODnet", "OGC/ISO", "schema.org"],
  "output_path": "_sources/<slug>/"                // proposed and confirmed before write
}
```

The agent then runs its own workflow (analyze → choose type-specific artifacts → write files → validate with the Docker OGC postprocessor → report).

For schema blocks, require the generator to apply the default composition contract:

- reuse existing local/imported schemas via `$ref`/`allOf` instead of copying inherited structures inline
- make `context.jsonld` mirror schema inheritance by referencing inherited context files first, then adding local terms
- derive `dependsOn` from actual schema refs, context refs, and transform/profile contracts
- reject local dependency cycles
- validate every example against the local schema with all refs resolved, even when a filtered postprocess run reports zero tests

## Parsing `$ARGUMENTS`

Parse flags in any order; treat unflagged text as the theme/description:

- `--kind=metadata|data|conceptual` — bypasses Step 1
- `--name=<bb-name>` — Building Block name (human-readable)
- `--id=<bb-id>` — fully qualified Building Block identifier
- `--build-on=<bb-id>[,<bb-id>...]` — pre-selected dependencies (still confirmed in Step 2)
- Remaining text — theme/description used for catalog query and abstract

If both `--name` and `--id` are missing, ask for them per the agent's Naming Rules before writing files.

## Examples

```
/generate-bblock fishing-effort time series                              # asks for kind, then catalog reuse
/generate-bblock --kind=metadata STAC catalogue for benthic surveys      # skips Step 1, still runs catalog reuse
/generate-bblock --kind=conceptual ecosystem services indicator model    # builds a model block (ontology.ttl)
/generate-bblock --kind=data --build-on=ogc.geo.json-fg.feature reef biomass observations
```

## Output

On success, the agent reports:
- the resolved `itemClass` and kind,
- the confirmed `dependsOn` set with the catalog evidence quoted,
- the schema/context inheritance choices and any local dependency-cycle check result,
- the generated folder layout under `_sources/<slug>/`,
- and the Docker postprocessor validation summary.

On reuse: the agent reports the matched bblock id and does **not** write a new folder.

If `$ARGUMENTS` is empty, ask the user for at least a theme/description before invoking Steps 1 and 2.

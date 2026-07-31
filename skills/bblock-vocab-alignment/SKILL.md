---
name: bblock-vocab-alignment
description: Use when a data-source building block's header, schema, or CSVW-style `propertyUrl` declarations should resolve to a semantic vocabulary — a local/imported model bblock (e.g. `oim-variables`) or a public vocabulary (NERC, CF, Darwin Core, OBIS, ICES, EMODnet, QUDT, schema.org) — and you need to check whether they actually do. Extracts every declared term programmatically, cross-checks each against the target vocabulary's concept declarations, and for anything dangling or missing decides whether a known vocabulary term applies or a new extension concept should be proposed in the fallback vocabulary bblock. Treats placeholder/reserved/future-use columns as a single shared concept instead of minting one per column. Propagates any premise correction into the block's `description.md`, `bblock.json` abstract/tags, and adjacent example prose, then validates every edited file. Not for JSON-LD `@context` completeness on instance data (use `context-completeness-checker`) or for generating a brand-new building block from scratch (use `building-block-generator`).
---

**Status**: pre-release · **Scope**: bblock-aware vocabulary alignment for header/schema `propertyUrl` declarations.<br>
_Derived from a manual alignment pass on `swedish-DT-simulations-output` against `oim-variables`._

# Building Block Vocabulary Alignment Skill

## Purpose

Given a data-source building block whose example header/schema (a `*-header.json`, CSVW `parquetSchema`, or similar) declares a `propertyUrl` (or equivalent compact-IRI term) per column, verify that every declared term resolves to a real concept in its target vocabulary. Partition terms into resolved / dangling / missing, resolve gaps against known vocabularies first, fall back to proposing new concepts in the project's vocabulary bblock only when nothing else fits, and keep the rest of the block's documentation consistent with whatever the alignment reveals.

## Activation

Use this skill when:

- a building block's example header assigns `propertyUrl`/term values that look like they reference a vocabulary (`prefix:local`) and you need to confirm those concepts actually exist
- a data source has columns with no semantic mapping at all and needs one assigned from `oim-variables` or another local/imported vocabulary bblock
- reviewing a bblock before promotion out of `under-development`/`dev` status, to catch dangling vocabulary references JSON Schema validation won't flag
- a column name and its documented description have drifted apart (e.g. the header calls something one thing, the prose calls it another) and needs reconciling alongside the vocabulary fix

Do not use this skill for:

- JSON-LD `@context` completeness/mapping audits on instance data — use `context-completeness-checker`
- generating a new bblock package from raw data — use `building-block-generator` (compose this skill in afterward)
- SHACL or JSON Schema structural validation — use `bblock-container-validation` / `response-schema-validator`

## Required input

- `block` — source directory or identifier of the building block being checked (e.g. `_sources/swedish-DT-simulations-output`)
- `header_or_schema` — path to the file declaring per-column/per-property terms (defaults to every file under `block/examples/` matching `*-header.json`, `schema.yaml`, or `schema.json` that contains `propertyUrl`)

## Optional input

| Parameter | Default | Meaning |
|---|---|---|
| `vocabulary_block` | `oim-variables` (or the block's `dependsOn` model block if declared) | the fallback vocabulary bblock to extend when no known vocabulary term fits |
| `public_vocab_priority` | `["NERC","CF","Darwin Core","OBIS","ICES","EMODnet","QUDT","SOSA","OIM","OGC/ISO","schema.org"]` | search order before proposing an extension |
| `propagate_scope` | `ask` | `header-only` fixes just the header file; `full` also updates `description.md`/`bblock.json`/`examples.yaml` prose that shares the same premise; `ask` means confirm with the user which scope before touching prose beyond the header |
| `placeholder_policy` | `single-shared-concept` | when several dangling columns turn out to be reserved/unused/future-use, propose one shared placeholder concept rather than one per column; only mint per-column concepts when the user confirms each has distinct, resolvable semantics |

## Process

### Phase 1 — locate candidate and authority

Identify the header/schema file(s) declaring terms, and the vocabulary bblock they should resolve against. If `vocabulary_block` isn't given, check the candidate block's `bblock.json` `dependsOn` first, then compose with `bblock-catalog` to find local model (`itemClass: model`) bblocks whose `sources`/tags suggest they're the right register.

### Phase 2 — extract every declared term programmatically

Do not eyeball a long column list. Parse the header/schema file and pull out every `(name, propertyUrl)` pair:

```python
import json
data = json.load(open(header_path))
cols = data["parquetSchema"]  # or the equivalent field for the format in hand
terms = [(c["name"], c.get("propertyUrl")) for c in cols]
```

### Phase 3 — check each term resolves in the authority

For every `prefix:local` `propertyUrl`, confirm the authority (usually a Turtle file) actually declares that subject:

```python
import re
ttl = open(vocab_ttl_path).read()
missing = []
for name, purl in terms:
    if not purl or ":" not in purl:
        continue
    prefix, local = purl.split(":", 1)
    if prefix not in vocab_prefixes:   # e.g. skip geo:, dct: — not this vocabulary's namespace
        continue
    if not re.search(rf"\b{re.escape(prefix)}:{re.escape(local)}\s+a\s", ttl):
        missing.append((name, purl))
```

Also flag columns with **no** `propertyUrl` at all — these are missing, not dangling, and go through the same resolution phase below. Prefer this exact-declaration check over relying on `skos:altLabel`/`skos:notation` string matching alone — a term can have a plausible-looking label without a real concept declaration, and vice versa (see `mean_biomas_sprat`-style misspellings, which are legitimately covered via `altLabel`/`notation` on the *correct* canonical concept).

If the vocabulary bblock ships an extraction/test script (e.g. `extract_indicators.py`'s `CODES` list), treat it as a second, independent signal — a term absent from that list even though it resolves in the TTL is a sign the vocabulary and its own tooling have drifted, worth a note even if not blocking.

### Phase 4 — resolve dangling/missing terms

For each gap, in order:

1. Check whether a known vocabulary already covers it (`public_vocab_priority`), especially for domain-specific quantities (mass, currency, dimensionless rates → QUDT `quantitykind`; taxa → Darwin Core/OBIS; sensor/observation shape → SOSA).
2. If nothing fits, propose a new concept in `vocabulary_block`, matching that vocabulary's existing modeling pattern (e.g. `ssn:Property` + `skos:Concept` in a `skos:ConceptScheme`, with `rdfs:label`/`skos:prefLabel`/`skos:definition`, `skos:broader` where a natural parent exists, and `qudt:hasQuantityKind` when it's a genuine quantity).
3. **Before minting one concept per column**, check whether the columns are actually distinct in meaning or are placeholders/reserved/unused (all-same sentinel value across the sample data is a strong hint). Ask the user rather than assuming — collapsing 17 near-identical placeholder columns into 17 fake distinct concepts is worse than one honest shared "reserved for future use" concept with a `scopeNote` explaining why. Don't over-engineer per-column `altLabel`/`notation` entries onto a shared placeholder concept purely to preserve a lookup mechanism (e.g. an extraction script's notation matching) that the columns don't actually need — that mechanism exists to disambiguate *distinct* concepts, not to fake distinctness that isn't there.

### Phase 5 — propagate the fix

A vocabulary fix that leaves adjacent prose asserting the old, now-wrong premise is only half done. After fixing `propertyUrl`s, check the same file for descriptive text built on the same assumption (a `source.note`, per-column `description`, or a `conversionNotes` list that references the old semantics) and correct it in the same pass — this is a direct consequence of the propertyUrl fix, not scope creep.

Then check whether the false premise reaches further:

- `examples.yaml` — does its prose name the columns differently than the actual header/CSV (naming drift)?
- `description.md` — does the block-level narrative assume the same thing the header used to assert?
- `bblock.json` — do the `abstract` or `tags` encode the same assumption (e.g. a tag naming a concept that turned out not to apply)?

Fix the header/adjacent-prose tier automatically (Phase 5a). For `description.md`/`bblock.json`/`examples.yaml` (Phase 5b), honor `propagate_scope`: if `ask`, confirm with the user before rewriting block-level narrative — that's an editorial judgment call about how far "alignment" should reach, not a mechanical one.

### Phase 6 — validate

Before reporting done:

- `json.load()` every edited JSON file
- parse every edited Turtle file with `rdflib` (`pip install rdflib` if not already available in the environment)
- re-run any extraction/test script the vocabulary bblock ships, if the newly resolved terms were meant to be discoverable through it
- re-grep the block's own directory for the string(s) that triggered the fix, to confirm nothing was missed

## Outputs

A short report:

- terms checked, resolved / dangling / missing counts
- gaps filled from a known vocabulary vs. proposed as new extension concepts, with the concept IRIs
- placeholder/reserved columns collapsed to a shared concept, if any, and why
- files touched, grouped by tier (header/adjacent-prose vs. block-level narrative)
- validation results (JSON/Turtle parse, script re-run)

## Edge cases

| Situation | Handling |
|---|---|
| `propertyUrl` uses a namespace outside the target vocabulary (e.g. `geo:hasGeometry`, `dct:source`) | Skip — not this vocabulary's concern; don't flag as missing |
| A column's raw name is misspelled/hyphenated relative to the canonical concept (e.g. `mean_biomas_sprat` vs. `mean_biomass_sprat`) | Resolved if the canonical concept's `altLabel`/`notation` already covers the variant spelling; don't create a second concept |
| All sampled values for a set of dangling columns are identical/zero/null | Likely placeholder/reserved — confirm with the user before treating as N distinct concepts |
| Vocabulary bblock has its own extraction/matching script | Treat gaps in that script's coverage as a secondary, non-blocking signal, not the primary resolution mechanism |
| Fixing the header would leave `description.md`/`bblock.json` asserting a now-contradicted premise | Surface this explicitly and ask before rewriting block-level narrative, rather than silently leaving the contradiction or silently rewriting prose beyond the header |
| No `dependsOn` links the candidate block to any vocabulary bblock, yet it uses that vocabulary's namespace throughout | Note the missing formal dependency as an aside; don't add it unless asked, since it may be intentional (string-only `propertyUrl`, not a schema `$ref`) |

## Interactions with other skills

- Compose with `bblock-catalog` to find candidate vocabulary bblocks when `vocabulary_block` isn't specified.
- Compose with `context-completeness-checker` when the same block also has a `context.jsonld` whose `@context` term completeness needs checking — that skill owns JSON-LD `@id` mapping on instance data; this skill owns `propertyUrl`/schema-level term declarations and the backing vocabulary bblock's concept coverage.
- Compose with `web-browsing-mcp` for public vocabulary lookups when a gap might be covered by NERC/CF/Darwin Core/OBIS/ICES/EMODnet rather than warranting a new local concept.
- Compose with `bblock-container-validation` after edits, to confirm the vocabulary bblock and the data-source bblock both still pass their respective Docker-based validation.

## References

- SKOS — https://www.w3.org/TR/skos-reference/
- QUDT quantity kinds — http://qudt.org/vocab/quantitykind/
- rdflib (Turtle parsing) — https://rdflib.readthedocs.io/

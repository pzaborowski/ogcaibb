---
description: Validate an OGC building block via Docker container (schema, context, examples, tests)
argument-hint: <path-to-_sources/block-name>
---

**Status**: stable · **Scope**: generic · **Building-block coupling**: none.  
_Wraps `validation-agent`._

Validate the OGC building block at `$ARGUMENTS` using the `validation-agent`.

Run a full validation including:
1. Structure check — all required files present and correctly named
2. Schema validation — JSON Schema syntax and use of `$ref`/`allOf` for inherited bblock shapes
3. Context composition — inherited schema refs have corresponding inherited context refs and local terms only cover local properties
4. Dependency graph — `dependsOn` reflects real schema/context/transform/profile relationships and has no local cycles
5. Example compliance — every example conforms to the local schema with remote refs resolved
6. Semantic coverage — all schema/example properties have context entries with authoritative vocabulary URIs
7. Test execution — test suites pass
8. Provenance — metadata and source URLs documented

Use the `bblock-container-validation` skill to run the Docker-based ogcincubator/bblocks-postprocess container.

Report results as:
- Structure check results
- Schema validation summary
- Schema/context composition report
- Dependency graph report
- Example compliance report
- Context completeness analysis (missing vocabulary mappings)
- Test execution results
- Overall validation status (Pass / Warnings / Errors)

If `$ARGUMENTS` is empty, ask the user to specify a building block path (e.g., `_sources/macroobservation`).

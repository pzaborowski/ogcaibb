---
description: Assess a source dataset for usability and stage BB1/BB2/BB3 via the data-usability-checkin-agent
argument-hint: <path-or-url> [--reference-set=<bb-ids>]
---

**Status**: experimental (PoC) · **Scope**: repo-specific to `iliad-apis-features` · **Wraps**: `data-usability-checkin-agent`.

Dispatch the `data-usability-checkin-agent` subagent to run the full SeaDOTs/ILIAD check-in pattern on `$ARGUMENTS`:

1. Assess usability against the seven SeaDOTs criteria (relevance, representativeness, reliability, temporal validity, ingestability, reusability, initial data-quality assessment).
2. Run the mandatory **catalog pre-check** via the `bblock-catalog` skill for each of BB1 / BB2 / BB3 — over local `_sources/` **and** every register imported in `bblocks-config.yaml`.
3. Rank candidates with `bblock-relevance` (six weighted dimensions) and quote the ranking table in the BB-selection rationale.
4. Stage three related building blocks under `_sources/_staging/`:
   - **BB1** — source-data block with representative examples drawn from the raw source.
   - **BB2** — target-model block; **always pick from catalog matches** when one fits.
   - **BB3** — metadata/catalog block linking BB1 and BB2 (OGC Records + relevant STAC extensions).
5. Delegate file authoring to `building-block-generator`, `metadata-dispatcher`, and `validation-agent`; this agent owns usability assessment, BB selection rationale, and the process report.

Pass the entire `$ARGUMENTS` string through to the agent so it can parse the source path/URL and any flags. Do not author bblock files yourself in the parent context — delegate.

## After staging

- `/validate-bblock _sources/_staging/<bb-id>` to run the Docker OGC postprocessor on each staged block.
- Promote via the check-in web UI or `iliad-checkin promote --id <bb-id>`.

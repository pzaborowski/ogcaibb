---
name: readme-to-deliverable-docx
description: >
  Pours a markdown README/spec into an existing Word deliverable template,
  keeping its cover page, executive summary, TOC, definitions/acronyms
  table, references table, and appendix intact while replacing the body
  with translated headings, paragraphs, lists, code blocks, and tables
  mapped onto that template's own named styles. Also re-expresses
  PlantUML/ASCII-art diagrams as rendered Graphviz graphics instead of
  monospace text, and can normalize the whole document onto one font
  (asking the user which, if several are mixed) while preserving
  monospace code styles. Use whenever someone asks to update a Word
  deliverable from a README, sync a docx with markdown, or hands over a
  .md file plus a .docx template wanting one turned into the other --
  including recurring cases like EU deliverables kept in sync with a
  markdown spec. Don't wait for "convert markdown to docx" to be spelled
  out; the need is usually "keep the Word version in sync with the
  README."
license: MIT
compatibility: >
  Requires Python 3 with the `python-docx` package installed
  (`pip install python-docx`) and the Graphviz `dot` CLI on PATH (used to
  render re-expressed PlantUML/ASCII-art diagrams as PNGs). No network
  access needed -- diagram rendering is fully local. Rendering the
  produced .docx to PDF for visual QA (recommended) additionally benefits
  from LibreOffice (`soffice --headless --convert-to pdf`) being
  available, but that step is optional.
---

# README → Word deliverable sync

## Why this is a template-surgery problem, not a markdown-to-docx problem

A naive "convert markdown to Word" approach (e.g. pandoc with a generic
reference doc) throws away exactly the parts that make a deliverable usable:
the cover page with project/grant metadata, the executive summary, the
auto-generated table of contents, a definitions/acronyms table, a formal
references table, and an appendix. Those live in the template and must
survive untouched. What actually needs to happen is: **keep the front
matter and back matter exactly as they are, and replace only the body**
(and, sometimes, specific sub-sections like the "References" list or a
"Conclusions" placeholder) with content translated from the markdown.

The complication is that every template names its styles differently. One
deliverable template might have `Heading 1` / `Heading 2` / `Heading 3`.
Another (like the one this skill was built against) has custom styles
called `Apdx H1` for front-matter section markers, `Heading 1` for real
body chapters, and `Appendix Header H2` / `Appendix Header H3` for
sub-sections -- with bullets needing a specific numbering id to render
correctly. **Never hardcode style names.** Always look at the actual
template first.

## Workflow

1. **Inspect the template before touching it.** Run:
   ```
   python3 -c "
   import sys; sys.path.insert(0, 'scripts')
   import docx_tools as dt
   doc = dt.load('TEMPLATE.docx')
   print(dt.dump_structure(doc))
   "
   ```
   This prints every paragraph and table in true document order, with its
   style name, list-numbering info, and a text preview. Read the whole
   thing. You're looking for:
   - Which style marks the **front-matter/back-matter section titles**
     (Executive Summary, Definitions, References, Appendix) -- these often
     use a distinct style from ordinary body chapter headings.
   - Which style is used for real **body chapter headings** (the ones that
     will map from markdown `#`/`##`), and which for the next 1-2 levels
     down.
   - The **body paragraph style** (often something like `Normal (Web)` if
     the document was ever created by pasting from a browser/markdown
     renderer, plain `Normal` otherwise).
   - The style + `numId` used for **bullets** -- look for `[list ilvl=... numId=...]`
     annotations. If the template has an example/style-guide section
     showing "first level bullet", "second level bullet" etc., that tells
     you exactly which numId to reuse for which nesting depth.
   - A **code/preformatted style** (e.g. `HTML Preformatted`) for fenced
     code blocks and ASCII-art diagrams -- render each line as its own
     paragraph in that style so line breaks survive exactly.
   - A **caption style** (e.g. `Caption`) if you'll be labelling figures/tables.
   - An existing **table style name** used by real content tables (not the
     front-matter admin tables) -- e.g. `Table Grid` or `Normal Table`.
   - Where the replaceable **body actually starts and ends** -- typically
     bounded by two headings in the "chapter" style (e.g. the first chapter
     heading like "Introduction" and a fixed closing heading like
     "Conclusions" or "References" that's part of every deliverable in
     that family).
   - Whether the template already has a **partial hand-translation** of an
     earlier version of the markdown sitting in its body. If so, treat it
     as a worked example of the intended style mapping -- diff it mentally
     against the corresponding markdown section rather than guessing.

2. **Read the markdown and note anything that doesn't fit the generic
   parser's assumptions**: bold-only lines used as pseudo-headings (a very
   common README convention -- `**Some Heading**` on its own line, meant to
   read as a sub-header without bumping the real heading level), duplicate
   or inconsistent manual numbering in `##`/`###` headings (don't silently
   renumber the user's content -- carry it through and mention the
   inconsistency in your summary instead), a trailing References/Appendices
   section that should land in the template's own References/Appendix
   sections rather than inside the main body, and images (`![alt](src)`)
   whose actual files you don't have -- these become a clearly marked TODO
   in the output rather than a silent gap.

3. **Write a small config** (see `scripts/build_deliverable.py`'s docstring
   for the exact JSON shape) capturing the style-role mapping you just
   derived, the anchors that bound the region to replace, how many leading
   markdown headings to drop (the doc's own title is usually already on the
   cover page), and whether to stop before a trailing heading (e.g. a
   `References` section that belongs in a different part of the template).

4. **Run `scripts/build_deliverable.py`** with that config to do the actual
   body swap. It deletes everything between your two anchor paragraphs and
   splices in freshly rendered headings/paragraphs/lists/code/tables in
   their place, using python-docx under the hood -- it does no editorial
   thinking, so garbage in the config produces garbage out. Read the
   printed block-count summary and warnings (e.g. images that need manual
   placement) after each run.

5. **Handle any extra target zones separately.** A single deliverable often
   needs content routed to more than one place: the main body between two
   chapter headings, PLUS a categorised reference list slotted in after an
   existing citation table, PLUS a short appendix pointer, PLUS maybe a
   synthesized paragraph for a template-required section (like
   "Conclusions") that the source markdown doesn't actually have. Don't try
   to force one script invocation to handle all of this -- either call
   `build_deliverable.py` again with different anchors/config for each
   zone (it's idempotent and safe to chain), or write a short driver script
   that imports `docx_tools` directly for one-off insertions (e.g.
   "find this table, insert new paragraphs right after it" -- see the
   `find_paragraph` / `insert_*` / `_new_paragraph_after` helpers). This is
   expected and normal, not a sign the tool is missing a feature.

6. **Verify visually, not just structurally.** `dump_structure()` tells you
   the right paragraphs exist with the right styles, but the only way to
   catch layout problems (bullets that got split by soft-wrapped source
   lines, tables with painfully narrow columns, headings that didn't
   inherit the template's outline numbering the way you expected) is to
   actually render it:
   ```
   libreoffice --headless --convert-to pdf output.docx
   ```
   then look at the PDF page by page (or at least: cover page, the start of
   the newly-generated body, any pages with tables/code blocks, and the
   tail end where back matter resumes). Flag anything you can't fix
   cleanly (e.g. a template-side numbering quirk unrelated to your edit)
   in your summary rather than fighting the low-level XML further.

7. **Tell the user what still needs a human.** Things this tool
   deliberately does NOT do: refresh Word's Table of Contents / Table of
   Figures / List of Tables fields (these are live fields -- the user needs
   to open the doc in Word and press Ctrl+A then F9, or right-click →
   Update Field), insert real images (it leaves a clearly marked
   `[TODO: insert figure here ...]` note), or fill in a
   Definitions/Acronyms glossary table (unless the markdown has one).
   Say so explicitly rather than letting the user discover it later.

## Font consistency

Deliverable templates that have accumulated content over time (copy-pasted
from a browser, a different template generation, or hand-edited sections)
routinely end up with several fonts stacked on top of each other: a base
font on the `Normal` style, a different font resolved through the theme
for styles that never set an explicit override, one more hardcoded onto
headings or a section that got pasted in from somewhere else, and
(correctly) a monospace font for code/ASCII blocks. Don't assume "one
style has the right font" means the whole document does -- font
resolution in a docx comes from (in order of precedence) a direct run
override, then the paragraph's style, then that style's parent style, then
`docDefaults`, then the theme's minor/major font. A style that looks fine
in isolation can still be surrounded by paragraphs that resolve through a
completely different path.

Before finishing a sync, check what fonts are actually in play: scan every
style's `w:rFonts` (`style.font.name` in python-docx, or read the raw
`w:rPr`/`w:rFonts` XML for styles that don't expose it through the simple
API), and scan direct run-level `w:rFonts` overrides too (style changes
don't affect direct formatting). **If more than one font is in use, ask
the user which one to standardize on** rather than guessing -- and
specifically call out any font that's serving a clear functional purpose
(monospace on code/preformatted styles) so the user can decide whether
that should be preserved or also unified. `scripts/normalize_fonts.py`
does the actual normalization: it walks `docDefaults`, every named style,
and every direct run override, sets ascii/hAnsi to the target font, and
by default leaves `HTML Preformatted` / `HTML Preformatted Char` /
`HTML Code` alone so code keeps reading as code. Run it as the last step,
after the body swap and any diagram embedding, so it also catches
whatever fonts your own newly-inserted content ended up inheriting.

```
python3 scripts/normalize_fonts.py INPUT.docx OUTPUT.docx --target Aptos
```

Note that LibreOffice-rendered PDF previews will silently substitute a
fallback for a font like Aptos that isn't installed on the rendering
machine -- don't take "the PDF preview didn't visibly change" as evidence
the font change failed. Verify by reading the docx XML directly (dump
every style's font and scan for stray direct-run `w:rFonts` values other
than the target/code fonts) rather than trusting the preview for this
specific check.

## PlantUML / ASCII-art diagrams: re-express, don't dump as text

READMEs routinely contain "diagrams" that are really just text: fenced
PlantUML source (` ```plantuml ... @startuml/@enduml` `), or hand-drawn
ASCII boxes-and-arrows (box-drawing characters, `+--+`, `-->`, tree
listings). Dumping these into the deliverable as monospace preformatted
text is technically faithful but reads poorly in a formal Word document --
the whole point of a diagram is to be looked at, not parsed as text. Treat
any such block as something to make genuinely graphical, not literal text
to preserve verbatim.

This environment had no working PlantUML renderer (no `plantuml.jar`
available locally, and typical PlantUML rendering hosts like
`plantuml.com` were not reachable from the sandbox) -- do not assume you
can just shell out to `plantuml`. What did work reliably: **read the
diagram source yourself, understand what it's actually depicting (which
entities/steps exist, how they connect, what the labels mean), and
re-express it as a small Graphviz DOT file**, then render it with the
`dot` CLI (already available) and embed the resulting PNG with
`docx_tools.insert_image()`. This is more reliable than trying to write a
generic ASCII-art-to-diagram parser -- arbitrary box-drawing layouts are a
hard parsing problem, but *understanding* a diagram you can read and
redrawing it cleanly is straightforward. If a real PlantUML/Mermaid
renderer or a network-reachable rendering service *is* available in a
given environment, use it directly instead -- Graphviz-by-hand is the
fallback that's guaranteed to work anywhere, not the only approach.

Practical workflow for each diagram-like code block found in the markdown:

1. Read the block and identify: the nodes/entities, the labelled edges
   between them, and any obvious grouping (e.g. "these 5 steps sit inside
   one control-plane box" → a Graphviz `subgraph cluster_...`).
2. Write a `.dot` file describing the same structure -- keep styling
   simple and consistent (rounded boxes, one accent colour, `fontname
   "Arial"`) so diagrams across the document look like they belong
   together, not like a grab-bag of default Graphviz output.
3. Render it: `dot -Tpng -Gdpi=170 diagram.dot -o diagram.png` (170 dpi
   reads crisply at typical Word page widths without producing an
   oversized file; adjust if the target page is unusually narrow/wide).
4. Look at the PNG before wiring it in -- Graphviz's automatic layout
   occasionally produces awkward crossings or cramped labels; tweak
   `rankdir`, node order, or edge labels and re-render rather than
   accepting a messy first attempt.
5. Find the contiguous run of preformatted paragraphs in the docx that
   held the original block (same `HTML Preformatted`-style scan you used
   for `dump_structure()`), delete that whole run, and splice in the image
   with `insert_image()` at that exact spot -- see `run/replace_diagrams.py`
   in this skill's history for a worked pattern (locate by the block's
   first line of text, delete the contiguous preformatted run, insert the
   image where it stood).
6. Do this as a separate pass *after* the main markdown→docx body swap,
   not fused into the generic block renderer -- each diagram needs
   individual judgment about what it's depicting, so it doesn't belong in
   `build_deliverable.py`'s generic, no-editorial-judgment code path.

Not every fenced code block is a diagram -- genuine code/config/data
examples (JSON payloads, Turtle/RDF triples, shell commands, directory
layout listings shown as an example of files-on-disk rather than a
picture of a process) should stay as text in the code/preformatted style.
Use judgment: if the block's *purpose* is to show a picture of a process,
architecture, or relationship graph, make it a picture.

## Bundled tools

- `scripts/docx_tools.py` -- template-agnostic python-docx helpers:
  `dump_structure()` for profiling, `find_paragraph()` for locating anchors,
  `delete_between()` for wiping a body region while keeping its bounding
  headings, and `insert_heading()` / `insert_paragraph()` /
  `insert_list_item()` / `insert_ordered_item()` / `insert_code_block()` /
  `insert_table()` / `insert_caption()` / `insert_image()` for splicing in
  new content one block at a time via a "cursor" element you thread through
  the calls.
  Also has `render_inline()` which turns `**bold**`, `*italic*`,
  `` `code` ``, and `[text](url)` (real hyperlinks) into proper runs --
  use it for every piece of text that came from markdown.
- `scripts/md_parser.py` -- a deliberately non-fancy markdown block parser
  (headings, bold-only pseudo-headings, fenced code blocks, pipe tables,
  bullet/ordered lists with soft-wrap continuation handling, blockquotes,
  images, horizontal rules, paragraphs). It does not resolve inline
  formatting -- that happens at render time via `render_inline()`.
- `scripts/build_deliverable.py` -- the CLI driver that ties the two
  together for the common case of "replace everything between anchor A and
  anchor B with the (optionally sliced) markdown". Read its module
  docstring for the full config schema before writing a config file.
- `scripts/normalize_fonts.py` -- walks `docDefaults`, every named style,
  and every direct run-level override to put the whole document on one
  target font, skipping monospace/code styles by default. Run it last.

## Things that bit us building this -- watch for them

- **Soft-wrapped list items.** README authors routinely wrap long bullet
  text across two physical lines without repeating the `-`/`1.` marker.
  `md_parser.py` already folds such continuation lines into the previous
  item, but if you extend the parser, don't regress this -- it silently
  turns one bullet into "one short bullet + a stray orphan paragraph" and
  breaks ordered-list numbering (each split fragment restarts at 1) if you
  don't handle it.
- **Markdown links whose label is itself code**, e.g. `` [`repo-name`](url) ``.
  `render_inline()` strips the surrounding backticks/asterisks from the
  link label so you don't end up with literal backticks inside a
  hyperlink's visible text.
- **A markdown table's column count won't match any existing template
  table**, so don't try to deep-clone a specific table's XML as a
  one-size-fits-all shape -- build a fresh table at the right size with
  `doc.add_table()` and just apply the template's table *style name* to
  it (`insert_table()` already does this).
- **Ordered lists are rendered as manual `"1. "` prefixes**, not real Word
  auto-numbering. This is a deliberate simplification: wiring up a
  decimal-numbering definition that's guaranteed to exist (or safely
  creating a new one) in an arbitrary template is a lot of fragile XML
  work for a cosmetic win. If the user specifically wants auto-renumbering
  ordered lists, that's a reasonable follow-up request, not something to
  assume by default.
- **Don't "fix" inconsistencies in the source markdown** (duplicate section
  numbers, typos, mismatched deliverable references) while translating it
  -- carry them through faithfully and call them out in your summary. The
  user owns the content; you own getting it into the right container.

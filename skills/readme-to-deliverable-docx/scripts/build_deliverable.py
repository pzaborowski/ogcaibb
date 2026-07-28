#!/usr/bin/env python3
"""
Render a markdown source document into the BODY of an existing Word
deliverable template, leaving the template's cover page, executive summary,
table of contents, definitions table, references table, and appendix intact.

This script does no editorial thinking of its own -- all judgment calls
(which docx style plays which role, where the replaceable body starts/ends,
how many leading markdown headings to drop because the title already lives
on the cover page, etc.) are captured in a small config JSON that should be
written after actually reading the template with docx_tools.dump_structure()
and reading the markdown source. See SKILL.md for the full workflow this
script is one step of.

Usage:
    python build_deliverable.py \
        --md README.md \
        --template template.docx \
        --config config.json \
        --out output.docx

config.json shape:
{
  "style_map": {              // markdown heading level (as string) -> docx style name
    "1": "Heading 1",
    "2": "Appendix Header H2",
    "3": "Appendix Header H3",
    "4": "Appendix Header H3"
  },
  "body_style": "Normal (Web)",
  "code_style": "HTML Preformatted",
  "caption_style": "Caption",
  "bullet_style": "List Paragraph",
  "bullet_numids": [12, 14, 15],       // one per nesting level, cycles if list is deeper
  "bullet_ilvl_mode": "numid_per_level",  // or "ilvl_increment" to keep one numId and bump ilvl instead
  "table_style": "Table Grid",
  "auto_caption_tables": false,
  "auto_caption_figures": false,
  "render_hr": false,
  "promote_bold_only_heading_level": 2,
  "skip_leading_md_headings": 1,
  "start_anchor": {"style": "Heading 1", "text_exact": "Introduction"},
  "end_anchor":   {"style": "Heading 1", "text_exact": "Conclusions"}
}
"""
import argparse
import json
import sys

import docx_tools as dt
import md_parser


def heading_style_for_level(style_map, level):
    """Walk down from the requested level to find the closest defined style
    (5 falls back to 4, then 3, ... then body-bold if nothing is defined)."""
    for lvl in range(level, 0, -1):
        if str(lvl) in style_map:
            return style_map[str(lvl)], False  # False = not a body-bold fallback
    return None, True


def render_blocks(doc, cursor, blocks, cfg):
    body_style = cfg.get("body_style", "Normal")
    code_style = cfg.get("code_style", "Normal")
    caption_style = cfg.get("caption_style", "Caption")
    bullet_style = cfg.get("bullet_style", "List Paragraph")
    bullet_numids = cfg.get("bullet_numids", [1])
    bullet_mode = cfg.get("bullet_ilvl_mode", "numid_per_level")
    table_style = cfg.get("table_style", "Table Grid")
    auto_caption_tables = cfg.get("auto_caption_tables", False)
    auto_caption_figures = cfg.get("auto_caption_figures", False)
    render_hr = cfg.get("render_hr", False)
    style_map = cfg.get("style_map", {})

    table_counter = 0
    figure_counter = 0
    warnings = []
    counts = {}

    for block in blocks:
        btype = block["type"]
        counts[btype] = counts.get(btype, 0) + 1

        if btype == "heading":
            style_name, is_fallback = heading_style_for_level(style_map, block["level"])
            if style_name is None:
                warnings.append(f"No style mapped for heading level {block['level']!r} "
                                 f"({block['text']!r}); rendered as bold body text.")
                cursor = dt.insert_paragraph(cursor, doc, f"**{block['text']}**", body_style)
            else:
                cursor = dt.insert_heading(cursor, doc, block["text"], style_name)

        elif btype == "paragraph":
            cursor = dt.insert_paragraph(cursor, doc, block["text"], body_style)

        elif btype == "blockquote":
            cursor = dt.insert_paragraph(cursor, doc, f"*{block['text']}*", body_style)

        elif btype == "code_block":
            cursor = dt.insert_code_block(cursor, doc, block["lines"], code_style)

        elif btype == "bullet_list":
            for item in block["items"]:
                lvl = item["level"]
                if bullet_mode == "ilvl_increment":
                    num_id = bullet_numids[0]
                    ilvl = min(lvl, 8)
                else:
                    num_id = bullet_numids[min(lvl, len(bullet_numids) - 1)]
                    ilvl = 0
                cursor = dt.insert_list_item(cursor, doc, item["text"], bullet_style, num_id, ilvl)

        elif btype == "ordered_list":
            for idx, item in enumerate(block["items"], start=1):
                cursor = dt.insert_ordered_item(cursor, doc, idx, item["text"], body_style)

        elif btype == "table":
            if auto_caption_tables:
                table_counter += 1
                cursor = dt.insert_caption(cursor, doc, f"Table {table_counter}:", caption_style)
            cursor = dt.insert_table(cursor, doc, block["header"], block["rows"], table_style)

        elif btype == "image":
            figure_counter += 1
            note = f"[TODO: insert figure here — {block['alt'] or block['src']} " \
                   f"(source: {block['src']}) — image file was not available to this conversion]"
            if auto_caption_figures:
                cursor = dt.insert_caption(cursor, doc, f"Figure {figure_counter}:", caption_style)
            cursor = dt.insert_paragraph(cursor, doc, note, body_style)
            warnings.append(f"Image {block['src']!r} needs to be inserted manually near "
                             f"'Figure {figure_counter}'.")

        elif btype == "hr":
            if render_hr:
                cursor = dt.insert_paragraph(cursor, doc, "—" * 20, body_style)

        else:
            warnings.append(f"Unknown block type {btype!r} skipped.")

    return cursor, counts, warnings


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--md", required=True)
    ap.add_argument("--template", required=True)
    ap.add_argument("--config", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    with open(args.config, encoding="utf-8") as f:
        cfg = json.load(f)

    with open(args.md, encoding="utf-8") as f:
        md_text = f.read()

    blocks = md_parser.parse(
        md_text,
        promote_bold_only_heading_level=cfg.get("promote_bold_only_heading_level", 2),
    )

    skip = cfg.get("skip_leading_md_headings", 0)
    dropped = 0
    while skip > 0 and blocks and blocks[0]["type"] == "heading":
        blocks.pop(0)
        dropped += 1
        skip -= 1

    stop_cfg = cfg.get("stop_before_heading")
    if stop_cfg:
        cutoff = None
        for idx, b in enumerate(blocks):
            if b["type"] != "heading":
                continue
            if stop_cfg.get("level") is not None and b["level"] != stop_cfg["level"]:
                continue
            if stop_cfg.get("text_contains") and stop_cfg["text_contains"] not in b["text"]:
                continue
            if stop_cfg.get("text_exact") and b["text"] != stop_cfg["text_exact"]:
                continue
            cutoff = idx
            break
        if cutoff is not None:
            blocks = blocks[:cutoff]

    doc = dt.load(args.template)

    sa = cfg["start_anchor"]
    ea = cfg["end_anchor"]
    start_para = dt.find_paragraph(doc, style=sa.get("style"), text_exact=sa.get("text_exact"),
                                    text_contains=sa.get("text_contains"), nth=sa.get("nth", 1))
    end_para = dt.find_paragraph(doc, style=ea.get("style"), text_exact=ea.get("text_exact"),
                                  text_contains=ea.get("text_contains"), nth=ea.get("nth", 1))
    if start_para is None:
        sys.exit(f"ERROR: start_anchor not found in template: {sa}")
    if end_para is None:
        sys.exit(f"ERROR: end_anchor not found in template: {ea}")

    cursor = dt.delete_between(doc, start_para, end_para, keep_start=True, keep_end=True)

    cursor, counts, warnings = render_blocks(doc, cursor, blocks, cfg)

    dt.save(doc, args.out)

    print(f"Dropped {dropped} leading markdown heading block(s) (title already on cover page).")
    print(f"Rendered blocks: {counts}")
    print(f"Saved to: {args.out}")
    if warnings:
        print(f"\n{len(warnings)} warning(s):")
        for w in warnings:
            print(f" - {w}")


if __name__ == "__main__":
    main()

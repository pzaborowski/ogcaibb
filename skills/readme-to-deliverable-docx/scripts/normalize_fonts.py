#!/usr/bin/env python3
"""
Normalize a docx onto a single target font, without disturbing monospace
code/preformatted styles.

Word deliverables built from a template + years of copy-pasted content
routinely end up with several fonts stacked on top of each other: a
base "Normal" style font, a theme-resolved default for styles that never
set an explicit font, one or more headings with their own hardcoded font,
and (rightly) a monospace font for code/ASCII-art blocks. Simply setting
one style's font does nothing for text that resolves through the theme or
through a *different* style's own explicit font -- you have to walk every
style definition (and docDefaults) to actually get one consistent font.

Usage:
    python3 normalize_fonts.py INPUT.docx OUTPUT.docx [--target Aptos] \
        [--skip-styles "HTML Preformatted,HTML Preformatted Char,HTML Code"]

This only touches font *family* (ascii/hAnsi rFonts attributes) -- it does
not touch size, bold/italic, color, or the "cs" (complex-script) fallback,
so existing emphasis and non-Latin-script fallback behaviour are left
alone.
"""
import argparse
import sys

from docx import Document
from docx.oxml.ns import qn

DEFAULT_SKIP_STYLES = {
    "HTML Preformatted", "HTML Preformatted Char", "HTML Code",
}


def set_rfonts(rpr_parent_el, target_font, tag="w:rFonts"):
    """Ensure rpr_parent_el (an rPr or rPrDefault/rPr element) has a
    w:rFonts child with ascii/hAnsi set to target_font. Leaves 'cs'
    (complex-script fallback) untouched if already present."""
    rf = rpr_parent_el.find(qn(tag))
    if rf is None:
        from docx.oxml import OxmlElement
        rf = OxmlElement(tag)
        rpr_parent_el.append(rf)
    rf.set(qn("w:ascii"), target_font)
    rf.set(qn("w:hAnsi"), target_font)
    # Only touch eastAsia if it was already there, to avoid pulling in an
    # unrelated CJK font substitution policy.
    if rf.get(qn("w:eastAsia")) is not None:
        rf.set(qn("w:eastAsia"), target_font)


def normalize(doc, target_font="Aptos", skip_styles=None):
    skip_styles = skip_styles or DEFAULT_SKIP_STYLES
    changed_styles = []

    # 1. docDefaults -- the fallback for any run with no style/direct font.
    styles_el = doc.styles.element
    doc_defaults = styles_el.find(qn("w:docDefaults"))
    if doc_defaults is not None:
        rpr_default = doc_defaults.find(qn("w:rPrDefault") + "/" + qn("w:rPr"))
        if rpr_default is None:
            rpr_default_parent = doc_defaults.find(qn("w:rPrDefault"))
            if rpr_default_parent is not None:
                from docx.oxml import OxmlElement
                rpr_default = OxmlElement("w:rPr")
                rpr_default_parent.append(rpr_default)
        if rpr_default is not None:
            set_rfonts(rpr_default, target_font)

    # 2. Every named style (paragraph, character, table) except the
    #    explicitly-skipped monospace/code ones.
    for style in doc.styles:
        if style.name in skip_styles:
            continue
        style_el = style.element
        rpr = style_el.find(qn("w:rPr"))
        if rpr is None:
            from docx.oxml import OxmlElement
            rpr = OxmlElement("w:rPr")
            # rPr must come after any pPr in a style definition
            ppr = style_el.find(qn("w:pPr"))
            if ppr is not None:
                ppr.addnext(rpr)
            else:
                style_el.insert(1, rpr)
        set_rfonts(rpr, target_font)
        changed_styles.append(style.name)

    # 3. Direct run-level overrides that aren't inside a skipped style --
    #    these win over style definitions, so they need fixing too.
    def runs_in(paragraphs):
        for p in paragraphs:
            if p.style is not None and p.style.name in skip_styles:
                continue
            for r in p.runs:
                yield r

    count = 0
    for r in runs_in(doc.paragraphs):
        rpr = r._element.find(qn("w:rPr"))
        if rpr is not None and rpr.find(qn("w:rFonts")) is not None:
            set_rfonts(rpr, target_font)
            count += 1
    for t in doc.tables:
        for row in t.rows:
            for cell in row.cells:
                for r in runs_in(cell.paragraphs):
                    rpr = r._element.find(qn("w:rPr"))
                    if rpr is not None and rpr.find(qn("w:rFonts")) is not None:
                        set_rfonts(rpr, target_font)
                        count += 1

    return changed_styles, count


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("output")
    ap.add_argument("--target", default="Aptos")
    ap.add_argument("--skip-styles", default=None,
                     help="Comma-separated style names to leave alone (default: code/preformatted styles)")
    args = ap.parse_args()

    skip = DEFAULT_SKIP_STYLES
    if args.skip_styles is not None:
        skip = {s.strip() for s in args.skip_styles.split(",") if s.strip()}

    doc = Document(args.input)
    changed_styles, run_count = normalize(doc, target_font=args.target, skip_styles=skip)
    doc.save(args.output)

    print(f"Set font to {args.target!r} on {len(changed_styles)} style(s) "
          f"and {run_count} direct run override(s).")
    print(f"Left these styles untouched (monospace/code): {sorted(skip)}")
    print(f"Saved: {args.output}")


if __name__ == "__main__":
    main()

"""
Low-level, template-agnostic helpers for surgically editing a .docx that
already has a fixed front matter / back matter (cover page, TOC, executive
summary, definitions table, references table, appendix, etc.) and needs its
BODY replaced or extended with freshly generated content, while keeping the
document's own paragraph/table/list styles.

These helpers never guess which style means what -- that decision belongs to
whoever calls them (a human, or Claude reading the output of dump_structure()
and the markdown source). This module just makes the mechanical XML surgery
safe and repeatable.

Typical flow (see build_deliverable.py for the full driver):

    doc = load(path)
    print(dump_structure(doc))               # figure out style names + anchors
    start = find_paragraph(doc, style="Heading 1", text_exact="Introduction")
    end = find_paragraph(doc, style="Heading 1", text_exact="Conclusions")
    cursor = delete_between(doc, start, end)  # wipes old body, returns cursor
    cursor = insert_heading(cursor, "Some Section", "Appendix Header H2")
    cursor = insert_paragraph(cursor, "Some body text.", "Normal (Web)")
    save(doc, out_path)
"""
import copy
import re

from docx import Document
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
from docx.text.paragraph import Paragraph
from docx.table import Table
from docx.oxml.shared import OxmlElement as OE

INLINE_RE = re.compile(
    r"(\*\*\*(?P<bolditalic>.+?)\*\*\*"
    r"|\*\*(?P<bold>.+?)\*\*"
    r"|\*(?P<italic>.+?)\*"
    r"|`(?P<code>.+?)`"
    r"|\[(?P<linktext>[^\]]+)\]\((?P<linkurl>[^)]+)\))"
)


# ---------------------------------------------------------------- load/save
def load(path):
    return Document(path)


def save(doc, path):
    doc.save(path)


# --------------------------------------------------------------- inspection
def dump_structure(doc, max_text=100):
    """Human/Claude-readable dump of every paragraph and table in document
    order, with index, style name, list-numbering info, and a text preview.
    Read this BEFORE writing a style_map / config -- don't guess style
    names, look them up here."""
    lines = []
    body = doc.element.body
    p_idx = 0
    t_idx = 0
    for child in body.iterchildren():
        if child.tag == qn("w:p"):
            para = Paragraph(child, doc)
            style = para.style.name if para.style else ""
            numPr = child.find(qn("w:pPr") + "/" + qn("w:numPr")) if child.find(qn("w:pPr")) is not None else None
            num_info = ""
            pPr = child.find(qn("w:pPr"))
            if pPr is not None:
                numPr = pPr.find(qn("w:numPr"))
                if numPr is not None:
                    ilvl_el = numPr.find(qn("w:ilvl"))
                    numid_el = numPr.find(qn("w:numId"))
                    ilvl = ilvl_el.get(qn("w:val")) if ilvl_el is not None else "?"
                    numid = numid_el.get(qn("w:val")) if numid_el is not None else "?"
                    num_info = f" [list ilvl={ilvl} numId={numid}]"
            text = para.text.replace("\n", " ")[:max_text]
            lines.append(f"p{p_idx}\t[{style}]{num_info}\t{text!r}")
            p_idx += 1
        elif child.tag == qn("w:tbl"):
            table = Table(child, doc)
            style_name = table.style.name if table.style else ""
            dims = f"{len(table.rows)}x{len(table.columns)}"
            preview_rows = []
            for r in table.rows[:3]:
                preview_rows.append([c.text.strip()[:30] for c in r.cells])
            lines.append(f"t{t_idx}\t<TABLE {dims} style={style_name!r}>\t{preview_rows}")
            t_idx += 1
    return "\n".join(lines)


def list_styles(doc):
    """Return {style_name: style_type_str} for every style defined in the doc."""
    out = {}
    for s in doc.styles:
        out[s.name] = str(s.type)
    return out


def find_paragraph(doc, style=None, text_exact=None, text_contains=None, nth=1):
    """Find the nth (1-based) paragraph matching the given criteria (all
    given criteria must match). Returns a Paragraph or None."""
    count = 0
    for p in doc.paragraphs:
        if style is not None and (p.style is None or p.style.name != style):
            continue
        if text_exact is not None and p.text.strip() != text_exact:
            continue
        if text_contains is not None and text_contains not in p.text:
            continue
        count += 1
        if count == nth:
            return p
    return None


def get_table(doc, index):
    return doc.tables[index]


# ----------------------------------------------------------- body surgery
def delete_between(doc, start_para, end_para, keep_start=True, keep_end=True):
    """Delete every paragraph/table between start_para and end_para
    (document order). If keep_start/keep_end are True (default) the anchor
    paragraphs themselves are preserved; set to False to remove them too.

    Returns the XML element that new content should be inserted after
    (via the insert_* functions below) to land right where the deleted
    content used to be.
    """
    body = doc.element.body
    children = list(body.iterchildren())
    start_el = start_para._p
    end_el = end_para._p
    start_i = children.index(start_el)
    end_i = children.index(end_el)
    if start_i > end_i:
        raise ValueError("start_para must come before end_para in the document")

    to_remove = children[start_i + 1:end_i]
    for el in to_remove:
        el.getparent().remove(el)

    if not keep_start:
        cursor = start_el.getprevious()
        start_el.getparent().remove(start_el)
    else:
        cursor = start_el

    if not keep_end:
        end_el.getparent().remove(end_el)

    return cursor


def delete_paragraph(paragraph):
    p = paragraph._p
    p.getparent().remove(p)


def cursor_of(paragraph_or_table):
    """Get the underlying XML element to use as an insertion cursor from a
    python-docx Paragraph or Table object."""
    if isinstance(paragraph_or_table, Paragraph):
        return paragraph_or_table._p
    if isinstance(paragraph_or_table, Table):
        return paragraph_or_table._tbl
    return paragraph_or_table  # assume already an lxml element


# --------------------------------------------------------------- inserting
def _new_paragraph_after(cursor_el, doc):
    new_p = OxmlElement("w:p")
    cursor_el.addnext(new_p)
    return Paragraph(new_p, doc)


def insert_heading(cursor_el, doc, text, style_name):
    """Insert a heading paragraph right after cursor_el. Returns the new
    paragraph's XML element (use as the next cursor)."""
    para = _new_paragraph_after(cursor_el, doc)
    para.style = style_name
    render_inline(para, text)
    return para._p


def insert_paragraph(cursor_el, doc, text, style_name):
    para = _new_paragraph_after(cursor_el, doc)
    para.style = style_name
    render_inline(para, text)
    return para._p


def insert_code_block(cursor_el, doc, lines, style_name):
    """Insert each line of a fenced code block as its own paragraph in the
    given (monospace / preformatted) style, so line breaks are preserved
    exactly -- important for ascii-art diagrams."""
    cur = cursor_el
    if not lines:
        lines = [""]
    for line in lines:
        para = _new_paragraph_after(cur, doc)
        para.style = style_name
        # code lines are literal -- do not interpret markdown inline syntax
        para.add_run(line)
        cur = para._p
    return cur


def set_bullet_numbering(paragraph, num_id, ilvl=0):
    pPr = paragraph._p.get_or_add_pPr()
    numPr = OxmlElement("w:numPr")
    ilvl_el = OxmlElement("w:ilvl")
    ilvl_el.set(qn("w:val"), str(ilvl))
    numid_el = OxmlElement("w:numId")
    numid_el.set(qn("w:val"), str(num_id))
    numPr.append(ilvl_el)
    numPr.append(numid_el)
    pPr.append(numPr)


def insert_list_item(cursor_el, doc, text, style_name, num_id, ilvl=0):
    para = _new_paragraph_after(cursor_el, doc)
    para.style = style_name
    render_inline(para, text)
    set_bullet_numbering(para, num_id, ilvl)
    return para._p


def insert_ordered_item(cursor_el, doc, index, text, style_name):
    """Ordered list item rendered as manual '<n>. text' -- avoids depending
    on a specific decimal-numbering definition existing in the template,
    which makes this robust across different templates."""
    para = _new_paragraph_after(cursor_el, doc)
    para.style = style_name
    para.add_run(f"{index}. ")
    render_inline(para, text)
    return para._p


def insert_caption(cursor_el, doc, text, style_name):
    para = _new_paragraph_after(cursor_el, doc)
    para.style = style_name
    para.add_run(text)
    return para._p


def insert_image(cursor_el, doc, image_path, width_inches=6.0):
    """Insert a picture right after cursor_el, wrapped in its own paragraph.
    Use this for rendered diagrams (PlantUML/ASCII-art re-expressed as a
    real image via Graphviz or similar) instead of dumping a wall of
    monospace box-drawing characters into the deliverable."""
    from docx.shared import Inches
    para = _new_paragraph_after(cursor_el, doc)
    run = para.add_run()
    run.add_picture(image_path, width=Inches(width_inches))
    return para._p


def insert_table(cursor_el, doc, header, rows, style_name, bold_header=True):
    """Create a new table (using the given table style name) with the given
    header row + data rows, and splice it in right after cursor_el.
    Works for any column count, unlike deep-cloning a specific template
    table's XML."""
    ncols = len(header) if header else (len(rows[0]) if rows else 1)
    nrows = (1 if header else 0) + len(rows)
    table = doc.add_table(rows=max(nrows, 1), cols=ncols)
    try:
        table.style = style_name
    except KeyError:
        pass  # style not found in this template -- fall back to default look

    r = 0
    if header:
        for c, val in enumerate(header):
            cell = table.rows[r].cells[c]
            cell.text = ""
            p = cell.paragraphs[0]
            run = p.add_run(val)
            if bold_header:
                run.bold = True
        r += 1
    for row in rows:
        for c, val in enumerate(row):
            if c >= ncols:
                break
            cell = table.rows[r].cells[c]
            cell.text = ""
            render_inline(cell.paragraphs[0], val)
        r += 1

    tbl_el = table._tbl
    tbl_el.getparent().remove(tbl_el)  # add_table() appended it at doc end
    cursor_el.addnext(tbl_el)
    return tbl_el


# ---------------------------------------------------------------- inline
def add_hyperlink(paragraph, url, text):
    part = paragraph.part
    r_id = part.relate_to(
        url,
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink",
        is_external=True,
    )
    hyperlink = OxmlElement("w:hyperlink")
    hyperlink.set(qn("r:id"), r_id)
    run_el = OxmlElement("w:r")
    rpr = OxmlElement("w:rPr")
    rstyle = OxmlElement("w:rStyle")
    rstyle.set(qn("w:val"), "Hyperlink")
    rpr.append(rstyle)
    run_el.append(rpr)
    t = OxmlElement("w:t")
    t.text = text
    run_el.append(t)
    hyperlink.append(run_el)
    paragraph._p.append(hyperlink)


def render_inline(paragraph, text, code_char_style="HTML Code"):
    """Parse **bold**, *italic*, `code`, [text](url) inside `text` and add
    corresponding runs/hyperlinks to `paragraph`. Plain text in between is
    added as a normal run. Call this instead of paragraph.add_run(text)
    whenever the text came from markdown source."""
    pos = 0
    for m in INLINE_RE.finditer(text):
        if m.start() > pos:
            paragraph.add_run(text[pos:m.start()])
        if m.group("bolditalic") is not None:
            run = paragraph.add_run(m.group("bolditalic"))
            run.bold = True
            run.italic = True
        elif m.group("bold") is not None:
            run = paragraph.add_run(m.group("bold"))
            run.bold = True
        elif m.group("italic") is not None:
            run = paragraph.add_run(m.group("italic"))
            run.italic = True
        elif m.group("code") is not None:
            run = paragraph.add_run(m.group("code"))
            try:
                run.style = code_char_style
            except KeyError:
                run.italic = True
        elif m.group("linktext") is not None:
            link_text = m.group("linktext")
            # strip markdown emphasis/code markers that sometimes wrap the
            # whole link label (e.g. [`repo-name`](url)) -- hyperlinks can't
            # usefully nest another run style through this simple renderer,
            # so just clean the label instead of leaving raw ** / ` in it.
            link_text = re.sub(r"^([*`_]{1,3})(.+)\1$", r"\2", link_text)
            add_hyperlink(paragraph, m.group("linkurl"), link_text)
        pos = m.end()
    if pos < len(text):
        paragraph.add_run(text[pos:])
    if not text:
        pass

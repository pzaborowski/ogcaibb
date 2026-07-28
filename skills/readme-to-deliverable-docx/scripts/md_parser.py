"""
Lightweight Markdown block parser tailored for turning project README-style
documents into a sequence of structural blocks that a Word deliverable
renderer can consume.

It intentionally does NOT try to be a full CommonMark parser. It handles the
constructs that actually show up in engineering README files:

  - ATX headings (# .. ######)
  - "pseudo-headings": a whole line wrapped in **bold** or ***bold italic***
    and nothing else (a common convention for sub-headers in READMEs that
    don't want to bump the real heading level). These are promoted to a
    configurable heading level.
  - fenced code blocks (``` ... ```), including ascii-art diagrams
  - pipe tables (| a | b | \n |---|---| \n | 1 | 2 |)
  - bullet lists (-, *, +), including simple indentation-based nesting
  - ordered lists (1. 2. ...)
  - block quotes (> ...)
  - images ![alt](src)
  - horizontal rules (---, ***, ___ on their own line)
  - plain paragraphs (default; consecutive non-blank lines are joined)

Each block is a dict with a "type" key. See the docstring of parse() for the
exact shapes. Inline markdown (bold/italic/code/links) is left INSIDE the
text of headings/paragraphs/list items/table cells -- it is not resolved
here. Use `docx_tools.render_inline()` at render time to turn `**x**`,
`*x*`, `` `x` `` and `[text](url)` into real formatting/hyperlinks.
"""
import re

FENCE_RE = re.compile(r"^```")
ATX_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
BULLET_RE = re.compile(r"^(\s*)[-*+]\s+(.*)$")
ORDERED_RE = re.compile(r"^(\s*)\d+[.)]\s+(.*)$")
TABLE_ROW_RE = re.compile(r"^\s*\|?(.+\|.+?)\|?\s*$")
TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
HR_RE = re.compile(r"^\s*([-*_])\s*(\1\s*){2,}$")
IMAGE_RE = re.compile(r"^!\[([^\]]*)\]\(([^)]+)\)\s*$")
BOLD_ONLY_RE = re.compile(r"^\*{2,3}([^*].*?)\*{2,3}$")
BLOCKQUOTE_RE = re.compile(r"^>\s?(.*)$")


def _starts_new_block(line, lines, idx):
    """True if `line` looks like the start of a different block type (so a
    list-parsing loop should stop instead of treating it as a wrapped
    continuation of the current item)."""
    if FENCE_RE.match(line) or ATX_RE.match(line) or HR_RE.match(line):
        return True
    if IMAGE_RE.match(line.strip()):
        return True
    if BLOCKQUOTE_RE.match(line):
        return True
    nxt = lines[idx + 1] if idx + 1 < len(lines) else None
    if "|" in line and nxt is not None and TABLE_SEP_RE.match(nxt):
        return True
    return False


def _split_table_row(line):
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [c.strip() for c in line.split("|")]


def parse(text, promote_bold_only_heading_level=2):
    """Parse markdown text into a list of block dicts.

    promote_bold_only_heading_level: if set (e.g. 2), a paragraph line that
    is ENTIRELY wrapped in bold/bold-italic markers (and has no other
    sibling lines in the same paragraph) is treated as a heading at this
    level instead of a normal paragraph. Set to None to disable.
    """
    lines = text.replace("\r\n", "\n").split("\n")
    blocks = []
    i = 0
    n = len(lines)

    def peek(offset=0):
        j = i + offset
        return lines[j] if 0 <= j < n else None

    while i < n:
        line = lines[i]

        if line.strip() == "":
            i += 1
            continue

        # Fenced code block
        if FENCE_RE.match(line):
            lang = line.strip()[3:].strip()
            code_lines = []
            i += 1
            while i < n and not FENCE_RE.match(lines[i]):
                code_lines.append(lines[i])
                i += 1
            i += 1  # skip closing fence
            blocks.append({"type": "code_block", "lang": lang, "lines": code_lines})
            continue

        # ATX heading
        m = ATX_RE.match(line)
        if m:
            blocks.append({"type": "heading", "level": len(m.group(1)), "text": m.group(2).strip()})
            i += 1
            continue

        # Horizontal rule
        if HR_RE.match(line):
            blocks.append({"type": "hr"})
            i += 1
            continue

        # Image on its own line
        m = IMAGE_RE.match(line.strip())
        if m:
            blocks.append({"type": "image", "alt": m.group(1), "src": m.group(2)})
            i += 1
            continue

        # Table: current line has a pipe and next non-empty line is a separator
        nxt = peek(1)
        if "|" in line and nxt is not None and TABLE_SEP_RE.match(nxt):
            header = _split_table_row(line)
            i += 2  # skip header + separator
            rows = []
            while i < n and "|" in lines[i] and lines[i].strip() != "":
                rows.append(_split_table_row(lines[i]))
                i += 1
            blocks.append({"type": "table", "header": header, "rows": rows})
            continue

        # Bullet list (soft-wrapped continuation lines -- a non-blank line
        # that doesn't start a new list item or another block -- are folded
        # into the previous item's text, since README authors routinely
        # wrap long bullet text across lines without repeating the marker)
        m = BULLET_RE.match(line)
        if m:
            items = []
            while i < n:
                cur = lines[i]
                if cur.strip() == "":
                    break
                m = BULLET_RE.match(cur)
                if m:
                    indent, item_text = m.groups()
                    level = len(indent) // 2
                    items.append({"level": level, "text": item_text.strip()})
                    i += 1
                    continue
                if _starts_new_block(cur, lines, i):
                    break
                # continuation of the previous item
                items[-1]["text"] = (items[-1]["text"] + " " + cur.strip()).strip()
                i += 1
            blocks.append({"type": "bullet_list", "items": items})
            continue

        # Ordered list (same soft-wrap handling as bullet lists)
        m = ORDERED_RE.match(line)
        if m:
            items = []
            while i < n:
                cur = lines[i]
                if cur.strip() == "":
                    break
                m = ORDERED_RE.match(cur)
                if m:
                    indent, item_text = m.groups()
                    level = len(indent) // 2
                    items.append({"level": level, "text": item_text.strip()})
                    i += 1
                    continue
                if _starts_new_block(cur, lines, i):
                    break
                items[-1]["text"] = (items[-1]["text"] + " " + cur.strip()).strip()
                i += 1
            blocks.append({"type": "ordered_list", "items": items})
            continue

        # Blockquote
        m = BLOCKQUOTE_RE.match(line)
        if m:
            quote_lines = []
            while i < n:
                m = BLOCKQUOTE_RE.match(lines[i])
                if not m:
                    break
                quote_lines.append(m.group(1))
                i += 1
            blocks.append({"type": "blockquote", "text": " ".join(quote_lines).strip()})
            continue

        # Default: paragraph -- accumulate consecutive plain lines
        para_lines = []
        while i < n:
            l = lines[i]
            if l.strip() == "":
                break
            if (FENCE_RE.match(l) or ATX_RE.match(l) or BULLET_RE.match(l)
                    or ORDERED_RE.match(l) or BLOCKQUOTE_RE.match(l) or HR_RE.match(l)
                    or IMAGE_RE.match(l.strip())):
                break
            nxt2 = lines[i + 1] if i + 1 < n else None
            if "|" in l and nxt2 is not None and TABLE_SEP_RE.match(nxt2):
                break
            para_lines.append(l.strip())
            i += 1

        if len(para_lines) == 1 and promote_bold_only_heading_level:
            bm = BOLD_ONLY_RE.match(para_lines[0])
            if bm:
                blocks.append({
                    "type": "heading",
                    "level": promote_bold_only_heading_level,
                    "text": bm.group(1).strip(),
                })
                continue

        text_joined = " ".join(para_lines).strip()
        if text_joined:
            blocks.append({"type": "paragraph", "text": text_joined})

    return blocks


if __name__ == "__main__":
    import sys
    import json

    path = sys.argv[1]
    with open(path, encoding="utf-8") as f:
        content = f.read()
    result = parse(content)
    print(json.dumps(result, indent=2, ensure_ascii=False))

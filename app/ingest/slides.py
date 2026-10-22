import os
import re
import pymupdf as fitz
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE

from app.db import get_conn


def extract_pdf(path):
    doc = fitz.open(path)
    pages = []
    for i, page in enumerate(doc):
        text = page.get_text("text")
        text = re.sub(r"\s+", " ", text).strip()
        pages.append((i + 1, text))
    doc.close()
    return pages


def _shape_text(shapes, parts):
    """Collect text from shapes, recursing into groups (text boxes inside a
    group were silently dropped)."""
    for shape in shapes:
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            _shape_text(shape.shapes, parts)
            continue
        if shape.has_text_frame:
            for para in shape.text_frame.paragraphs:
                txt = "".join(run.text for run in para.runs).strip()
                if txt:
                    parts.append(txt)
        if getattr(shape, "has_table", False) and shape.has_table:
            for row in shape.table.rows:
                cells = [c.text.strip() for c in row.cells]
                parts.append(" | ".join(cells))


def extract_pptx(path):
    prs = Presentation(path)
    slides = []
    for i, slide in enumerate(prs.slides):
        parts = []
        _shape_text(slide.shapes, parts)
        if slide.has_notes_slide:
            notes = slide.notes_slide.notes_text_frame
            if notes is not None and notes.text.strip():
                parts.append("Speaker notes: " + notes.text.strip())
        text = " ".join(parts)
        text = re.sub(r"\s+", " ", text).strip()
        slides.append((i + 1, text))
    return slides


def slugify(name):
    name = re.sub(r"[^\w\s-]", "", name).replace(" ", "_")
    return name.strip("_")


def extract_slides(path):
    """Parse a PDF/PPTX into [(slide_num, text)]."""
    return extract_pdf(path) if path.lower().endswith(".pdf") else extract_pptx(path)


def import_lecture(path, slides=None):
    """`slides` lets the caller pass an already-parsed deck (upload parses once)."""
    ext = os.path.splitext(path)[1].lower()
    base = os.path.basename(path)
    title = os.path.splitext(base)[0]

    # Slow extraction first, before touching the DB, so no lock is held during it.
    if slides is None:
        slides = extract_slides(path)

    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO lectures(title, filename, source) VALUES(?,?,?)",
        (title, base, "pdf" if ext == ".pdf" else "pptx"),
    )
    lecture_id = cur.lastrowid

    word_count = 0
    for num, text in slides:
        conn.execute(
            "INSERT INTO slides(lecture_id, slide_num, text) VALUES(?,?,?)",
            (lecture_id, num, text),
        )
        word_count += len(text.split())
    conn.execute(
        "UPDATE lectures SET slide_count=?, word_count=? WHERE id=?",
        (len(slides), word_count, lecture_id),
    )
    conn.commit()
    conn.close()

    # Extract images after slides exist (best-effort; never blocks import).
    # OCR is NOT run here: the caller's background _analyze_lecture runs it.
    # Running it here too did every OCR + paid vision call twice per import,
    # and held the upload request open for the whole first pass.
    try:
        from app.ingest.images import extract_lecture_images
        extract_lecture_images(lecture_id)
    except Exception as e:
        print(f"image extraction for lecture {lecture_id} failed: {e}")

    return lecture_id


def list_lectures():
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM lectures ORDER BY created_at DESC"
    ).fetchall()
    conn.close()
    return rows

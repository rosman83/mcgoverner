import os
import re
import shutil
import subprocess
import tempfile
import pymupdf as fitz
from PIL import Image, UnidentifiedImageError
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE

from app.db import get_conn, DB_PATH

IMAGES_ROOT = os.path.join(os.path.dirname(DB_PATH), "images")
PDF_DPI = 140

# Unlike the PDF path (rendered at a controlled 140 DPI above), a PPTX's
# embedded images are the presenter's raw blob - sometimes a multi-thousand-
# pixel screenshot or phone photo pasted at full size. Vision APIs price
# image input by resolution, so an oversized embedded image can cost several
# times what a properly-sized one does for identical content - this was
# silently multiplying the vision-captioning cost (see ocr.py's fallback)
# for image-heavy decks. 1568px is well past what OCR/vision needs for a
# legible slide figure.
MAX_IMAGE_DIM = 1568


def _downscale_if_large(path):
    try:
        with Image.open(path) as img:
            if max(img.size) <= MAX_IMAGE_DIM:
                return
            img.thumbnail((MAX_IMAGE_DIM, MAX_IMAGE_DIM), Image.LANCZOS)
            img.save(path)
    except (UnidentifiedImageError, OSError):
        pass  # not a real/openable image - leave it; OCR/vision will just skip it


def _lecture_dir(lecture_id):
    d = os.path.join(IMAGES_ROOT, str(lecture_id))
    os.makedirs(d, exist_ok=True)
    return d


def _rel_path(abs_path):
    """Path relative to IMAGES_ROOT, so /images/ + rel resolves correctly."""
    return os.path.relpath(abs_path, IMAGES_ROOT)


def _clear_slide_images(conn, lecture_id):
    conn.execute(
        "DELETE FROM slide_images WHERE slide_id IN "
        "(SELECT id FROM slides WHERE lecture_id=?)",
        (lecture_id,),
    )
    # remove orphan files
    d = _lecture_dir(lecture_id)
    if os.path.isdir(d):
        for f in os.listdir(d):
            try:
                os.remove(os.path.join(d, f))
            except OSError:
                pass


def _insert(conn, slide_id, abs_path, kind, seq):
    conn.execute(
        "INSERT INTO slide_images(slide_id, path, kind, seq) VALUES(?,?,?,?)",
        (slide_id, _rel_path(abs_path), kind, seq),
    )


def extract_pdf_images(lecture_id, path, conn=None):
    """Render each PDF page to a full-page JPEG."""
    own_conn = conn is None
    conn = conn or get_conn()
    try:
        slides = conn.execute(
            "SELECT id, slide_num FROM slides WHERE lecture_id=? ORDER BY slide_num",
            (lecture_id,),
        ).fetchall()
        slide_by_num = {r["slide_num"]: r["id"] for r in slides}

        doc = fitz.open(path)
        count = 0
        for pno in range(len(doc)):
            page = doc[pno]
            num = pno + 1
            if num not in slide_by_num:
                continue
            pix = page.get_pixmap(dpi=PDF_DPI)
            dest = os.path.join(_lecture_dir(lecture_id), f"page_{num}.jpg")
            pix.save(dest, jpg_quality=85)
            _insert(conn, slide_by_num[num], dest, "page", 0)
            count += 1
            # Commit per page so the write lock is never held across the slow loop.
            conn.commit()
        doc.close()
        if own_conn:
            conn.commit()
        return count
    finally:
        if own_conn:
            conn.close()


def _iter_picture_shapes(shapes):
    """Yield picture shapes, recursing into groups (best-effort)."""
    for shape in shapes:
        st = shape.shape_type
        if st in (MSO_SHAPE_TYPE.PICTURE, MSO_SHAPE_TYPE.LINKED_PICTURE):
            yield shape
        elif st == MSO_SHAPE_TYPE.GROUP and hasattr(shape, "shapes"):
            yield from _iter_picture_shapes(shape.shapes)


def extract_pptx_images(lecture_id, path, conn=None):
    """Extract embedded images per slide from a PPTX."""
    own_conn = conn is None
    conn = conn or get_conn()
    try:
        slides = conn.execute(
            "SELECT id, slide_num FROM slides WHERE lecture_id=? ORDER BY slide_num",
            (lecture_id,),
        ).fetchall()
        slide_by_num = {r["slide_num"]: r["id"] for r in slides}

        prs = Presentation(path)
        count = 0
        for idx, slide in enumerate(prs.slides):
            num = idx + 1
            if num not in slide_by_num:
                continue
            seq = 0
            for shape in _iter_picture_shapes(slide.shapes):
                try:
                    blob = shape.image.blob
                    ext = shape.image.ext or "png"
                except Exception:
                    continue
                dest = os.path.join(_lecture_dir(lecture_id), f"slide_{num}_{seq}.{ext}")
                with open(dest, "wb") as f:
                    f.write(blob)
                _downscale_if_large(dest)
                _insert(conn, slide_by_num[num], dest, "embedded", seq)
                seq += 1
                count += 1
                # Commit per image so the write lock is never held across the slow loop.
                conn.commit()
        if own_conn:
            conn.commit()
        return count
    finally:
        if own_conn:
            conn.close()


def _soffice():
    for c in (shutil.which("soffice"), shutil.which("libreoffice"),
              "/Applications/LibreOffice.app/Contents/MacOS/soffice",
              r"C:\Program Files\LibreOffice\program\soffice.exe"):
        if c and os.path.exists(c):
            return c


def _pptx_to_pdf(path, outdir):
    """Full-slide PDF via LibreOffice, or None if it isn't installed / fails."""
    exe = _soffice()
    if not exe:
        return None
    try:
        subprocess.run([exe, "--headless", "--convert-to", "pdf", "--outdir", outdir, path],
                       check=True, capture_output=True, timeout=300)
    except (subprocess.SubprocessError, OSError):
        return None
    pdf = os.path.join(outdir, os.path.splitext(os.path.basename(path))[0] + ".pdf")
    return pdf if os.path.exists(pdf) else None


def extract_lecture_images(lecture_id):
    """Extract images for a lecture. Returns (count, source_type)."""
    conn = get_conn()
    row = conn.execute(
        "SELECT filename, source FROM lectures WHERE id=?", (lecture_id,)
    ).fetchone()
    if not row:
        conn.close()
        raise ValueError(f"No lecture {lecture_id}")
    _clear_slide_images(conn, lecture_id)
    conn.commit()

    lectures_dir = os.path.join(os.path.dirname(DB_PATH), "..", "lectures")
    filepath = os.path.join(lectures_dir, row["filename"])
    if row["source"] == "pdf":
        count = extract_pdf_images(lecture_id, filepath, conn=conn)
        kind = "page"
    else:
        # Prefer rendering whole slides (what people expect to see); fall back to
        # embedded pictures when LibreOffice is missing or page count doesn't line up.
        # ponytail: hidden slides can skew page count -> falls back to embedded pics.
        with tempfile.TemporaryDirectory() as tmp:
            pdf = _pptx_to_pdf(filepath, tmp)
            if pdf and len(fitz.open(pdf)) == len(Presentation(filepath).slides):
                count = extract_pdf_images(lecture_id, pdf, conn=conn)
                kind = "page"
            else:
                count = extract_pptx_images(lecture_id, filepath, conn=conn)
                kind = "embedded"
    conn.commit()
    conn.close()
    return count, kind


def images_for_lecture(lecture_id):
    """Images grouped by slide, for the summary gallery."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT si.*, s.slide_num FROM slide_images si "
        "JOIN slides s ON s.id = si.slide_id "
        "WHERE s.lecture_id=? ORDER BY s.slide_num, si.seq",
        (lecture_id,),
    ).fetchall()
    conn.close()
    grouped = {}
    for r in rows:
        grouped.setdefault(r["slide_num"], []).append(
            {"path": "/images/" + r["path"], "kind": r["kind"]}
        )
    return [{"slide_num": k, "images": v} for k, v in sorted(grouped.items())]


def images_for_slides(slide_ids):
    """Image URLs for a list of slide ids (for question source material)."""
    return [u for urls in images_by_slide(slide_ids).values() for u in urls]


def images_by_slide(slide_ids):
    """{slide_id: [image URLs]} in one query - callers listing many slides used to
    open a connection per row."""
    slide_ids = [i for i in slide_ids if i is not None]
    if not slide_ids:
        return {}
    conn = get_conn()
    ph = ",".join("?" * len(slide_ids))
    rows = conn.execute(
        f"SELECT slide_id, path FROM slide_images WHERE slide_id IN ({ph}) "
        "ORDER BY slide_id, seq",
        tuple(slide_ids),
    ).fetchall()
    conn.close()
    out = {}
    for r in rows:
        out.setdefault(r["slide_id"], []).append("/images/" + r["path"])
    return out

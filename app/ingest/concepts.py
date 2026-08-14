import re
import json

from app.db import get_conn

MIN_WORDS = 8
MAX_CONCEPT_WORDS = 400
TITLE_MAX_WORDS = 25

# Header/footer boilerplate that adds no conceptual value
BOILERPLATE_PATTERNS = [
    r"\bObjectives?\b",
    r"\bLearning Objectives\b",
    r"\bLECTURE OBJECTIVES\b",
    r"\bOverview\s*:?\s*$",
    r"\bUSMLE\b",
    r"\bImage resources?\b",
    r"\bCopyright\b",
    r"\bMcGovern\b",
    r"\bUTHealth\b",
    r"\bProfessor\b",
    r"\bDr\.?\s+[A-Z]",
    r"\bDr\s+[A-Z]",
    r"\bMed School\b",
    r"\bLecturer\b",
    r"Fall\s*['`]\d{2}",
    r"resources?\s*•",
]

# Content indicators that suggest a concept is meaningful (not just a header)
CONTENT_INDICATORS = [
    r"\b(is|are|was|were|occurs?|occurs? in|called|defined|referred to|leads? to|causes?|results? in|consists? of|contains?|lined by|composed of)\b",
    r"\b(stages?|steps?|types?|categories?|pathway|mechanism|process|formation|functions?|functions of)\b",
]


def _is_boilerplate(text):
    import re as _re
    for pat in BOILERPLATE_PATTERNS:
        if _re.search(pat, text, _re.IGNORECASE):
            return True
    return False


def _is_resource_list(text):
    """Detect a pure image-resource / reference-list concept (book cites, URLs, '•' items)."""
    import re as _re
    t = text.lower()
    if "image resource" in t or "mage resources" in t or "reference" in t:
        # contains links/books/& bullets and no content indicators
        has_link = ("http" in t or ".com" in t or "et al" in t or "edition" in t or "•" in t)
        has_content = bool(_re.search(r"\b(is|are|occur|called|function|structure|cell|tissue|pathway)\b", t))
        if has_link and not has_content:
            return True
    return False


def _leading_boilerplate(text):
    """Strip a leading boilerplate token run (professor names, school headers, objectives header).
    Returns (cleaned_text, found_bool)."""
    import re as _re
    cleaned = text.strip()
    found = False
    # keep stripping known leading headers
    patterns = [
        r"^Dr\.?\s+[A-Z][\w.\-]+(?:\s+[A-Z][\w.\-]+)*\s+(Professor|Lecturer|Dept\.?|Department)",
        r"^Dr\.?\s+[A-Z][\w.\-]+(?:\s+[A-Z][\w.\-]+)*\s*,?\s*[A-Z]",
        r"^Dr\s+[A-Z][\w.\-]+",
        r"^[A-Za-z\s&.'-]*?(Professor|Dept\.?|Department)\s+of\s+[A-Za-z\s&]+",
        r"^[A-Za-z\.\s&,'-]+?\s+Professor of\s+",
        r"^[A-Za-z]+\.?\s+[A-Z][a-z]+,\s+[A-Za-z]+ Med School",
        r"^[A-Z]\.?\s*[A-Za-z]+\s*,?\s+McGovern",
        r"^[A-Za-z]+\.?\s*[A-Za-z]*\s+McGovern Med School",
        r"^\S+\s+Med School",
        r"^(Objectives?:?\s*[\d.]*\s*)+",
        r"^(LECTURE\s+)?OBJECTIVES?:?\s*[\d.]*\s*",
        r"^(Learning\s+)?Objectives?\s*",
        r"^Image resources?\s*:?",
        r"^USMLE( Content| Step)?\s*:?",
        r"^Copyright\s+",
        r"^Overview\s*:?\s*$",
        r"^[^,]*?,\s*(Fall|Spring|Summer)\s*['`]\d{2}",
        r"^\S+\s+Med School,\s*Fall\s*['`]\d{2}",
        r"^(Fall|Spring|Summer|Winter)\s*[‘'`]?\d{2}\s*",
        r"^R\.?\s*[A-Z][a-z]+(?:,?\s+[A-Z][a-z]+)*\s*,?\s*McGovern",
    ]
    for _ in range(4):
        for pat in patterns:
            m = _re.match(pat, cleaned, _re.IGNORECASE)
            if m:
                cleaned = cleaned[m.end():].strip()
                found = True
                break
        else:
            break
    # clean residual artifacts from OCR/header concatenation
    cleaned = _re.sub(r"^\s*[,\-•;:]\s*", "", cleaned)
    cleaned = _re.sub(r"\bUSMLE( Content| Step)?\b", "", cleaned, flags=_re.IGNORECASE)
    cleaned = _re.sub(r"\s{2,}", " ", cleaned).strip()
    return cleaned, found


def _has_content(text):
    """A slide/concept is content if it has sentence-like structure beyond a header."""
    import re as _re
    for pat in CONTENT_INDICATORS:
        if _re.search(pat, text, _re.IGNORECASE):
            return True
    # has sentence punctuation
    if _re.search(r"[.!?]", text):
        return True
    return False


def _looks_like_title(text):
    """Heuristic: short text with no sentence punctuation is likely a slide title."""
    if len(text.split()) >= TITLE_MAX_WORDS:
        return False
    import re
    has_sentence = re.search(r"[.!?]\s*$", text)
    return not has_sentence


def split_concepts(slide_text):
    """Split a slide's text into concept-sized chunks.

    Priority: numbered/lettered list items break into separate concepts.
    Otherwise keep sentences; merge short ones up to a cap.
    """
    text = slide_text.strip()
    if not text:
        return []

    candidates = []
    numbered = re.split(r"(?<=\d|[a-z])\.\s+(?=[A-Z0-9])", text)
    if len(numbered) > 1:
        candidates = [c.strip() for c in numbered if c.strip()]
    else:
        sentences = re.split(r"(?<=[.!?])\s+", text)
        candidates = [s.strip() for s in sentences if s.strip()]

    concepts = []
    buf = ""
    for c in candidates:
        if len(buf.split()) + len(c.split()) <= MAX_CONCEPT_WORDS:
            buf = " ".join([buf, c]).strip()
        else:
            if len(buf.split()) >= MIN_WORDS:
                concepts.append(buf)
            buf = c
    if buf and len(buf.split()) >= MIN_WORDS:
        concepts.append(buf)
    return concepts


def concept_label(text):
    # strip leading boilerplate for a clean short label
    import re as _re
    cleaned, _ = _leading_boilerplate(text)
    if not cleaned:
        cleaned = _re.sub(r"^\s*(Objectives?:?\s*[\d.]*\s*)+", "", text)
    words = cleaned.split()
    label = " ".join(words[:14])
    if len(words) > 14:
        label += "..."
    return label or text[:40]


def chunk_lecture(lecture_id):
    conn = get_conn()
    slides = conn.execute(
        "SELECT id, slide_num, text FROM slides WHERE lecture_id=? ORDER BY slide_num",
        (lecture_id,),
    ).fetchall()

    for slide in slides:
        if _looks_like_title(slide["text"]):
            continue
        if _is_boilerplate(slide["text"]) and not _has_content(slide["text"]):
            continue
        if _is_resource_list(slide["text"]):
            continue
        parts = split_concepts(slide["text"])
        for part in parts:
            if _is_boilerplate(part) and not _has_content(part):
                continue
            if _is_resource_list(part):
                continue
            conn.execute(
                "INSERT INTO concepts(lecture_id, label, slide_ids, slide_nums) "
                "VALUES(?,?,?,?)",
                (
                    lecture_id,
                    concept_label(part),
                    json.dumps([slide["id"]]),
                    json.dumps([slide["slide_num"]]),
                ),
            )
    conn.commit()
    conn.close()


def coverage_stats():
    """Slide-based coverage: how many content slides have at least one question."""
    conn = get_conn()
    total = conn.execute(
        "SELECT COUNT(*) c FROM slides "
        "WHERE (length(trim(text)) >= 15 OR length(trim(ocr_text)) >= 15)"
    ).fetchone()["c"]
    covered = conn.execute(
        "SELECT COUNT(DISTINCT q.slide_id) c FROM questions q WHERE q.slide_id IS NOT NULL"
    ).fetchone()["c"]
    conn.close()
    return {
        "total": total,
        "covered": covered,
        "question_coverage": round(100 * covered / total, 1) if total else 0,
    }


def clean_lecture_concepts(lecture_id):
    """Remove boilerplate concepts that have no questions; relabel the rest.
    Returns (deleted, relabeled)."""
    conn = get_conn()
    concepts = conn.execute(
        "SELECT c.* FROM concepts c WHERE c.lecture_id=? ORDER BY c.id", (lecture_id,)
    ).fetchall()
    deleted = 0
    relabeled = 0
    for c in concepts:
        has_q = conn.execute(
            "SELECT COUNT(*) c FROM questions WHERE concept_ids LIKE ?",
            (f"%{c['id']}%",),
        ).fetchone()["c"]
        cleaned_label, had_header = _leading_boilerplate(c["label"])
        # Pure noise: header stripped leaves nothing meaningful, and no questions
        if (not cleaned_label or _is_resource_list(c["label"])) and not has_q:
            conn.execute("DELETE FROM concepts WHERE id=?", (c["id"],))
            deleted += 1
            continue
        # Prefer a clean reasoned caption as the label when available
        slide = None
        try:
            sids = json.loads(c["slide_ids"] or "[]")
            if sids:
                slide = conn.execute(
                    "SELECT caption FROM slides WHERE id=?", (sids[0],)
                ).fetchone()
        except (ValueError, TypeError):
            slide = None
        caption = (slide["caption"] or "").strip() if slide else ""
        if caption and caption.lower() != c["label"].lower():
            conn.execute(
                "UPDATE concepts SET label=? WHERE id=?", (caption[:120], c["id"])
            )
            relabeled += 1
            continue
        # Header-led but has content: relabel to content-only
        new_label = concept_label(c["label"]) if had_header else None
        if new_label and new_label != c["label"]:
            conn.execute(
                "UPDATE concepts SET label=? WHERE id=?", (new_label, c["id"])
            )
            relabeled += 1
        elif had_header and cleaned_label == c["label"]:
            # header found but nothing stripped (e.g. mid-string); try full relabel
            new_label = concept_label(c["label"])
            if new_label != c["label"]:
                conn.execute(
                    "UPDATE concepts SET label=? WHERE id=?", (new_label, c["id"])
                )
                relabeled += 1
    conn.commit()
    conn.close()
    return {"deleted": deleted, "relabeled": relabeled}

"""Strip running headers/footers from VLM OCR output using Docling's furniture detection.

A general VLM (e.g. Qwen3.6-35B) transcribes body content and tables well but cannot
reliably suppress repeated page furniture by prompting alone — running titles, journal
lines, DOIs/URLs and page numbers leak in. Docling's layout model, by contrast, routes
that furniture out of the document body almost perfectly. This module keeps the VLM's
full-page text and only removes the lines Docling flags as page_header/page_footer/
page_number, getting the best of both.

On olmOCR-bench (150-doc sample, 2026-05-24) this lifted tuned Qwen3.6-35B from 72.3%
to 80.9% Overall (headers_footers 17.6% -> 83.8%) with tables/math unchanged.

One Docling-standard JSON call per document classifies layout (do_ocr=false is instant on
born-digital PDFs; we retry with OCR only when there is no text layer). The strip itself is
pure string post-processing and never touches the model.
"""
import difflib
import json
import logging
import re

import httpx

logger = logging.getLogger(__name__)

_HF_LABELS = ("page_header", "page_footer", "page_number")


def furniture_by_page(filepath: str, docling_url: str) -> dict[int, list[str]]:
    """Return {page_no (1-based): [furniture text strings]} via one Docling call.

    Docling labels furniture elements page_header/page_footer/page_number; they appear
    in the document `body` (the JSON `furniture` group is usually empty). do_ocr=false is
    instant on born-digital docs; retry with OCR when the doc has no text layer (scanned).
    """
    url = f"{docling_url.rstrip('/')}/v1/convert/file"

    def fetch(do_ocr: bool) -> dict | None:
        with open(filepath, "rb") as f:
            files = {"files": (filepath.split("/")[-1], f)}
            data = {
                "to_formats": "json",
                "pipeline": "standard",
                "do_ocr": str(do_ocr).lower(),
                "do_table_structure": "false",
            }
            with httpx.Client(timeout=600) as client:
                resp = client.post(url, files=files, data=data)
                resp.raise_for_status()
        jc = resp.json().get("document", {}).get("json_content")
        return json.loads(jc) if isinstance(jc, str) else jc

    jc = fetch(False)
    if len((jc or {}).get("texts", []) or []) < 2:
        jc = fetch(True)
    if not jc:
        return {}

    def resolve(ref: dict) -> dict:
        coll, idx = ref["$ref"].lstrip("#/").split("/")
        return jc[coll][int(idx)]

    out: dict[int, list[str]] = {}
    for group in ("furniture", "body"):
        node = jc.get(group) or {}
        for child in node.get("children") or []:
            try:
                el = resolve(child)
            except Exception:
                continue
            if el.get("label") not in _HF_LABELS:
                continue
            text = (el.get("text") or "").strip()
            if not text:
                continue
            page_no = ((el.get("prov") or [{}])[0].get("page_no")) or 1
            out.setdefault(int(page_no), []).append(text)
    return out


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _strip_md(line: str) -> str:
    s = line.strip()
    s = re.sub(r"^#{1,6}\s*", "", s)   # ATX headings
    s = s.strip("|").strip()           # table-row pipes
    s = re.sub(r"[*_`]", "", s)        # emphasis markers
    return s.strip()


def strip_furniture(md: str, furn: list[str]) -> str:
    """Remove lines from `md` that match Docling's furniture strings for the page.

    'strong' furniture (>=8 alnum chars incl. a letter — running titles, journal lines,
    DOIs, URLs) removes any line that fuzzy-matches it anywhere (handles the VLM splitting
    one header across two lines). 'weak' furniture (short/numeric — page numbers) removes
    an exactly-matching short line only within the first/last 3 lines, so a numeric table
    cell mid-page is never touched.
    """
    nfurn = [_norm(f) for f in furn if _norm(f)]
    if not nfurn:
        return md
    strong = [n for n in nfurn if len(n) >= 8 and re.search(r"[a-z]", n)]
    weak = [n for n in nfurn if n not in strong]
    lines = md.split("\n")
    n = len(lines)
    kept: list[str] = []
    for i, line in enumerate(lines):
        nl = _norm(_strip_md(line))
        if not nl:
            kept.append(line)
            continue
        drop = any(
            s in nl or nl in s or difflib.SequenceMatcher(None, s, nl).ratio() > 0.85
            for s in strong
        )
        if not drop and (i < 3 or i > n - 4) and len(nl) <= 6 and nl in weak:
            drop = True
        if not drop:
            kept.append(line)
    result = re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()
    # Never strip a page down to nothing: on a sparse page (e.g. an archival cover
    # whose only text is a title Docling labels page_header) the whole body can match
    # furniture. Keep the original rather than emit an empty page.
    if not re.search(r"[a-z0-9]", result, re.I) and re.search(r"[a-z0-9]", md, re.I):
        return md.strip()
    return result


def strip_furniture_pages(
    page_texts: list[str], filepath: str, docling_url: str
) -> list[str]:
    """Strip Docling-detected furniture from each page's OCR text.

    A failed Docling lookup leaves the OCR untouched (the model output is still valid),
    so enabling this option can only ever remove furniture, never lose a page.
    """
    if not docling_url:
        return page_texts
    try:
        by_page = furniture_by_page(filepath, docling_url)
    except Exception as exc:
        logger.warning("furniture lookup failed (%s); leaving OCR unmodified", exc)
        return page_texts
    if not by_page:
        return page_texts
    return [strip_furniture(t, by_page.get(i + 1, [])) for i, t in enumerate(page_texts)]

import io
import zipfile

import pytest

from server import extract_text_by_extension


def _minimal_pdf_with_text(text: str) -> bytes:
    """
    Minimal valid PDF containing one page and a simple text draw.
    This avoids extra dependencies like reportlab.
    """
    # Simple PDF with a single content stream using Helvetica.
    # Note: This is intentionally tiny; extraction libraries should still return the text.
    content = f"BT /F1 24 Tf 72 720 Td ({text}) Tj ET"
    objects = []
    offsets = []

    def add_obj(obj: str):
        offsets.append(sum(len(o.encode("latin-1")) for o in objects) + len(header.encode("latin-1")))
        objects.append(obj)

    header = "%PDF-1.4\n"
    add_obj("1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n")
    add_obj("2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n")
    add_obj(
        "3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        "/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>\nendobj\n"
    )
    add_obj("4 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n")
    stream_bytes = content.encode("latin-1")
    add_obj(f"5 0 obj\n<< /Length {len(stream_bytes)} >>\nstream\n{content}\nendstream\nendobj\n")

    xref_start = len(header.encode("latin-1")) + sum(len(o.encode("latin-1")) for o in objects)
    xref = ["xref\n0 6\n0000000000 65535 f \n"]
    for off in offsets:
        xref.append(f"{off:010d} 00000 n \n")
    trailer = (
        "trailer\n<< /Size 6 /Root 1 0 R >>\n"
        f"startxref\n{xref_start}\n%%EOF\n"
    )
    pdf = header + "".join(objects) + "".join(xref) + trailer
    return pdf.encode("latin-1")


def test_extract_text_by_extension_pdf_returns_text():
    pdf_bytes = _minimal_pdf_with_text("PIXLS PDF TEST")
    text = extract_text_by_extension("resume.pdf", pdf_bytes)
    assert isinstance(text, str)
    assert "pixls" in text.lower()


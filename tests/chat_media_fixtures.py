"""Synthetic office/image/PDF samples; no user data or network."""

from io import BytesIO
from pathlib import Path
import zipfile
from PIL import Image
from openpyxl import Workbook


def png():
    out = BytesIO()
    Image.new("RGB", (120, 80), "red").save(out, format="PNG")
    return out.getvalue()


def docx():
    out = BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
        )
        archive.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>中文成本报告 Alpha 1234</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>项目甲</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>99.5</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>',
        )
    return out.getvalue()


def xlsx():
    out = BytesIO()
    book = Workbook()
    sheet = book.active
    sheet.title = "成本明细"
    sheet.append(["项目", "金额"])
    sheet.append(["项目甲", 1234])
    sheet.append(["合计", "=SUM(B2:B2)"])
    book.save(out)
    book.close()
    return out.getvalue()


def pdf():
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 300] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    stream = b"BT /F1 18 Tf 20 200 Td (PDF Alpha cost 1234) Tj ET"
    objects.append(
        b"<< /Length "
        + str(len(stream)).encode()
        + b" >>\nstream\n"
        + stream
        + b"\nendstream"
    )
    raw = b"%PDF-1.4\n"
    offsets = [0]
    for i, obj in enumerate(objects, 1):
        offsets.append(len(raw))
        raw += str(i).encode() + b" 0 obj\n" + obj + b"\nendobj\n"
    offset = len(raw)
    raw += b"xref\n0 6\n0000000000 65535 f \n" + b"".join(
        f"{p:010} 00000 n \n".encode() for p in offsets[1:]
    )
    return (
        raw
        + b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n"
        + str(offset).encode()
        + b"\n%%EOF\n"
    )


def scanned_pdf():
    out = BytesIO()
    Image.new("RGB", (120, 80), "blue").save(out, format="PDF")
    return out.getvalue()


def samples():
    result = {
        "report.pdf": pdf(),
        "report.docx": docx(),
        "report.xlsx": xlsx(),
        "picture.png": png(),
        "scanned.pdf": scanned_pdf(),
    }
    fixture = Path(__file__).parent / "fixtures/chat-media/example.xls"
    if fixture.is_file():
        result["report.xls"] = fixture.read_bytes()
    return result

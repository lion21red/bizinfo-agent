"""Word(.docx, 구버전 .doc) 첨부파일에서 텍스트를 추출하는 유틸.
자격요건표가 본문 중간의 표 안에 들어있는 경우가 흔해서, 문단만 읽는
python-docx의 기본 doc.paragraphs(표를 건너뜀)로는 정보가 빠진다.
문서 body를 문단/표 등장 순서 그대로 순회해 표 셀 내용도 함께 담는다."""

import io
import shutil
import subprocess
import tempfile
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

# 일부 환경은 soffice가 PATH에 없어서, 흔한 설치 경로도 함께 찾아본다.
_SOFFICE_CANDIDATES = [
    "soffice",
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
]


def extract_docx_text(file_bytes: bytes) -> str:
    doc = Document(io.BytesIO(file_bytes))
    parts = []
    for child in doc.element.body.iterchildren():
        if child.tag == qn("w:p"):
            p = Paragraph(child, doc)
            if p.text.strip():
                parts.append(p.text)
        elif child.tag == qn("w:tbl"):
            table = Table(child, doc)
            for row in table.rows:
                cells = [c.text.strip() for c in row.cells]
                if any(cells):
                    parts.append(" | ".join(cells))
    return "\n".join(parts)


def _find_soffice() -> str | None:
    for candidate in _SOFFICE_CANDIDATES:
        path = shutil.which(candidate) if not candidate.endswith(".exe") else (
            candidate if Path(candidate).exists() else None
        )
        if path:
            return path
    return None


def extract_doc_text(file_bytes: bytes) -> str:
    """구버전 .doc(바이너리)은 python-docx가 못 읽어서, 로컬에 LibreOffice가
    설치되어 있으면 이를 이용해 .docx로 변환한 뒤 같은 방식으로 추출한다.
    LibreOffice가 없는 환경에서는 조용히 빈 문자열을 반환한다(다른 첨부파일
    형식 미지원 처리와 동일하게, 전체 파싱 흐름을 막지 않기 위함)."""
    soffice = _find_soffice()
    if not soffice:
        return ""

    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "input.doc"
        src.write_bytes(file_bytes)
        subprocess.run(
            [soffice, "--headless", "--convert-to", "docx", "--outdir", tmp, str(src)],
            capture_output=True, timeout=60, check=True,
        )
        converted = Path(tmp) / "input.docx"
        if not converted.exists():
            return ""
        return extract_docx_text(converted.read_bytes())

"""HWP/HWPX(아래아한글) 문서에서 본문 텍스트를 추출하는 유틸리티.

공고문 붙임의 신청서·사업계획서 양식은 대개 문서 뒷부분에 있어서, 한컴오피스가 저장할 때
만들어 두는 '미리보기 텍스트'(첫 한두 쪽 분량)만으로는 양식을 읽을 수 없다. 그래서 본문 전체를
읽고, 본문을 읽을 수 없는 문서(배포용·암호 문서 등)만 미리보기 텍스트로 대신한다.
  - .hwpx: ZIP 안의 Contents/section*.xml (표는 행마다 "칸 | 칸" 한 줄로)
  - .hwp (OLE 복합문서): BodyText/Section* 스트림의 문단 텍스트 레코드
pyhwp 같은 설치가 까다로운 라이브러리 없이 표준 라이브러리 + olefile만으로 동작한다.
"""

import io
import re
import struct
import zipfile
import zlib
from xml.etree import ElementTree

import olefile


def extract_hwp_text(file_bytes: bytes) -> str:
    """HWP 또는 HWPX 바이트에서 본문 텍스트를 추출한다. 실패 시 빈 문자열 반환."""
    try:
        return _extract_hwpx(file_bytes)
    except (zipfile.BadZipFile, KeyError):
        pass

    try:
        return _extract_hwp(file_bytes)
    except Exception:
        return ""


# ---------------------------------------------------------------- HWPX

def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _cell_text(tc) -> str:
    parts = []
    for el in tc.iter():
        if _local(el.tag) == "t":
            parts.append("".join(el.itertext()))
        elif _local(el.tag) == "p" and parts and not parts[-1].endswith(" "):
            parts.append(" ")
    return re.sub(r"\s+", " ", "".join(parts)).strip()


def _hwpx_table(tbl, out: list[str]):
    for tr in tbl:
        if _local(tr.tag) != "tr":
            continue
        cells = [_cell_text(tc) for tc in tr if _local(tc.tag) == "tc"]
        if any(cells):
            out.append(" | ".join(cells))


def _hwpx_walk(node, out: list[str]):
    for child in node:
        tag = _local(child.tag)
        if tag == "tbl":
            _hwpx_table(child, out)
        elif tag == "p":
            line = []
            for run in child:
                for el in run:
                    el_tag = _local(el.tag)
                    if el_tag == "t":
                        line.append("".join(el.itertext()))
                    elif el_tag == "tbl":
                        # 문단 안에 들어 있는 표: 앞 글자를 먼저 내보내고 표를 행 단위로 적는다
                        if "".join(line).strip():
                            out.append("".join(line).strip())
                        line = []
                        _hwpx_table(el, out)
            if "".join(line).strip():
                out.append("".join(line).strip())
        else:
            _hwpx_walk(child, out)


def _extract_hwpx(file_bytes: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(file_bytes)) as z:
        names = z.namelist()
        sections = sorted(
            (n for n in names if re.fullmatch(r"Contents/section\d+\.xml", n)),
            key=lambda n: int(re.search(r"\d+", n.rsplit("/", 1)[-1]).group()),
        )
        lines: list[str] = []
        for name in sections:
            _hwpx_walk(ElementTree.fromstring(z.read(name)), lines)
        body = "\n".join(lines).strip()
        if body:
            return body
        return z.read("Preview/PrvText.txt").decode("utf-8", errors="ignore")


# ---------------------------------------------------------------- HWP (5.x 바이너리)

HWPTAG_PARA_TEXT = 0x10 + 51
# 문단 텍스트 안의 제어 문자: 이 코드들은 1글자, 나머지 제어 문자(표·그림 등 개체)는 8글자(16바이트)를 차지한다
_SINGLE_WCHAR_CONTROLS = {0, 10, 13} | set(range(24, 32))


def _para_text(data: bytes) -> str:
    chars, i = [], 0
    while i + 1 < len(data):
        code = struct.unpack_from("<H", data, i)[0]
        if code >= 32:
            chars.append(chr(code))
            i += 2
        elif code in _SINGLE_WCHAR_CONTROLS:
            if code == 10:
                chars.append("\n")
            elif code >= 24:
                chars.append(" ")
            i += 2
        else:
            if code == 9:
                chars.append("\t")
            i += 16
    return "".join(chars).strip()


def _records(data: bytes):
    i = 0
    while i + 4 <= len(data):
        header = struct.unpack_from("<I", data, i)[0]
        tag, size = header & 0x3FF, (header >> 20) & 0xFFF
        i += 4
        if size == 0xFFF:
            size = struct.unpack_from("<I", data, i)[0]
            i += 4
        yield tag, data[i:i + size]
        i += size


def _extract_hwp(file_bytes: bytes) -> str:
    with olefile.OleFileIO(io.BytesIO(file_bytes)) as ole:
        flags = struct.unpack_from("<I", ole.openstream("FileHeader").read(), 36)[0]
        compressed, encrypted, distribution = flags & 1, flags & 2, flags & 4
        sections = sorted(
            (e for e in ole.listdir() if len(e) == 2 and e[0] == "BodyText" and e[1].startswith("Section")),
            key=lambda e: int(e[1][len("Section"):] or 0),
        )
        lines = []
        if not (encrypted or distribution):
            for entry in sections:
                data = ole.openstream(entry).read()
                if compressed:
                    data = zlib.decompress(data, -15)
                for tag, payload in _records(data):
                    if tag == HWPTAG_PARA_TEXT:
                        text = _para_text(payload)
                        if text:
                            lines.append(text)
        if lines:
            return "\n".join(lines)
        # 배포용·암호 문서는 본문이 암호화돼 있어 미리보기 텍스트로 대신한다
        if ole.exists("PrvText"):
            return ole.openstream("PrvText").read().decode("utf-16le", errors="ignore")
        return ""

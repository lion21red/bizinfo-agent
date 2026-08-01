"""HWP/HWPX(아래아한글) 문서에서 텍스트를 추출하는 유틸리티.

한글 문서는 본문 전체를 파싱하는 대신, 한컴오피스가 저장 시 자동으로
만들어 두는 '미리보기 텍스트'를 읽는다.
  - .hwpx: ZIP 압축 내부의 Preview/PrvText.txt
  - .hwp (구버전, OLE 복합문서): 'PrvText' 스트림 (UTF-16LE 인코딩)

전체 본문은 아니지만 공고문 서두(사업 개요, 신청 자격 등 핵심 내용)가
포함되어 있어 자격요건 분석에는 충분한 경우가 많다. pyhwp 같은 무거운/설치가
까다로운 라이브러리 없이 표준 zipfile + olefile만으로 동작한다.
"""

import io
import zipfile

import olefile


def extract_hwp_text(file_bytes: bytes) -> str:
    """HWP 또는 HWPX 바이트에서 미리보기 텍스트를 추출한다. 실패 시 빈 문자열 반환."""
    try:
        return _extract_hwpx(file_bytes)
    except (zipfile.BadZipFile, KeyError):
        pass

    try:
        return _extract_hwp(file_bytes)
    except Exception:
        return ""


def _extract_hwpx(file_bytes: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(file_bytes)) as z:
        return z.read("Preview/PrvText.txt").decode("utf-8", errors="ignore")


def _extract_hwp(file_bytes: bytes) -> str:
    with olefile.OleFileIO(io.BytesIO(file_bytes)) as ole:
        if not ole.exists("PrvText"):
            return ""
        data = ole.openstream("PrvText").read()
        return data.decode("utf-16le", errors="ignore")

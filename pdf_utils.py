"""PDF에서 텍스트 또는(텍스트 추출이 부실한 스캔본/이미지 기반 문서의 경우)
페이지 이미지를 추출하는 공용 유틸리티. app.py(기업 서류 업로드)와
parser.py(공고 첨부파일 분석)에서 공통으로 사용한다."""

import io

import pdfplumber

MAX_IMAGE_PAGES = 10
AVG_CHARS_PER_PAGE_THRESHOLD = 150


def extract_pdf_content(file_obj_or_bytes, max_image_pages: int = MAX_IMAGE_PAGES):
    """PDF에서 텍스트를 추출한다. 페이지당 평균 텍스트 분량이 매우 적으면
    (보안뷰어로 내보낸 스캔본 등) 페이지를 이미지로 렌더링해 함께 반환한다
    (Gemini Vision으로 분석하기 위함).

    반환값: (full_text: str, page_images: list[PIL.Image])
    """
    if isinstance(file_obj_or_bytes, (bytes, bytearray)):
        file_obj_or_bytes = io.BytesIO(file_obj_or_bytes)

    text_parts = []
    page_images = []
    with pdfplumber.open(file_obj_or_bytes) as pdf:
        for page in pdf.pages:
            # dedupe_chars: 보안 워터마크 등으로 글자가 겹쳐 찍혀 중복 추출되는 문제 보정
            text_parts.append(page.dedupe_chars().extract_text() or "")
        full_text = "\n".join(text_parts)

        # 페이지당 평균 텍스트 분량이 매우 적으면 스캔본/이미지 기반 문서로 판단한다.
        # 전체 합산 길이로 비교하면 페이지 수가 많을 때 반복되는 워터마크 문구만으로도
        # 임계값을 넘어버려 오판하므로 페이지당 평균을 쓴다.
        avg_len_per_page = len(full_text.replace("\n", "").strip()) / max(len(pdf.pages), 1)
        if avg_len_per_page < AVG_CHARS_PER_PAGE_THRESHOLD:
            for page in pdf.pages[:max_image_pages]:
                page_images.append(page.to_image(resolution=150).original)

    return full_text, page_images

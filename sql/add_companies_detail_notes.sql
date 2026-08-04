-- 매칭에는 쓰이지 않지만, 나중에 신청서/사업계획서 초안 작성(application_writer.py)에
-- 참고자료로 재사용할 수 있도록 회사 개요·연혁·제품/서비스·강점 등 자유 서술 요약을 보관하는 컬럼.
-- 서류 업로드나 AI 웹 검색으로 얻은 상세 내용을 여기 저장해두면, 신청서 작성 페이지에서
-- 매번 다시 자료를 올리지 않아도 자동으로 불러와 활용된다.
ALTER TABLE companies
ADD COLUMN IF NOT EXISTS detail_notes TEXT;

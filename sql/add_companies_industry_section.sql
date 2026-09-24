-- 기업 업종을 한국표준산업분류(KSIC) 대분류 코드(A~U 한 글자)로 저장한다.
-- 공고의 업종 제한(parsed_data.industry_sections)과 같은 코드 체계로 비교해서,
-- "전지 및 케이블 도매업"(G)인 기업이 "일반음식점 한정"(I) 공고에 매칭되는 오류를 걸러낸다.
ALTER TABLE companies
ADD COLUMN IF NOT EXISTS industry_section TEXT;

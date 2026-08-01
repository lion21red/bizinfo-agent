-- 중복 저장된 기업 중 회사명별로 가장 최근 것만 남기고 정리
DELETE FROM companies a
USING companies b
WHERE a.company_name = b.company_name
  AND a.created_at < b.created_at;

-- 같은 회사명으로 다시 저장하면 새로 추가되지 않고 기존 값을 덮어쓰도록 유니크 제약 추가
ALTER TABLE companies ADD CONSTRAINT companies_company_name_key UNIQUE (company_name);

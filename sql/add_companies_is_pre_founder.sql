-- 설립일 정보 없음(정보 미확인)과 예비창업자(아직 사업자등록 전)를 구분하기 위한 컬럼.
-- 이전에는 establishment_date가 비어있으면 곧바로 예비창업자로 간주했으나,
-- 설립일을 단순히 확인하지 못한 경우와 실제 예비창업자인 경우를 구분해야 하므로
-- 명시적으로 확인된 경우에만 true가 되는 별도 플래그를 추가한다.
ALTER TABLE companies
ADD COLUMN IF NOT EXISTS is_pre_founder BOOLEAN DEFAULT FALSE;

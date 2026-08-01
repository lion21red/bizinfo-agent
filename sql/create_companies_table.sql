-- 기업 프로필 저장용 테이블
CREATE TABLE IF NOT EXISTS companies (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_name TEXT,
    establishment_date DATE,
    region TEXT,
    industry TEXT,
    annual_revenue BIGINT DEFAULT 0,
    employee_count INT DEFAULT 0,
    is_venture BOOLEAN DEFAULT FALSE,
    is_female_owned BOOLEAN DEFAULT FALSE,
    patent_count INT DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

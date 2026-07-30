-- VidhiMitra Database Schema
-- Run this in Supabase SQL Editor

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Table 1: Government Documents
CREATE TABLE IF NOT EXISTS documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title TEXT NOT NULL,
    doc_type TEXT NOT NULL,
    source_url TEXT NOT NULL,
    published_date DATE,
    text_content TEXT,
    pdf_link TEXT,
    ministry TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE(source_url)
);

CREATE INDEX IF NOT EXISTS idx_documents_doc_type ON documents(doc_type);
CREATE INDEX IF NOT EXISTS idx_documents_published_date ON documents(published_date DESC);
CREATE INDEX IF NOT EXISTS idx_documents_title_search ON documents USING gin(to_tsvector('english', title));

-- Table 2: Scholarships
CREATE TABLE IF NOT EXISTS scholarships (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    scheme_name TEXT NOT NULL,
    provider TEXT NOT NULL,
    category TEXT[] NOT NULL DEFAULT '{}',
    education_level TEXT[] NOT NULL DEFAULT '{}',
    max_family_income NUMERIC,
    min_marks NUMERIC,
    amount NUMERIC,
    application_deadline DATE,
    status TEXT DEFAULT 'Open',
    application_link TEXT,
    description TEXT,
    UNIQUE(scheme_name, provider)
);

CREATE INDEX IF NOT EXISTS idx_scholarships_status ON scholarships(status);
CREATE INDEX IF NOT EXISTS idx_scholarships_category ON scholarships USING gin(category);
CREATE INDEX IF NOT EXISTS idx_scholarships_education_level ON scholarships USING gin(education_level);
CREATE INDEX IF NOT EXISTS idx_scholarships_deadline ON scholarships(application_deadline);

-- Table 3: User Sessions (for eligibility flow)
CREATE TABLE IF NOT EXISTS user_sessions (
    id TEXT PRIMARY KEY,
    data JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

-- Table 4: Response Cache
CREATE TABLE IF NOT EXISTS response_cache (
    query_hash TEXT PRIMARY KEY,
    response JSONB NOT NULL,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_cache_created ON response_cache(created_at);

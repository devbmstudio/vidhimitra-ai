-- Run in Supabase SQL Editor (https://supabase.com/dashboard/project/vplzxrzoovtjvdksegbx/sql/new)
CREATE TABLE IF NOT EXISTS process_guides (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    category TEXT,
    state TEXT,
    tags TEXT[],
    eligibility TEXT,
    documents_needed TEXT[],
    step_by_step TEXT[],
    where_to_apply TEXT,
    portal TEXT,
    common_problems TEXT[],
    validity TEXT,
    fees TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

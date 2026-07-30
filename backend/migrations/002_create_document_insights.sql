-- Run this in Supabase SQL Editor (https://supabase.com/dashboard/project/vplzxrzoovtjvdksegbx/sql/new)
CREATE TABLE IF NOT EXISTS document_insights (
    id SERIAL PRIMARY KEY,
    scheme_name TEXT,
    doc_type TEXT,
    amount TEXT,
    application_deadline TEXT,
    portal TEXT,
    provider TEXT,
    category TEXT,
    education_level TEXT,
    state TEXT,
    description TEXT,
    user_confirmed BOOLEAN DEFAULT false,
    count INTEGER DEFAULT 1,
    first_seen TIMESTAMPTZ DEFAULT NOW(),
    last_seen TIMESTAMPTZ DEFAULT NOW(),
    promoted BOOLEAN DEFAULT false,
    UNIQUE(scheme_name, provider, amount)
);

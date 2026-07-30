-- Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- Document chunks table for RAG vector search
CREATE TABLE IF NOT EXISTS document_chunks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID,
    content TEXT NOT NULL,
    section_heading TEXT,
    portal TEXT,
    chunk_index INTEGER NOT NULL DEFAULT 0,
    embedding vector(384),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
);

-- HNSW index for fast cosine similarity search
CREATE INDEX IF NOT EXISTS idx_document_chunks_embedding
    ON document_chunks
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- Index for filtering by document
CREATE INDEX IF NOT EXISTS idx_document_chunks_document_id
    ON document_chunks (document_id);

-- Index for filtering by portal + section
CREATE INDEX IF NOT EXISTS idx_document_chunks_portal_section
    ON document_chunks (portal, section_heading);

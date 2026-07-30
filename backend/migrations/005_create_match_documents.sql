-- Cosine similarity search on document_chunks with optional filters
CREATE OR REPLACE FUNCTION match_documents(
    query_embedding vector(384),
    match_threshold float DEFAULT 0.3,
    match_count int DEFAULT 5,
    filter_portal text DEFAULT NULL,
    filter_section text DEFAULT NULL
)
RETURNS TABLE(
    id uuid,
    content text,
    section_heading text,
    portal text,
    chunk_index int,
    document_id uuid,
    similarity float,
    document_title text,
    document_source_url text
)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    SELECT
        dc.id,
        dc.content,
        dc.section_heading,
        dc.portal,
        dc.chunk_index,
        dc.document_id,
        1 - (dc.embedding <=> query_embedding) AS similarity,
        d.title AS document_title,
        d.source_url AS document_source_url
    FROM document_chunks dc
    LEFT JOIN documents d ON d.id = dc.document_id
    WHERE 1 - (dc.embedding <=> query_embedding) > match_threshold
      AND (filter_portal IS NULL OR dc.portal = filter_portal)
      AND (filter_section IS NULL OR dc.section_heading = filter_section)
    ORDER BY dc.embedding <=> query_embedding
    LIMIT match_count;
END;
$$;

-- Expression indexes used by PostgresRetrievalData's `simple` text search.
-- Partial predicates match its archive filters; archived content is not searched.
CREATE INDEX idx_chat_messages_search_vector
    ON chat_messages USING GIN (to_tsvector('simple'::regconfig, content))
    WHERE archived_at IS NULL;

CREATE INDEX idx_project_memory_search_vector
    ON project_memory USING GIN (to_tsvector('simple'::regconfig, value))
    WHERE archived_at IS NULL;

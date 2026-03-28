CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS runs (
    id UUID PRIMARY KEY,
    run_type TEXT NOT NULL,
    run_date DATE NOT NULL,
    status TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS provider_calls (
    id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    provider_id TEXT NOT NULL,
    endpoint_type TEXT NOT NULL,
    request_fingerprint TEXT NOT NULL,
    freshness_class TEXT NOT NULL,
    cost_usd NUMERIC(12, 6),
    latency_ms INTEGER,
    request_json JSONB NOT NULL,
    response_json JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS provider_calls_fingerprint_idx
    ON provider_calls (provider_id, endpoint_type, request_fingerprint, freshness_class);

CREATE TABLE IF NOT EXISTS cassette_records (
    id UUID PRIMARY KEY,
    provider_call_id UUID NOT NULL REFERENCES provider_calls(id) ON DELETE CASCADE,
    replayable BOOLEAN NOT NULL DEFAULT TRUE,
    replay_count INTEGER NOT NULL DEFAULT 0,
    cost_avoided_usd NUMERIC(12, 6) NOT NULL DEFAULT 0,
    latency_saved_ms BIGINT NOT NULL DEFAULT 0,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS eval_cache_entries (
    id UUID PRIMARY KEY,
    run_id UUID REFERENCES runs(id) ON DELETE CASCADE,
    cache_key TEXT NOT NULL UNIQUE,
    artifact_type TEXT NOT NULL,
    payload JSONB NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS pinned_queries (
    id UUID PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,
    query_text TEXT NOT NULL,
    topic_id TEXT NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    exclude_from_query_eval BOOLEAN NOT NULL DEFAULT TRUE,
    cadence TEXT NOT NULL DEFAULT 'daily',
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS query_executions (
    id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    query_class TEXT NOT NULL,
    query_text TEXT NOT NULL,
    pinned_query_id UUID REFERENCES pinned_queries(id) ON DELETE SET NULL,
    provider_id TEXT NOT NULL,
    cost_usd NUMERIC(12, 6),
    raw_hit_count INTEGER NOT NULL DEFAULT 0,
    canonical_hit_count INTEGER NOT NULL DEFAULT 0,
    novelty_yield INTEGER NOT NULL DEFAULT 0,
    frequency_yield INTEGER NOT NULL DEFAULT 0,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS canonical_items (
    id UUID PRIMARY KEY,
    source_id TEXT NOT NULL UNIQUE,
    item_type TEXT NOT NULL,
    canonical_url TEXT,
    title TEXT NOT NULL,
    published_at TIMESTAMPTZ,
    content_mode TEXT,
    review_value TEXT,
    user_utility TEXT,
    validation_status TEXT,
    perishability_score NUMERIC(8, 4),
    source_authority_score NUMERIC(8, 4),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ideas (
    id UUID PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    topic_id TEXT NOT NULL,
    embedding VECTOR(1536),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS graph_nodes (
    id UUID PRIMARY KEY,
    node_type TEXT NOT NULL,
    node_key TEXT NOT NULL UNIQUE,
    label TEXT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS graph_edges (
    id UUID PRIMARY KEY,
    edge_type TEXT NOT NULL,
    from_node_id UUID NOT NULL REFERENCES graph_nodes(id) ON DELETE CASCADE,
    to_node_id UUID NOT NULL REFERENCES graph_nodes(id) ON DELETE CASCADE,
    confidence NUMERIC(8, 4),
    first_seen_at TIMESTAMPTZ,
    last_seen_at TIMESTAMPTZ,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS graph_edges_type_idx ON graph_edges (edge_type);
CREATE INDEX IF NOT EXISTS graph_edges_from_idx ON graph_edges (from_node_id);
CREATE INDEX IF NOT EXISTS graph_edges_to_idx ON graph_edges (to_node_id);
CREATE INDEX IF NOT EXISTS graph_edges_metadata_gin_idx ON graph_edges USING GIN (metadata);

CREATE TABLE IF NOT EXISTS novelty_labels (
    id UUID PRIMARY KEY,
    canonical_item_id UUID NOT NULL REFERENCES canonical_items(id) ON DELETE CASCADE,
    label TEXT NOT NULL,
    model_id TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    retrieval_policy_version TEXT NOT NULL,
    confidence NUMERIC(8, 4),
    rationale TEXT,
    evidence JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS idea_frequency_labels (
    id UUID PRIMARY KEY,
    idea_id UUID NOT NULL REFERENCES ideas(id) ON DELETE CASCADE,
    topic_id TEXT NOT NULL,
    window_days INTEGER NOT NULL,
    label TEXT NOT NULL,
    raw_mention_count INTEGER NOT NULL DEFAULT 0,
    distinct_source_count INTEGER NOT NULL DEFAULT 0,
    distinct_provider_count INTEGER NOT NULL DEFAULT 0,
    acceleration NUMERIC(12, 6),
    burstiness NUMERIC(12, 6),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS provisional_goldens (
    id UUID PRIMARY KEY,
    golden_type TEXT NOT NULL,
    subject_ids JSONB NOT NULL,
    evidence JSONB NOT NULL,
    confidence NUMERIC(8, 4),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS confirmed_goldens (
    id UUID PRIMARY KEY,
    provisional_golden_id UUID REFERENCES provisional_goldens(id) ON DELETE SET NULL,
    golden_type TEXT NOT NULL,
    verdict TEXT NOT NULL,
    evidence JSONB NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

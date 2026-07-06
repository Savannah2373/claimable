-- Claimable schema (brief §3): opportunities → documents → chunks/criteria,
-- profiles → analyses → verdicts. One Postgres holds relational data, dense
-- vectors (pgvector), and BM25-style full text (tsvector).

CREATE EXTENSION IF NOT EXISTS vector;

-- ---------------------------------------------------------------------------
-- Opportunity index: anything a user can be matched to — a grant NOFO, a
-- benefit program, an SBA program. One row per (source, source_id).
-- ---------------------------------------------------------------------------
CREATE TABLE opportunities (
    id              BIGSERIAL PRIMARY KEY,
    source          TEXT NOT NULL,              -- 'grants.gov' | 'sam.gov' | 'policy'
    source_id       TEXT NOT NULL,              -- id at the source
    number          TEXT,                       -- e.g. NOFO number ED-GRANTS-070126-001
    title           TEXT NOT NULL,
    agency_code     TEXT,
    agency_name     TEXT,
    status          TEXT,                       -- posted | forecasted | closed | archived
    open_date       DATE,
    close_date      DATE,
    synopsis        TEXT,
    applicant_types TEXT[],                     -- eligibility hint from source metadata
    categories      TEXT[],
    raw             JSONB NOT NULL,             -- full source payload, never lossy
    -- Hybrid search: dense (pgvector) + full-text. Dim 384 = BAAI/bge-small-en-v1.5
    -- (local dev model; swap to 1024/bge-m3 via TEI in deployment — eval will compare).
    embedding       vector(384),
    tsv             tsvector GENERATED ALWAYS AS (
                        to_tsvector('english', title || ' ' || coalesce(synopsis, ''))
                    ) STORED,
    first_seen_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source, source_id)
);

CREATE INDEX opportunities_status_close_idx ON opportunities (status, close_date);
CREATE INDEX opportunities_embedding_idx ON opportunities
    USING hnsw (embedding vector_cosine_ops);
CREATE INDEX opportunities_tsv_idx ON opportunities USING gin (tsv);

-- ---------------------------------------------------------------------------
-- Source documents: NOFO attachment PDFs, policy manuals, regulation sections.
-- A document may belong to an opportunity (NOFO) or stand alone (SNAP manual).
-- ---------------------------------------------------------------------------
CREATE TABLE documents (
    id              BIGSERIAL PRIMARY KEY,
    opportunity_id  BIGINT REFERENCES opportunities(id) ON DELETE CASCADE,
    kind            TEXT NOT NULL,              -- 'nofo' | 'policy_manual' | 'regulation'
    title           TEXT,
    url             TEXT,
    content_sha256  TEXT,                       -- change detection → drift monitor
    storage_path    TEXT,                       -- local/object-store path to the file
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Retrieval chunks: hybrid search = dense (pgvector) + full-text (tsvector).
-- Embedding dim 384 = BAAI/bge-small-en-v1.5 (see note on opportunities.embedding).
-- ---------------------------------------------------------------------------
CREATE TABLE doc_chunks (
    id              BIGSERIAL PRIMARY KEY,
    document_id     BIGINT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    chunk_index     INT NOT NULL,
    page            INT,
    text            TEXT NOT NULL,
    embedding       vector(384),
    tsv             tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED,
    UNIQUE (document_id, chunk_index)
);

CREATE INDEX doc_chunks_embedding_idx ON doc_chunks
    USING hnsw (embedding vector_cosine_ops);
CREATE INDEX doc_chunks_tsv_idx ON doc_chunks USING gin (tsv);

-- ---------------------------------------------------------------------------
-- Compiled criteria: the Criteria Compiler's output. Atomic, testable, and
-- citation-linked. Versioned: a rule change supersedes the old row rather
-- than mutating it, so past verdicts stay auditable.
-- ---------------------------------------------------------------------------
CREATE TABLE criteria (
    id                 BIGSERIAL PRIMARY KEY,
    opportunity_id     BIGINT NOT NULL REFERENCES opportunities(id) ON DELETE CASCADE,
    criterion_key      TEXT NOT NULL,           -- stable key across versions, e.g. 'applicant_type'
    version            INT NOT NULL DEFAULT 1,
    text               TEXT NOT NULL,           -- plain-English atomic requirement
    category           TEXT,                    -- financial | geographic | organizational | documentation
    check_type         TEXT NOT NULL,           -- 'deterministic' | 'judgment'
    threshold          JSONB,                   -- structured params for deterministic checks
    source_document_id BIGINT REFERENCES documents(id),
    source_page        INT,
    source_quote       TEXT NOT NULL,           -- exact sentence(s) this was compiled from
    effective_from     TIMESTAMPTZ NOT NULL DEFAULT now(),
    superseded_at      TIMESTAMPTZ,             -- NULL = current version
    UNIQUE (opportunity_id, criterion_key, version)
);

CREATE INDEX criteria_current_idx ON criteria (opportunity_id)
    WHERE superseded_at IS NULL;

-- ---------------------------------------------------------------------------
-- Profiles: synthetic personas (individual or organization). No real PII.
-- ---------------------------------------------------------------------------
CREATE TABLE profiles (
    id          BIGSERIAL PRIMARY KEY,
    kind        TEXT NOT NULL,                  -- 'individual' | 'organization'
    name        TEXT NOT NULL,
    attrs       JSONB NOT NULL DEFAULT '{}',    -- structured facts gathered by intake agent
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Analyses & verdicts: one analysis = one (profile, opportunity) run.
-- Verdict per criterion; 'needs_info' carries the follow-up question that
-- routes back to the intake agent.
-- ---------------------------------------------------------------------------
CREATE TABLE analyses (
    id              BIGSERIAL PRIMARY KEY,
    profile_id      BIGINT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    opportunity_id  BIGINT NOT NULL REFERENCES opportunities(id) ON DELETE CASCADE,
    status          TEXT NOT NULL DEFAULT 'running',  -- running | complete | stale
    model           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE verdicts (
    id                  BIGSERIAL PRIMARY KEY,
    analysis_id         BIGINT NOT NULL REFERENCES analyses(id) ON DELETE CASCADE,
    criterion_id        BIGINT NOT NULL REFERENCES criteria(id),
    verdict             TEXT NOT NULL,          -- 'met' | 'not_met' | 'needs_info'
    reasoning           TEXT,
    citation_quote      TEXT,                   -- rule text the verdict rests on
    verified            BOOLEAN NOT NULL DEFAULT FALSE,  -- passed the verifier agent
    follow_up_question  TEXT,                   -- set when verdict = 'needs_info'
    UNIQUE (analysis_id, criterion_id)
);

-- SB&P Engagement Letter Pipeline — database schema
-- Agentic AI (DSC 627O) course project, Lebanese American University.
-- Five tables: services, clients, prospects, drafts, escalations. All client and prospect data is synthetic.
--
-- Rebuild from scratch (from the repo root):
--     python data/build_db.py
--
-- NOTE for the code that will use this file: SQLite only enforces FOREIGN KEY rules when the
-- connection asks for it. Run   PRAGMA foreign_keys = ON;   once after every connect.

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------------------------------
-- services — the firm's current catalog (45 services, 9 service lines).
-- Week 3's retrieval runs over `description`.
-- ---------------------------------------------------------------------------------------------
CREATE TABLE services (
    code          TEXT PRIMARY KEY,                 -- e.g. 'AUD-001'
    name          TEXT NOT NULL,
    service_line  TEXT NOT NULL,                    -- e.g. 'Audit & Assurance'
    description   TEXT NOT NULL,
    active        INTEGER NOT NULL DEFAULT 1        -- 1 = accepting new engagements, 0 = paused
                  CHECK (active IN (0, 1))          --   (set by a partner, never by an agent)
);

-- ---------------------------------------------------------------------------------------------
-- clients — companies the firm already works with.
-- COLLATE NOCASE makes name matching case-insensitive, so a lookup for
-- 'al-rawdah trading company k.s.c.c.' finds 'Al-Rawdah Trading Company K.S.C.C.'.
-- ---------------------------------------------------------------------------------------------
CREATE TABLE clients (
    id             INTEGER PRIMARY KEY,
    name           TEXT NOT NULL UNIQUE COLLATE NOCASE,
    contact_name   TEXT,
    contact_email  TEXT,
    sector         TEXT,
    notes          TEXT,                            -- free text: size, services already used, history
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------------------------------------
-- prospects — companies that contacted the firm but have no client record.
-- Promotion from prospect to client is done by a partner, not by the agent.
-- ---------------------------------------------------------------------------------------------
CREATE TABLE prospects (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL COLLATE NOCASE,
    contact     TEXT,
    needs       TEXT,                               -- what they asked for, in their own words
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------------------------------------
-- drafts — draft engagement letters waiting for, or past, partner review.
--
-- A draft belongs to a client OR to a prospect: exactly one of the two ids is filled.
--
-- The lifecycle is one `status` column. The agent only ever writes 'pending_review';
-- every later status is set by a partner. The CHECK below makes the database reject
-- any other value, so the gate lives in the schema and not only in a prompt.
-- ---------------------------------------------------------------------------------------------
CREATE TABLE drafts (
    id             INTEGER PRIMARY KEY,
    client_id      INTEGER REFERENCES clients(id),
    prospect_id    INTEGER REFERENCES prospects(id),
    service_codes  TEXT NOT NULL,                   -- comma-separated service codes, e.g. 'AUD-001,TAX-002'
    letter_text    TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'pending_review'
                   CHECK (status IN ('pending_review', 'approved', 'rejected', 'revise', 'sent')),
    reviewer_note  TEXT,                            -- the partner's note; filled on reject / revise
    run_id         TEXT,                            -- the pipeline run that wrote it (its trace log is runs/<run_id>.jsonl)
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at     TEXT NOT NULL DEFAULT (datetime('now')),
    CHECK ( (client_id IS NOT NULL AND prospect_id IS NULL)
         OR (client_id IS NULL AND prospect_id IS NOT NULL) )
);

CREATE INDEX idx_drafts_status ON drafts(status);

-- ---------------------------------------------------------------------------------------------
-- escalations — cases the pipeline hands to a partner instead of deciding itself
-- (an unclear scope, a failed input check, a letter that keeps failing its checks).
--
-- A case may belong to a client, a prospect, or neither (an intake that failed the input
-- checks never got as far as a lookup) — but never both.
-- Like drafts, the agent only ever writes the first status; a partner resolves the case.
-- ---------------------------------------------------------------------------------------------
CREATE TABLE escalations (
    id           INTEGER PRIMARY KEY,
    client_id    INTEGER REFERENCES clients(id),
    prospect_id  INTEGER REFERENCES prospects(id),
    reason       TEXT NOT NULL,
    run_id       TEXT,                              -- the pipeline run that raised it
    status       TEXT NOT NULL DEFAULT 'awaiting_scope_decision'
                 CHECK (status IN ('awaiting_scope_decision', 'resolved')),
    partner_note TEXT,                              -- what the partner decided; filled when resolved
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    resolved_at  TEXT,
    CHECK (client_id IS NULL OR prospect_id IS NULL)
);

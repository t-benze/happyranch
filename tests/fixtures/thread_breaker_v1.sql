-- Historical PR-E v1 interrupted stage: episode table committed before
-- receipt table and indexes were installed.
DROP TABLE IF EXISTS thread_reply_breaker_receipts;
DROP TABLE IF EXISTS thread_reply_breaker_episodes;
CREATE TABLE thread_reply_breaker_episodes (
    thread_id TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    executor_key TEXT NOT NULL,
    episode_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('closed','open','probe')),
    consecutive_failures INTEGER NOT NULL DEFAULT 0 CHECK (consecutive_failures >= 0),
    opened_at TEXT,
    cooldown_until TEXT,
    probe_lease_id TEXT,
    last_failure_category TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (thread_id, agent_name, executor_key),
    UNIQUE (episode_id),
    FOREIGN KEY (thread_id) REFERENCES threads(id)
);

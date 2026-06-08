CREATE TABLE IF NOT EXISTS jf_kv (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS jf_set (
    key    TEXT NOT NULL,
    member TEXT NOT NULL,
    PRIMARY KEY (key, member)
);

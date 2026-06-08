CREATE TABLE IF NOT EXISTS jf_kv (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS jf_set (
    key    TEXT NOT NULL,
    member TEXT NOT NULL,
    PRIMARY KEY (key, member)
);

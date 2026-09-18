CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY,
    hashed_password TEXT NOT NULL,
    disabled BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS refresh_tokens (
    token_hash TEXT PRIMARY KEY,
    username TEXT NOT NULL REFERENCES users (username) ON DELETE CASCADE,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS refresh_tokens_username_idx ON refresh_tokens (username);

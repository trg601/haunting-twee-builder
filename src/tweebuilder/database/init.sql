CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY,
    hashed_password TEXT NOT NULL,
    disabled BOOLEAN NOT NULL DEFAULT FALSE
);

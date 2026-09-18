CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY,
    email TEXT,
    full_name TEXT,
    hashed_password TEXT NOT NULL,
    disabled BOOLEAN NOT NULL DEFAULT FALSE
);

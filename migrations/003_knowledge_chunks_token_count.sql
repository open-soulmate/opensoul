-- knowledge_chunks.token_count drift fix (2026-09-19)
-- 001_init.sql defines token_count, but databases created before that column was
-- added never received it (migrations only CREATE TABLE IF NOT EXISTS, no ALTER).
-- Runtime counterpart: src/database/postgres.py SQLitePool._ensure_schema() auto-heals
-- the same drift idempotently on startup; this file documents the schema intent.
ALTER TABLE knowledge_chunks ADD COLUMN token_count INT DEFAULT 0;

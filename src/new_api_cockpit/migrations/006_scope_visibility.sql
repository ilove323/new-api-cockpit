-- Preserve all ledgers and settings; visibility follows the live channel catalog.
ALTER TABLE balance_scopes ADD COLUMN is_visible boolean NOT NULL DEFAULT true;

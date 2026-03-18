-- Migration 002a: Fix platform_users column defaults
-- Ensures NOT NULL columns have proper DEFAULT values

-- sync_status: should default to 'pending'
ALTER TABLE platform_users
    ALTER COLUMN sync_status SET DEFAULT 'pending',
    ALTER COLUMN sync_status SET NOT NULL;

-- created_at / updated_at: should default to CURRENT_TIMESTAMP
ALTER TABLE platform_users
    ALTER COLUMN created_at SET DEFAULT CURRENT_TIMESTAMP;

ALTER TABLE platform_users
    ALTER COLUMN updated_at SET DEFAULT CURRENT_TIMESTAMP;

-- Backfill any NULL values
UPDATE platform_users SET sync_status = 'pending' WHERE sync_status IS NULL;
UPDATE platform_users SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL;
UPDATE platform_users SET updated_at = CURRENT_TIMESTAMP WHERE updated_at IS NULL;

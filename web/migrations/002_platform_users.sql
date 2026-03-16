-- Migration 002: Platform Users Support (Telegram + Mattermost)
-- This migration adds new tables for dual-platform support and user synchronization

-- ============================================================================
-- Table 1: platform_users
-- Unified user table supporting both Telegram and Mattermost platforms
-- ============================================================================
CREATE TABLE IF NOT EXISTS platform_users (
    id SERIAL PRIMARY KEY,

    -- Platform identifiers
    telegram_id BIGINT UNIQUE NULL,
    mattermost_user_id VARCHAR(255) UNIQUE NULL,
    mattermost_username VARCHAR(255) NULL,

    -- Basic info
    role VARCHAR(50) NOT NULL DEFAULT 'user' CHECK (role IN ('admin', 'user')),
    username VARCHAR(255) NULL,
    full_name VARCHAR(255) NULL,
    phone VARCHAR(20) NULL,

    -- Last command tracking
    last_command VARCHAR(255) NULL,
    last_command_at TIMESTAMPTZ NULL,

    -- Telegram lifecycle
    tg_added_at TIMESTAMPTZ NULL,
    tg_disabled_at TIMESTAMPTZ NULL,

    -- Mattermost lifecycle
    mm_added_at TIMESTAMPTZ NULL,
    mm_disabled_at TIMESTAMPTZ NULL,

    -- Synchronization tracking
    sync_status VARCHAR(50) DEFAULT 'pending' CHECK (sync_status IN ('pending', 'synced', 'failed')),
    sync_error TEXT NULL,
    last_sync_at TIMESTAMPTZ NULL,

    -- Timestamps
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_platform_users_telegram_id ON platform_users(telegram_id) WHERE telegram_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_platform_users_mattermost_user_id ON platform_users(mattermost_user_id) WHERE mattermost_user_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_platform_users_role ON platform_users(role);
CREATE INDEX IF NOT EXISTS idx_platform_users_sync_status ON platform_users(sync_status);


-- ============================================================================
-- Table 2: platform_destinations
-- Routing rules for notifications across platforms (Telegram chats/topics + MM channels)
-- ============================================================================
CREATE TABLE IF NOT EXISTS platform_destinations (
    id SERIAL PRIMARY KEY,

    -- Destination name/identifier
    name VARCHAR(255) UNIQUE NOT NULL,
    description TEXT NULL,

    -- Telegram routing
    tg_chat_id BIGINT NULL,
    tg_thread_id INTEGER NULL,

    -- Mattermost routing
    mm_channel_id VARCHAR(255) NULL,
    mm_channel_name VARCHAR(255) NULL,
    mm_post_parent_id VARCHAR(255) NULL,  -- For threading in channels

    -- Lifecycle
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_platform_destinations_tg_chat ON platform_destinations(tg_chat_id) WHERE tg_chat_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_platform_destinations_mm_channel ON platform_destinations(mm_channel_id) WHERE mm_channel_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_platform_destinations_enabled ON platform_destinations(enabled);


-- ============================================================================
-- Table 3: platform_sync_log
-- Audit log for all platform synchronization events
-- ============================================================================
CREATE TABLE IF NOT EXISTS platform_sync_log (
    id BIGSERIAL PRIMARY KEY,

    -- User being synchronized
    user_id INTEGER NOT NULL REFERENCES platform_users(id) ON DELETE CASCADE,

    -- What happened
    platform VARCHAR(50) NOT NULL CHECK (platform IN ('telegram', 'mattermost', 'system')),
    action VARCHAR(50) NOT NULL CHECK (action IN ('create', 'update', 'delete', 'disable', 'enable', 'link', 'unlink')),

    -- Details of the action (JSON for flexibility)
    details JSONB NOT NULL DEFAULT '{}'::jsonb,

    -- Timestamp
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_platform_sync_log_user_id ON platform_sync_log(user_id);
CREATE INDEX IF NOT EXISTS idx_platform_sync_log_platform ON platform_sync_log(platform);
CREATE INDEX IF NOT EXISTS idx_platform_sync_log_action ON platform_sync_log(action);
CREATE INDEX IF NOT EXISTS idx_platform_sync_log_created_at ON platform_sync_log(created_at);
CREATE INDEX IF NOT EXISTS idx_platform_sync_log_user_created ON platform_sync_log(user_id, created_at DESC);


-- ============================================================================
-- Optional: Archive old data (soft compatibility)
-- Rename old tables to preserve historical data
-- ============================================================================
-- Uncomment these if needed:
-- ALTER TABLE tg_users RENAME TO tg_users_legacy;
-- ALTER TABLE tg_command_history RENAME TO tg_command_history_legacy;
-- ALTER TABLE tg_user_audit RENAME TO tg_user_audit_legacy;

-- ============================================================================
-- Migration notes:
-- - Telegram IDs are BIGINT (can be negative for groups)
-- - Mattermost user IDs are VARCHAR(26) typically (Ulid format)
-- - JSONB usage for details allows flexible logging
-- - Foreign keys cascade delete on user deletion
-- - All new tables are created with IF NOT EXISTS for idempotency
-- ============================================================================

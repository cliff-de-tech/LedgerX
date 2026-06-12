-- ============================================================================
-- LedgerX Database Schema
-- Production-grade digital wallet & ledger system
-- ============================================================================

-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ============================================================================
-- ENUMS
-- ============================================================================

CREATE TYPE wallet_status AS ENUM (
    'ACTIVE',
    'FROZEN',
    'SUSPENDED',
    'CLOSED'
);

CREATE TYPE wallet_type AS ENUM (
    'USER',
    'MERCHANT',
    'SYSTEM',
    'FLOAT',
    'SETTLEMENT'
);

CREATE TYPE transaction_status AS ENUM (
    'PENDING',
    'PROCESSING',
    'COMPLETED',
    'FAILED',
    'REVERSED',
    'EXPIRED'
);

CREATE TYPE transaction_type AS ENUM (
    'CREDIT',           -- Add funds to wallet
    'DEBIT',            -- Remove funds from wallet
    'TRANSFER',         -- P2P or payment
    'HOLD',             -- Reserve funds
    'RELEASE',          -- Cancel hold
    'CAPTURE',          -- Convert hold to debit
    'REFUND',           -- Return funds
    'FEE',              -- Platform fee
    'ADJUSTMENT'        -- Manual correction
);

CREATE TYPE entry_type AS ENUM (
    'DEBIT',
    'CREDIT'
);

CREATE TYPE entry_status AS ENUM (
    'PENDING',
    'POSTED',
    'VOIDED'
);

CREATE TYPE currency_code AS ENUM (
    'USD', 'IDR', 'PHP', 'VND', 'THB', 'MYR', 'SGD', 'INR', 'JPY', 'KRW'
);

-- ============================================================================
-- CORE TABLES
-- ============================================================================

-- -----------------------------------------------------------------------------
-- Wallets Table
-- Central entity for all account holders
-- -----------------------------------------------------------------------------
CREATE TABLE wallets (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

    -- Identity
    external_id VARCHAR(64) UNIQUE NOT NULL,        -- Client-provided unique ID
    user_id UUID NOT NULL,                          -- Reference to user service
    wallet_type wallet_type NOT NULL DEFAULT 'USER',

    -- Status
    status wallet_status NOT NULL DEFAULT 'ACTIVE',
    status_reason VARCHAR(255),
    status_updated_at TIMESTAMP WITH TIME ZONE,

    -- Configuration
    currency currency_code NOT NULL DEFAULT 'USD',
    daily_limit DECIMAL(20, 4) DEFAULT 10000.0000,
    monthly_limit DECIMAL(20, 4) DEFAULT 100000.0000,

    -- Metadata
    metadata JSONB DEFAULT '{}',

    -- Audit
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    created_by UUID,
    version INTEGER NOT NULL DEFAULT 1              -- Optimistic locking
);

CREATE INDEX idx_wallets_user_id ON wallets(user_id);
CREATE INDEX idx_wallets_external_id ON wallets(external_id);
CREATE INDEX idx_wallets_status ON wallets(status) WHERE status != 'CLOSED';
CREATE INDEX idx_wallets_type ON wallets(wallet_type);

-- -----------------------------------------------------------------------------
-- Wallet Balances (Materialized/Cached)
-- Updated atomically via triggers, verified via reconciliation
-- -----------------------------------------------------------------------------
CREATE TABLE wallet_balances (
    wallet_id UUID PRIMARY KEY REFERENCES wallets(id),

    -- Balance breakdown
    posted_balance DECIMAL(20, 4) NOT NULL DEFAULT 0.0000,      -- Settled funds
    pending_credits DECIMAL(20, 4) NOT NULL DEFAULT 0.0000,     -- Incoming pending
    pending_debits DECIMAL(20, 4) NOT NULL DEFAULT 0.0000,      -- Outgoing pending
    held_balance DECIMAL(20, 4) NOT NULL DEFAULT 0.0000,        -- Reserved funds

    -- Computed available = posted_balance - held_balance + pending_credits
    available_balance DECIMAL(20, 4) GENERATED ALWAYS AS (
        posted_balance - held_balance
    ) STORED,

    -- Consistency tracking
    last_entry_id BIGINT,                                       -- Last processed entry
    last_entry_at TIMESTAMP WITH TIME ZONE,
    entry_count BIGINT NOT NULL DEFAULT 0,

    -- Audit
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    reconciled_at TIMESTAMP WITH TIME ZONE,
    version INTEGER NOT NULL DEFAULT 1
);

-- -----------------------------------------------------------------------------
-- Transactions Table
-- High-level transaction record (one per user action)
-- -----------------------------------------------------------------------------
CREATE TABLE transactions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

    -- Idempotency
    idempotency_key VARCHAR(128) UNIQUE NOT NULL,   -- Client-provided dedup key

    -- Transaction details
    transaction_type transaction_type NOT NULL,
    status transaction_status NOT NULL DEFAULT 'PENDING',

    -- Parties
    source_wallet_id UUID REFERENCES wallets(id),
    destination_wallet_id UUID REFERENCES wallets(id),

    -- Amounts
    amount DECIMAL(20, 4) NOT NULL CHECK (amount > 0),
    currency currency_code NOT NULL,
    fee_amount DECIMAL(20, 4) DEFAULT 0.0000,

    -- References
    reference_id VARCHAR(128),                      -- External reference
    parent_transaction_id UUID REFERENCES transactions(id),  -- For reversals

    -- Metadata
    description VARCHAR(512),
    metadata JSONB DEFAULT '{}',

    -- Processing
    processed_at TIMESTAMP WITH TIME ZONE,
    failure_reason VARCHAR(512),
    retry_count INTEGER DEFAULT 0,

    -- Audit
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    created_by UUID,
    version INTEGER NOT NULL DEFAULT 1,

    -- Constraints
    CONSTRAINT chk_different_wallets CHECK (
        source_wallet_id IS NULL OR
        destination_wallet_id IS NULL OR
        source_wallet_id != destination_wallet_id
    ),
    CONSTRAINT chk_has_wallet CHECK (
        source_wallet_id IS NOT NULL OR destination_wallet_id IS NOT NULL
    )
);

CREATE INDEX idx_transactions_idempotency ON transactions(idempotency_key);
CREATE INDEX idx_transactions_source ON transactions(source_wallet_id, created_at DESC);
CREATE INDEX idx_transactions_destination ON transactions(destination_wallet_id, created_at DESC);
CREATE INDEX idx_transactions_status ON transactions(status) WHERE status IN ('PENDING', 'PROCESSING');
CREATE INDEX idx_transactions_created ON transactions(created_at DESC);
CREATE INDEX idx_transactions_reference ON transactions(reference_id) WHERE reference_id IS NOT NULL;

-- Partitioning by month for scale (PostgreSQL 12+)
-- CREATE TABLE transactions_y2024m01 PARTITION OF transactions
--     FOR VALUES FROM ('2024-01-01') TO ('2024-02-01');

-- -----------------------------------------------------------------------------
-- Ledger Entries Table (Journal)
-- Immutable, append-only double-entry records
-- This is the source of truth for all balances
-- -----------------------------------------------------------------------------
CREATE TABLE ledger_entries (
    id BIGSERIAL PRIMARY KEY,                       -- Sequential for ordering

    -- Transaction reference
    transaction_id UUID NOT NULL REFERENCES transactions(id),

    -- Account affected
    wallet_id UUID NOT NULL REFERENCES wallets(id),

    -- Entry details
    entry_type entry_type NOT NULL,                 -- DEBIT or CREDIT
    amount DECIMAL(20, 4) NOT NULL CHECK (amount > 0),
    currency currency_code NOT NULL,

    -- Status
    status entry_status NOT NULL DEFAULT 'PENDING',
    posted_at TIMESTAMP WITH TIME ZONE,

    -- Running balance (for statement generation)
    running_balance DECIMAL(20, 4),                 -- Balance after this entry

    -- Audit
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),

    -- Immutability constraint: entries cannot be updated, only status can change
    CONSTRAINT chk_entry_immutable CHECK (TRUE)     -- Enforced via triggers
);

CREATE INDEX idx_ledger_transaction ON ledger_entries(transaction_id);
CREATE INDEX idx_ledger_wallet ON ledger_entries(wallet_id, created_at DESC);
CREATE INDEX idx_ledger_wallet_posted ON ledger_entries(wallet_id, posted_at DESC)
    WHERE status = 'POSTED';
CREATE INDEX idx_ledger_status ON ledger_entries(status) WHERE status = 'PENDING';

-- Partitioning for scale
-- Partition by wallet_id hash for even distribution
-- Or by created_at for time-series queries

-- -----------------------------------------------------------------------------
-- Holds Table
-- Track reserved funds that reduce available balance
-- -----------------------------------------------------------------------------
CREATE TABLE holds (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

    -- Reference
    wallet_id UUID NOT NULL REFERENCES wallets(id),
    transaction_id UUID NOT NULL REFERENCES transactions(id),

    -- Hold details
    amount DECIMAL(20, 4) NOT NULL CHECK (amount > 0),
    currency currency_code NOT NULL,

    -- Status
    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE'
        CHECK (status IN ('ACTIVE', 'CAPTURED', 'RELEASED', 'EXPIRED')),

    -- Expiry
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,

    -- Resolution
    resolved_at TIMESTAMP WITH TIME ZONE,
    resolved_transaction_id UUID REFERENCES transactions(id),

    -- Audit
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_holds_wallet ON holds(wallet_id, status);
CREATE INDEX idx_holds_expires ON holds(expires_at) WHERE status = 'ACTIVE';
CREATE INDEX idx_holds_transaction ON holds(transaction_id);

-- -----------------------------------------------------------------------------
-- Idempotency Keys Table
-- Prevent duplicate transaction processing
-- -----------------------------------------------------------------------------
CREATE TABLE idempotency_keys (
    key VARCHAR(128) PRIMARY KEY,

    -- Request details
    request_hash VARCHAR(64) NOT NULL,              -- SHA256 of request body

    -- Response caching
    response_status INTEGER,
    response_body JSONB,

    -- Reference
    transaction_id UUID REFERENCES transactions(id),

    -- Lifecycle
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT (NOW() + INTERVAL '24 hours')
);

CREATE INDEX idx_idempotency_expires ON idempotency_keys(expires_at);

-- -----------------------------------------------------------------------------
-- Audit Log Table
-- Immutable record of all system actions
-- -----------------------------------------------------------------------------
CREATE TABLE audit_logs (
    id BIGSERIAL PRIMARY KEY,

    -- Event details
    event_type VARCHAR(64) NOT NULL,
    event_action VARCHAR(32) NOT NULL,              -- CREATE, UPDATE, DELETE

    -- Entity reference
    entity_type VARCHAR(64) NOT NULL,               -- wallet, transaction, etc.
    entity_id VARCHAR(128) NOT NULL,

    -- Actor
    actor_type VARCHAR(32) NOT NULL,                -- USER, SYSTEM, ADMIN
    actor_id UUID,

    -- Change details
    old_values JSONB,
    new_values JSONB,

    -- Context
    ip_address INET,
    user_agent TEXT,
    request_id UUID,

    -- Timestamp
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_audit_entity ON audit_logs(entity_type, entity_id, created_at DESC);
CREATE INDEX idx_audit_actor ON audit_logs(actor_id, created_at DESC) WHERE actor_id IS NOT NULL;
CREATE INDEX idx_audit_event ON audit_logs(event_type, created_at DESC);
CREATE INDEX idx_audit_created ON audit_logs(created_at DESC);

-- Partition by month for retention management
-- ALTER TABLE audit_logs SET (autovacuum_enabled = false);  -- Append-only optimization

-- -----------------------------------------------------------------------------
-- Daily Balance Snapshots
-- For fast historical balance queries and reconciliation
-- -----------------------------------------------------------------------------
CREATE TABLE balance_snapshots (
    id BIGSERIAL PRIMARY KEY,

    wallet_id UUID NOT NULL REFERENCES wallets(id),
    snapshot_date DATE NOT NULL,

    -- Balances at end of day
    posted_balance DECIMAL(20, 4) NOT NULL,
    held_balance DECIMAL(20, 4) NOT NULL,

    -- Entry tracking
    last_entry_id BIGINT NOT NULL,
    entry_count BIGINT NOT NULL,

    -- Audit
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),

    UNIQUE(wallet_id, snapshot_date)
);

CREATE INDEX idx_snapshots_wallet_date ON balance_snapshots(wallet_id, snapshot_date DESC);

-- ============================================================================
-- FUNCTIONS & TRIGGERS
-- ============================================================================

-- -----------------------------------------------------------------------------
-- Function: Update wallet balance on ledger entry
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION update_wallet_balance()
RETURNS TRIGGER AS $$
BEGIN
    -- Only process when entry is posted
    IF NEW.status = 'POSTED' AND (OLD IS NULL OR OLD.status != 'POSTED') THEN

        -- Update balance based on entry type
        -- DEBIT decreases balance (money out)
        -- CREDIT increases balance (money in)
        UPDATE wallet_balances
        SET
            posted_balance = posted_balance +
                CASE WHEN NEW.entry_type = 'CREDIT' THEN NEW.amount
                     ELSE -NEW.amount END,
            last_entry_id = NEW.id,
            last_entry_at = NEW.posted_at,
            entry_count = entry_count + 1,
            updated_at = NOW(),
            version = version + 1
        WHERE wallet_id = NEW.wallet_id;

        -- Update running balance on entry
        UPDATE ledger_entries
        SET running_balance = (
            SELECT posted_balance
            FROM wallet_balances
            WHERE wallet_id = NEW.wallet_id
        )
        WHERE id = NEW.id;

    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_update_balance
    AFTER INSERT OR UPDATE ON ledger_entries
    FOR EACH ROW
    EXECUTE FUNCTION update_wallet_balance();

-- -----------------------------------------------------------------------------
-- Function: Prevent ledger entry modification (immutability)
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION prevent_ledger_modification()
RETURNS TRIGGER AS $$
BEGIN
    -- Only allow status changes
    IF OLD.transaction_id != NEW.transaction_id OR
       OLD.wallet_id != NEW.wallet_id OR
       OLD.entry_type != NEW.entry_type OR
       OLD.amount != NEW.amount OR
       OLD.currency != NEW.currency THEN
        RAISE EXCEPTION 'Ledger entries are immutable. Only status can be changed.';
    END IF;

    -- Prevent changing from POSTED to PENDING
    IF OLD.status = 'POSTED' AND NEW.status = 'PENDING' THEN
        RAISE EXCEPTION 'Cannot unpublish a posted ledger entry.';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_ledger_immutable
    BEFORE UPDATE ON ledger_entries
    FOR EACH ROW
    EXECUTE FUNCTION prevent_ledger_modification();

-- -----------------------------------------------------------------------------
-- Function: Auto-create wallet balance record
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION create_wallet_balance()
RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO wallet_balances (wallet_id)
    VALUES (NEW.id);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_create_balance
    AFTER INSERT ON wallets
    FOR EACH ROW
    EXECUTE FUNCTION create_wallet_balance();

-- -----------------------------------------------------------------------------
-- Function: Update hold balance
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION update_hold_balance()
RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'INSERT' AND NEW.status = 'ACTIVE' THEN
        UPDATE wallet_balances
        SET held_balance = held_balance + NEW.amount,
            updated_at = NOW()
        WHERE wallet_id = NEW.wallet_id;

    ELSIF TG_OP = 'UPDATE' AND OLD.status = 'ACTIVE' AND NEW.status != 'ACTIVE' THEN
        UPDATE wallet_balances
        SET held_balance = held_balance - NEW.amount,
            updated_at = NOW()
        WHERE wallet_id = NEW.wallet_id;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_update_hold_balance
    AFTER INSERT OR UPDATE ON holds
    FOR EACH ROW
    EXECUTE FUNCTION update_hold_balance();

-- -----------------------------------------------------------------------------
-- Function: Compute balance from ledger (for verification)
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION compute_balance_from_ledger(p_wallet_id UUID)
RETURNS TABLE(
    computed_balance DECIMAL(20, 4),
    credit_sum DECIMAL(20, 4),
    debit_sum DECIMAL(20, 4),
    entry_count BIGINT
) AS $$
BEGIN
    RETURN QUERY
    SELECT
        COALESCE(SUM(CASE WHEN entry_type = 'CREDIT' THEN amount ELSE -amount END), 0) AS computed_balance,
        COALESCE(SUM(CASE WHEN entry_type = 'CREDIT' THEN amount ELSE 0 END), 0) AS credit_sum,
        COALESCE(SUM(CASE WHEN entry_type = 'DEBIT' THEN amount ELSE 0 END), 0) AS debit_sum,
        COUNT(*) AS entry_count
    FROM ledger_entries
    WHERE wallet_id = p_wallet_id
      AND status = 'POSTED';
END;
$$ LANGUAGE plpgsql;

-- -----------------------------------------------------------------------------
-- Function: Safe transfer with optimistic locking
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION execute_transfer(
    p_idempotency_key VARCHAR(128),
    p_source_wallet_id UUID,
    p_destination_wallet_id UUID,
    p_amount DECIMAL(20, 4),
    p_currency currency_code,
    p_description VARCHAR(512) DEFAULT NULL,
    p_metadata JSONB DEFAULT '{}'
)
RETURNS UUID AS $$
DECLARE
    v_transaction_id UUID;
    v_source_balance DECIMAL(20, 4);
    v_source_version INTEGER;
BEGIN
    -- Check idempotency
    SELECT transaction_id INTO v_transaction_id
    FROM idempotency_keys
    WHERE key = p_idempotency_key;

    IF v_transaction_id IS NOT NULL THEN
        RETURN v_transaction_id;  -- Already processed
    END IF;

    -- Lock and check source balance with version
    SELECT available_balance, version
    INTO v_source_balance, v_source_version
    FROM wallet_balances
    WHERE wallet_id = p_source_wallet_id
    FOR UPDATE;

    IF v_source_balance IS NULL THEN
        RAISE EXCEPTION 'Source wallet not found';
    END IF;

    IF v_source_balance < p_amount THEN
        RAISE EXCEPTION 'Insufficient balance. Available: %, Required: %',
            v_source_balance, p_amount;
    END IF;

    -- Create transaction record
    INSERT INTO transactions (
        idempotency_key, transaction_type, status,
        source_wallet_id, destination_wallet_id,
        amount, currency, description, metadata
    ) VALUES (
        p_idempotency_key, 'TRANSFER', 'PROCESSING',
        p_source_wallet_id, p_destination_wallet_id,
        p_amount, p_currency, p_description, p_metadata
    ) RETURNING id INTO v_transaction_id;

    -- Create ledger entries (double-entry)
    -- Debit source (money out)
    INSERT INTO ledger_entries (
        transaction_id, wallet_id, entry_type, amount, currency, status, posted_at
    ) VALUES (
        v_transaction_id, p_source_wallet_id, 'DEBIT', p_amount, p_currency, 'POSTED', NOW()
    );

    -- Credit destination (money in)
    INSERT INTO ledger_entries (
        transaction_id, wallet_id, entry_type, amount, currency, status, posted_at
    ) VALUES (
        v_transaction_id, p_destination_wallet_id, 'CREDIT', p_amount, p_currency, 'POSTED', NOW()
    );

    -- Update transaction status
    UPDATE transactions
    SET status = 'COMPLETED', processed_at = NOW(), updated_at = NOW()
    WHERE id = v_transaction_id;

    -- Store idempotency record
    INSERT INTO idempotency_keys (key, request_hash, transaction_id)
    VALUES (p_idempotency_key, encode(sha256(p_metadata::text::bytea), 'hex'), v_transaction_id);

    RETURN v_transaction_id;
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- SYSTEM ACCOUNTS (Seed Data)
-- ============================================================================

-- -----------------------------------------------------------------------------
-- Outbox Events Table (Transactional Outbox Pattern)
-- Ensures at-least-once event delivery without distributed transactions
-- -----------------------------------------------------------------------------
CREATE TABLE outbox_events (
    id BIGSERIAL PRIMARY KEY,

    -- Event identity
    event_id UUID NOT NULL DEFAULT uuid_generate_v4(),
    event_type VARCHAR(64) NOT NULL,                -- e.g., 'transaction.completed'

    -- Routing
    aggregate_type VARCHAR(64) NOT NULL,            -- e.g., 'wallet', 'transaction'
    aggregate_id UUID NOT NULL,                     -- e.g., wallet_id, transaction_id

    -- Payload
    payload JSONB NOT NULL,

    -- Publishing state
    published BOOLEAN NOT NULL DEFAULT FALSE,
    published_at TIMESTAMP WITH TIME ZONE,
    publish_attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,

    -- Ordering (for per-aggregate ordering)
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),

    -- Cleanup (events can be deleted after successful publish + retention)
    expires_at TIMESTAMP WITH TIME ZONE DEFAULT (NOW() + INTERVAL '7 days')
);

CREATE INDEX idx_outbox_unpublished ON outbox_events(created_at) WHERE published = FALSE;
CREATE INDEX idx_outbox_aggregate ON outbox_events(aggregate_type, aggregate_id, created_at);
CREATE INDEX idx_outbox_expires ON outbox_events(expires_at) WHERE published = TRUE;

COMMENT ON TABLE outbox_events IS 'Transactional outbox for reliable event publishing with at-least-once delivery';

-- Create system wallets for internal operations
INSERT INTO wallets (id, external_id, user_id, wallet_type, currency, status)
VALUES
    ('00000000-0000-0000-0000-000000000001', 'SYSTEM_FLOAT', '00000000-0000-0000-0000-000000000000', 'FLOAT', 'USD', 'ACTIVE'),
    ('00000000-0000-0000-0000-000000000002', 'SYSTEM_FEE', '00000000-0000-0000-0000-000000000000', 'SYSTEM', 'USD', 'ACTIVE'),
    ('00000000-0000-0000-0000-000000000003', 'SYSTEM_SETTLEMENT', '00000000-0000-0000-0000-000000000000', 'SETTLEMENT', 'USD', 'ACTIVE')
ON CONFLICT (external_id) DO NOTHING;

-- ============================================================================
-- INDEXES FOR QUERY OPTIMIZATION
-- ============================================================================

-- Composite indexes for common queries
CREATE INDEX idx_ledger_wallet_status_date ON ledger_entries(wallet_id, status, created_at DESC);
CREATE INDEX idx_transactions_wallet_type_date ON transactions(source_wallet_id, transaction_type, created_at DESC);

-- Partial indexes for active records
CREATE INDEX idx_active_wallets ON wallets(id) WHERE status = 'ACTIVE';
CREATE INDEX idx_pending_transactions ON transactions(created_at) WHERE status = 'PENDING';

-- ============================================================================
-- COMMENTS
-- ============================================================================

COMMENT ON TABLE wallets IS 'Core wallet entities representing user, merchant, or system accounts';
COMMENT ON TABLE wallet_balances IS 'Materialized balance cache, updated via triggers from ledger entries';
COMMENT ON TABLE transactions IS 'High-level transaction records for user-initiated operations';
COMMENT ON TABLE ledger_entries IS 'Immutable double-entry journal - source of truth for all balances';
COMMENT ON TABLE holds IS 'Fund reservations that reduce available balance';
COMMENT ON TABLE idempotency_keys IS 'Request deduplication for safe retries';
COMMENT ON TABLE audit_logs IS 'Immutable audit trail for compliance';
COMMENT ON TABLE balance_snapshots IS 'Daily balance snapshots for fast historical queries';

COMMENT ON FUNCTION compute_balance_from_ledger IS 'Recomputes balance from ledger entries for reconciliation';
COMMENT ON FUNCTION execute_transfer IS 'Atomic transfer with optimistic locking and idempotency';

# LedgerX - Production-Grade Digital Wallet & Ledger Backend

> A financial-grade digital wallet system with double-entry ledger accounting, designed for Asia-scale fintech usage.

---

## 📋 Table of Contents

1. [Problem Statement](#problem-statement)
2. [User Personas](#user-personas)
3. [Core Features](#core-features)
4. [Ledger-Based Accounting Model](#ledger-based-accounting-model)
5. [Non-Functional Requirements](#non-functional-requirements)
6. [High-Level Architecture](#high-level-architecture)
7. [Tech Stack](#tech-stack)
8. [Design Deep Dive](#design-deep-dive)
   - [Balance Snapshot Strategy](#1-balance-snapshot-strategy)
   - [Transaction Reversal Model](#2-transaction-reversal-model)
   - [Event Publishing Guarantees](#3-event-publishing-guarantees)
   - [Security Trust Boundaries](#4-security-trust-boundaries)
   - [Design Tradeoffs & Rationale](#5-design-tradeoffs--rationale)
   - [Sequence Diagrams](#6-sequence-diagrams)
9. [Getting Started](#getting-started)

---

## 🎯 Problem Statement

### The Challenge

Digital wallets face critical challenges that can result in financial loss and regulatory violations:

1. **Double-Spending**: Concurrent transactions can overdraw accounts when using naive `balance -= amount` updates
2. **Lost Updates**: Race conditions cause transactions to overwrite each other
3. **Inconsistent State**: Network failures mid-transaction leave wallets in undefined states
4. **Audit Trail Gaps**: Direct balance mutations make forensic accounting impossible
5. **Scalability vs Consistency**: Traditional locking doesn't scale to millions of concurrent users

### Real-World Scenario

```
User Balance: $100

T1 (Thread 1): Read balance → $100
T2 (Thread 2): Read balance → $100
T1: Debit $80 → Write $20
T2: Debit $80 → Write $20  ← DOUBLE SPEND! User spent $160 with only $100

Actual balance should be: -$60 (overdraft) or T2 should fail
```

### Our Solution

LedgerX implements **double-entry bookkeeping** with **append-only ledger entries**, ensuring:
- Every transaction is atomic and traceable
- Balances are computed from immutable journal entries
- No direct balance mutations ever occur
- Full audit trail for regulatory compliance

---

## 👥 User Personas

### 1. End Consumer (Primary)
- **Profile**: Mobile-first users in Southeast Asia (Indonesia, Philippines, Vietnam)
- **Needs**: Instant P2P transfers, bill payments, QR payments
- **Pain Points**: Transaction failures, unclear balance, delayed settlements
- **Scale**: 10M+ active users, 1000+ TPS peak

### 2. Merchant
- **Profile**: Small-to-medium businesses accepting digital payments
- **Needs**: Real-time settlement, transaction reports, refund capabilities
- **Pain Points**: Reconciliation errors, chargebacks, cash flow visibility

### 3. Platform Operator (Internal)
- **Profile**: Finance and compliance teams
- **Needs**: Real-time dashboards, audit logs, regulatory reports
- **Pain Points**: Manual reconciliation, fraud detection delays

### 4. Partner Systems (B2B)
- **Profile**: Banks, payment gateways, e-commerce platforms
- **Needs**: Reliable APIs, webhook notifications, idempotent operations
- **Pain Points**: Integration complexity, retry handling, data consistency

---

## ⚡ Core Features

### 1. Wallet Management
| Feature | Description |
|---------|-------------|
| Create Wallet | Open new wallet with KYC-linked user ID |
| Get Balance | Real-time computed balance from ledger |
| Freeze/Unfreeze | Compliance-triggered account controls |
| Close Wallet | Soft-delete with balance transfer requirement |

### 2. Transaction Operations
| Operation | Description | Atomicity |
|-----------|-------------|-----------|
| **Credit** | Add funds (top-up, refund, cashback) | Single-entry debit from source |
| **Debit** | Remove funds (payment, withdrawal) | Single-entry credit to destination |
| **Transfer** | P2P or merchant payment | Double-entry atomic operation |
| **Hold** | Reserve funds for pending transaction | Creates hold entry, reduces available |
| **Release** | Cancel hold, restore available balance | Reverses hold entry |
| **Capture** | Convert hold to actual debit | Settles held amount |

### 3. Ledger Operations
| Feature | Description |
|---------|-------------|
| Journal Entry | Immutable record of every balance change |
| Balance Computation | `SUM(credits) - SUM(debits)` per wallet |
| Statement Generation | Date-range transaction history |
| Reconciliation | Automated balance verification |

### 4. Administrative
| Feature | Description |
|---------|-------------|
| Idempotency | Duplicate request detection via client tokens |
| Retry Safety | Safe retries without duplicate processing |
| Webhooks | Real-time event notifications |
| Rate Limiting | Abuse prevention per user/IP |

---

## 📒 Ledger-Based Accounting Model

### Why Not Naive Balance Updates?

```python
# ❌ WRONG: Direct balance update (race condition prone)
UPDATE wallets SET balance = balance - 100 WHERE id = 'user123';

# ❌ WRONG: Read-modify-write (lost update prone)  
balance = SELECT balance FROM wallets WHERE id = 'user123';
UPDATE wallets SET balance = {balance - 100} WHERE id = 'user123';
```

### Double-Entry Bookkeeping

Every transaction creates **exactly two entries** that sum to zero:

```
Transfer $100 from Alice to Bob:

┌─────────────────────────────────────────────────────────────┐
│ Journal Entry #TXN-001                                      │
├─────────────┬─────────┬────────┬────────┬──────────────────┤
│ Account     │ Type    │ Debit  │ Credit │ Running Balance  │
├─────────────┼─────────┼────────┼────────┼──────────────────┤
│ Alice       │ ASSET   │ $100   │        │ $900             │
│ Bob         │ ASSET   │        │ $100   │ $200             │
├─────────────┼─────────┼────────┼────────┼──────────────────┤
│ TOTAL       │         │ $100   │ $100   │ ✓ Balanced       │
└─────────────┴─────────┴────────┴────────┴──────────────────┘
```

### Account Types (Chart of Accounts)

```
ASSETS (Debit increases, Credit decreases)
├── USER_WALLET          # Individual user wallets
├── MERCHANT_WALLET      # Business wallets  
├── FLOAT_ACCOUNT        # Platform operating funds
└── SETTLEMENT_ACCOUNT   # Pending settlements

LIABILITIES (Credit increases, Debit decreases)
├── USER_PAYABLE         # Owed to users
├── MERCHANT_PAYABLE     # Owed to merchants
└── HOLD_ACCOUNT         # Funds on hold

REVENUE (Credit increases)
├── TRANSACTION_FEE      # Per-transaction fees
├── INTERCHANGE_FEE      # Network fees earned
└── INTEREST_INCOME      # Float interest

EXPENSES (Debit increases)
├── PAYMENT_GATEWAY_FEE  # Third-party costs
├── FRAUD_LOSS           # Chargebacks/fraud
└── OPERATIONAL_COST     # Platform operations
```

### Balance Computation

```sql
-- Wallet balance is ALWAYS computed, never stored directly
SELECT 
    wallet_id,
    SUM(CASE WHEN entry_type = 'CREDIT' THEN amount ELSE 0 END) -
    SUM(CASE WHEN entry_type = 'DEBIT' THEN amount ELSE 0 END) AS balance
FROM ledger_entries
WHERE wallet_id = :wallet_id
  AND status = 'POSTED'
GROUP BY wallet_id;
```

### Optimized Balance with Materialized View

For performance, we maintain a **cached balance** that's updated atomically:

```sql
-- Materialized balance (updated via triggers/events)
CREATE TABLE wallet_balances (
    wallet_id UUID PRIMARY KEY,
    posted_balance DECIMAL(20,4),      -- Settled balance
    pending_balance DECIMAL(20,4),     -- Including holds
    available_balance DECIMAL(20,4),   -- posted - holds
    last_entry_id BIGINT,              -- For consistency check
    updated_at TIMESTAMP
);

-- Consistency invariant (verified periodically)
ASSERT wallet_balances.posted_balance == SUM(ledger_entries for wallet)
```

---

## 📊 Non-Functional Requirements

### 1. Consistency (CRITICAL)
| Requirement | Target | Mechanism |
|-------------|--------|-----------|
| Transaction Atomicity | 100% | Database transactions + saga pattern |
| Balance Accuracy | 100% | Ledger-based computation |
| Double-spend Prevention | 100% | Optimistic locking + idempotency |
| Eventual Consistency Window | < 100ms | Async event propagation |

### 2. Durability
| Requirement | Target | Mechanism |
|-------------|--------|-----------|
| Data Persistence | 99.999999999% | Multi-AZ PostgreSQL + WAL archiving |
| Point-in-time Recovery | 30 days | Continuous backup + PITR |
| Audit Log Retention | 7 years | Immutable append-only logs |
| Disaster Recovery | RPO < 1min, RTO < 15min | Cross-region replication |

### 3. Availability
| Requirement | Target | Mechanism |
|-------------|--------|-----------|
| API Uptime | 99.95% | Multi-AZ deployment + health checks |
| Read Availability | 99.99% | Read replicas + caching |
| Graceful Degradation | Yes | Circuit breakers + fallbacks |

### 4. Scalability
| Metric | Target | Strategy |
|--------|--------|----------|
| Concurrent Users | 10M+ | Horizontal API scaling |
| Transactions/Second | 10,000 TPS | Sharded writes + CQRS |
| Storage Growth | 1TB/month | Time-series partitioning |
| Wallet Count | 100M+ | Hash-based sharding |

### 5. Security
| Requirement | Implementation |
|-------------|----------------|
| Encryption at Rest | AES-256 for all PII and financial data |
| Encryption in Transit | TLS 1.3 mandatory |
| Authentication | OAuth 2.0 + JWT with short expiry |
| Authorization | RBAC + resource-level permissions |
| Audit Logging | Immutable logs for all operations |
| PCI-DSS Compliance | Tokenized card data, no PAN storage |

### 6. Performance
| Operation | P50 | P99 | Max |
|-----------|-----|-----|-----|
| Balance Query | 5ms | 50ms | 200ms |
| Transfer | 50ms | 200ms | 500ms |
| Statement (30 days) | 100ms | 500ms | 2s |

---

## 🏗️ High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              CLIENTS                                         │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐        │
│  │ Mobile App  │  │  Web App    │  │ Partner API │  │  Admin UI   │        │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘        │
└─────────┼────────────────┼────────────────┼────────────────┼────────────────┘
          │                │                │                │
          ▼                ▼                ▼                ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           API GATEWAY (Kong/AWS API Gateway)                 │
│  • Rate Limiting  • Authentication  • Request Routing  • SSL Termination    │
└─────────────────────────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         APPLICATION LAYER (Kubernetes)                       │
│                                                                              │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐             │
│  │  Wallet Service │  │ Ledger Service  │  │ Transfer Service│             │
│  │   (FastAPI)     │  │   (FastAPI)     │  │   (FastAPI)     │             │
│  │                 │  │                 │  │                 │             │
│  │ • Create Wallet │  │ • Post Entry    │  │ • P2P Transfer  │             │
│  │ • Get Balance   │  │ • Get Statement │  │ • Hold/Capture  │             │
│  │ • Freeze/Close  │  │ • Reconcile     │  │ • Refund        │             │
│  └────────┬────────┘  └────────┬────────┘  └────────┬────────┘             │
│           │                    │                    │                       │
│  ┌────────┴────────────────────┴────────────────────┴────────┐             │
│  │                    DOMAIN LAYER                            │             │
│  │  • Transaction Coordinator  • Balance Calculator           │             │
│  │  • Idempotency Manager      • Event Publisher              │             │
│  └────────────────────────────────────────────────────────────┘             │
└─────────────────────────────────────────────────────────────────────────────┘
          │                    │                    │
          ▼                    ▼                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           DATA LAYER                                         │
│                                                                              │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐             │
│  │   PostgreSQL    │  │     Redis       │  │   Kafka/SQS     │             │
│  │   (Primary)     │  │   (Cache)       │  │   (Events)      │             │
│  │                 │  │                 │  │                 │             │
│  │ • Ledger Entries│  │ • Balance Cache │  │ • Txn Events    │             │
│  │ • Wallets       │  │ • Idempotency   │  │ • Webhooks      │             │
│  │ • Transactions  │  │ • Rate Limits   │  │ • Audit Stream  │             │
│  │ • Audit Logs    │  │ • Sessions      │  │ • Analytics     │             │
│  └────────┬────────┘  └─────────────────┘  └────────┬────────┘             │
│           │                                          │                       │
│           ▼                                          ▼                       │
│  ┌─────────────────┐                      ┌─────────────────┐               │
│  │  Read Replicas  │                      │  Event Workers  │               │
│  │  (2-3 replicas) │                      │  • Webhook      │               │
│  └─────────────────┘                      │  • Notification │               │
│                                           │  • Analytics    │               │
│                                           └─────────────────┘               │
└─────────────────────────────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                        OBSERVABILITY & OPERATIONS                            │
│                                                                              │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐             │
│  │  Prometheus +   │  │   ELK Stack /   │  │    Jaeger /     │             │
│  │    Grafana      │  │   CloudWatch    │  │    X-Ray        │             │
│  │   (Metrics)     │  │    (Logs)       │  │   (Tracing)     │             │
│  └─────────────────┘  └─────────────────┘  └─────────────────┘             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Component Responsibilities

#### API Gateway
- **Rate Limiting**: 1000 req/min per user, 10000 req/min per partner
- **Authentication**: JWT validation, API key verification
- **Request Routing**: Version-based routing, A/B testing
- **DDoS Protection**: AWS Shield / Cloudflare integration

#### Wallet Service
- Wallet lifecycle management (create, freeze, close)
- Balance queries with caching
- KYC status integration

#### Ledger Service
- **Core responsibility**: Append-only journal entries
- Balance computation from ledger
- Statement generation
- Reconciliation jobs

#### Transfer Service
- Transaction orchestration (saga pattern)
- Idempotency enforcement
- Hold/capture/release flows
- Rollback handling

#### Event Workers
- Async webhook delivery with retry
- Push notifications
- Analytics event processing
- Audit log archival

---

## 🛠️ Tech Stack

### Core Services

| Component | Technology | Justification |
|-----------|------------|---------------|
| **API Framework** | FastAPI (Python) | Async support, auto OpenAPI docs, type hints, excellent for fintech |
| **Primary Database** | PostgreSQL 15+ | ACID compliance, SERIALIZABLE isolation, excellent for financial data |
| **Cache** | Redis Cluster | Sub-ms latency, Lua scripting for atomic ops, pub/sub for events |
| **Message Queue** | Apache Kafka | Ordered events, replay capability, high throughput |
| **API Gateway** | Kong / AWS API Gateway | Rate limiting, auth, observability built-in |

### Infrastructure

| Component | Technology | Justification |
|-----------|------------|---------------|
| **Container Orchestration** | Kubernetes (EKS/GKE) | Auto-scaling, rolling deployments, self-healing |
| **Service Mesh** | Istio | mTLS, traffic management, observability |
| **Secrets Management** | HashiCorp Vault | Dynamic secrets, encryption-as-a-service |
| **CI/CD** | GitHub Actions + ArgoCD | GitOps, automated deployments |

### Observability

| Component | Technology | Justification |
|-----------|------------|---------------|
| **Metrics** | Prometheus + Grafana | Industry standard, rich ecosystem |
| **Logging** | ELK Stack / Loki | Centralized logs, full-text search |
| **Tracing** | Jaeger / AWS X-Ray | Distributed tracing for debugging |
| **Alerting** | PagerDuty + Grafana | On-call rotation, alert aggregation |

### Why This Stack?

1. **PostgreSQL over NoSQL**: Financial systems require ACID guarantees. PostgreSQL's SERIALIZABLE isolation prevents double-spending at the database level.

2. **FastAPI over Django/Flask**: Native async support handles thousands of concurrent connections efficiently. Type hints catch errors early.

3. **Kafka over RabbitMQ**: Event replay capability is crucial for reconciliation. Kafka's log-based architecture matches our append-only ledger philosophy.

4. **Redis for Caching**: Atomic operations (WATCH/MULTI/EXEC) enable safe balance caching with optimistic locking.

5. **Kubernetes**: Asia-scale requires elastic scaling. K8s handles traffic spikes during promotions/festivals seamlessly.

---

## 🚀 Getting Started

### Prerequisites

- Python 3.11+
- Docker & Docker Compose
- PostgreSQL 15+
- Redis 7+

### Quick Start

```bash
# Clone the repository
git clone https://github.com/yourorg/ledgerx.git
cd ledgerx

# Start dependencies
docker-compose up -d postgres redis kafka

# Install dependencies
pip install -r requirements.txt

# Run migrations
alembic upgrade head

# Start the API server
uvicorn app.main:app --reload --port 8000

# Access API docs
open http://localhost:8000/docs
```

### Project Structure

```
ledgerx/
├── app/
│   ├── api/                    # API routes
│   │   ├── v1/
│   │   │   ├── wallets.py
│   │   │   ├── transfers.py
│   │   │   └── ledger.py
│   │   └── deps.py             # Dependencies
│   ├── core/                   # Core configuration
│   │   ├── config.py
│   │   ├── security.py
│   │   └── exceptions.py
│   ├── domain/                 # Business logic
│   │   ├── models/
│   │   ├── services/
│   │   └── events/
│   ├── infrastructure/         # External integrations
│   │   ├── database/
│   │   ├── cache/
│   │   └── messaging/
│   └── main.py
├── migrations/                 # Alembic migrations
├── tests/
├── docker-compose.yml
├── Dockerfile
└── requirements.txt
```

---

## � Design Deep Dive

This section provides detailed clarifications on critical design decisions for production deployment.

### 1. Balance Snapshot Strategy

#### Snapshot Frequency
| Snapshot Type | Frequency | Retention | Use Case |
|---------------|-----------|-----------|----------|
| **Hot Snapshots** | Every 1 hour | 7 days | Fast balance recovery, recent queries |
| **Daily Snapshots** | End of day (UTC) | 2 years | Statements, reconciliation, audits |
| **Monthly Snapshots** | End of month | 7 years | Regulatory compliance, long-term audits |

#### Snapshot Process
```
┌─────────────────────────────────────────────────────────────────────────────┐
│                     BALANCE SNAPSHOT WORKFLOW                                │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  1. TRIGGER                    2. COMPUTE                    3. PERSIST     │
│  ┌──────────────┐             ┌──────────────┐             ┌──────────────┐ │
│  │ Scheduled    │────────────▶│ SELECT SUM   │────────────▶│ INSERT INTO  │ │
│  │ Cron Job     │             │ FROM ledger  │             │ snapshots    │ │
│  │ (K8s CronJob)│             │ WHERE posted │             │              │ │
│  └──────────────┘             └──────────────┘             └──────────────┘ │
│         │                            │                            │         │
│         │                            │                            │         │
│         ▼                            ▼                            ▼         │
│  ┌──────────────┐             ┌──────────────┐             ┌──────────────┐ │
│  │ Acquire      │             │ Compare with │             │ Update       │ │
│  │ Advisory Lock│             │ Cached Balance│            │ last_entry_id│ │
│  └──────────────┘             └──────────────┘             └──────────────┘ │
│                                      │                                      │
│                                      ▼                                      │
│                              ┌──────────────┐                               │
│                              │ Discrepancy? │                               │
│                              │ Alert + Log  │                               │
│                              └──────────────┘                               │
└─────────────────────────────────────────────────────────────────────────────┘
```

#### Failure Handling
| Failure Mode | Detection | Recovery | Impact |
|--------------|-----------|----------|--------|
| Snapshot job fails | K8s job monitoring + PagerDuty alert | Auto-retry with exponential backoff (3 attempts) | None - balance still computed from ledger |
| Snapshot divergence detected | Reconciliation comparison | Trigger full recompute from ledger + alert ops | Snapshot marked invalid, queries fall back to ledger |
| Database unavailable | Health check failure | Wait for recovery, resume from last `last_entry_id` | Delayed snapshots, no data loss |
| Partial snapshot (mid-write crash) | `completed_at IS NULL` check | Delete incomplete, re-run | None - atomic transaction ensures consistency |

#### Consistency Guarantees
```sql
-- Snapshots are created atomically within a transaction
BEGIN;
  -- Lock to prevent concurrent snapshots for same wallet
  SELECT pg_advisory_xact_lock(hashtext('snapshot:' || wallet_id::text));
  
  -- Compute balance at specific entry point
  INSERT INTO balance_snapshots (wallet_id, snapshot_date, posted_balance, held_balance, last_entry_id, entry_count)
  SELECT 
    wallet_id,
    CURRENT_DATE,
    SUM(CASE WHEN entry_type = 'CREDIT' THEN amount ELSE -amount END),
    COALESCE((SELECT SUM(amount) FROM holds WHERE wallet_id = le.wallet_id AND status = 'ACTIVE'), 0),
    MAX(id),
    COUNT(*)
  FROM ledger_entries le
  WHERE wallet_id = :wallet_id AND status = 'POSTED'
  GROUP BY wallet_id;
COMMIT;
```

**Invariant**: `balance_snapshots.posted_balance + SUM(ledger_entries WHERE id > last_entry_id) == wallet_balances.posted_balance`

---

### 2. Transaction Reversal Model

LedgerX follows the **compensating transaction pattern** - reversals never mutate historical data. Instead, new entries are created that offset the original transaction.

#### Reversal Types
| Reversal Type | Trigger | Timing | Creates |
|---------------|---------|--------|---------|
| **Full Refund** | Customer request, dispute | Any time | Inverse entries for full amount |
| **Partial Refund** | Merchant-initiated | Any time | Inverse entries for partial amount |
| **Void** | System/admin action | Within 24 hours | Void entries, marks original as VOIDED |
| **Chargeback** | Bank/card network | Up to 120 days | Compensating entries + chargeback record |
| **Correction** | Admin with approval | Any time | Adjustment entries with audit trail |

#### Reversal Entry Structure
```
Original Transfer: Alice → Bob ($100)
┌─────────────────────────────────────────────────────────────────────────────┐
│ Transaction #TXN-001 (status: COMPLETED)                                    │
├─────────────┬─────────┬────────┬────────┬──────────────────────────────────┤
│ Entry #1    │ Alice   │ DEBIT  │ $100   │ Posted 2024-01-15 10:00:00       │
│ Entry #2    │ Bob     │ CREDIT │ $100   │ Posted 2024-01-15 10:00:00       │
└─────────────┴─────────┴────────┴────────┴──────────────────────────────────┘

Reversal: Full Refund
┌─────────────────────────────────────────────────────────────────────────────┐
│ Transaction #TXN-002 (status: COMPLETED, parent: TXN-001, type: REFUND)     │
├─────────────┬─────────┬────────┬────────┬──────────────────────────────────┤
│ Entry #3    │ Bob     │ DEBIT  │ $100   │ Posted 2024-01-16 14:30:00       │
│ Entry #4    │ Alice   │ CREDIT │ $100   │ Posted 2024-01-16 14:30:00       │
└─────────────┴─────────┴────────┴────────┴──────────────────────────────────┘

Net Effect: Alice $0, Bob $0 (back to original state)
Original entries remain IMMUTABLE - audit trail preserved
```

#### Reversal Workflow
```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         REVERSAL WORKFLOW                                    │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│   ┌─────────┐    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐     │
│   │ Request │───▶│ Validate    │───▶│ Create      │───▶│ Post        │     │
│   │ Reversal│    │ Original Txn│    │ Compensating│    │ Entries     │     │
│   └─────────┘    └─────────────┘    │ Transaction │    └─────────────┘     │
│                         │           └─────────────┘           │             │
│                         ▼                  │                  ▼             │
│                  ┌─────────────┐           │           ┌─────────────┐      │
│                  │ Reversible? │           │           │ Update      │      │
│                  │ • Not already│          │           │ Original    │      │
│                  │   reversed   │          │           │ Status      │      │
│                  │ • Within     │          │           │ → REVERSED  │      │
│                  │   timeframe  │          │           └─────────────┘      │
│                  │ • Balance OK │          │                  │             │
│                  └─────────────┘           │                  ▼             │
│                         │                  │           ┌─────────────┐      │
│                         ▼                  │           │ Emit Event  │      │
│                  ┌─────────────┐           │           │ txn.reversed│      │
│                  │ Link via    │◀──────────┘           └─────────────┘      │
│                  │ parent_txn_id                                            │
│                  └─────────────┘                                            │
└─────────────────────────────────────────────────────────────────────────────┘
```

#### Reversal Constraints
```sql
-- Reversals reference the original transaction
ALTER TABLE transactions ADD CONSTRAINT fk_parent_transaction
    FOREIGN KEY (parent_transaction_id) REFERENCES transactions(id);

-- Prevent double reversal
CREATE UNIQUE INDEX idx_unique_reversal ON transactions(parent_transaction_id) 
    WHERE transaction_type IN ('REFUND', 'VOID', 'CHARGEBACK');

-- Reversal must match original amount (for full reversals)
-- Enforced at application layer with partial refund tracking
```

#### Key Principles
1. **Immutability**: Original ledger entries are NEVER modified
2. **Traceability**: `parent_transaction_id` links reversal to original
3. **Auditability**: Complete history preserved for compliance
4. **Idempotency**: Same reversal request returns same result

---

### 3. Event Publishing Guarantees

LedgerX uses the **Transactional Outbox Pattern** to ensure reliable event delivery without distributed transactions.

#### Delivery Semantics
| Guarantee | Implementation | Tradeoff |
|-----------|----------------|----------|
| **At-Least-Once** | ✅ Default | Consumers must be idempotent |
| **Exactly-Once** | ❌ Not guaranteed | Too costly for performance |
| **Ordering** | ✅ Per-wallet | Global ordering not guaranteed |

#### Outbox Pattern Architecture
```
┌─────────────────────────────────────────────────────────────────────────────┐
│                    TRANSACTIONAL OUTBOX PATTERN                              │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  WRITE PATH (Atomic)                    PUBLISH PATH (Async)                 │
│  ─────────────────────                  ────────────────────                 │
│                                                                              │
│  ┌─────────────┐                        ┌─────────────────┐                 │
│  │ Application │                        │ Outbox Poller   │                 │
│  │ Service     │                        │ (Debezium/Poll) │                 │
│  └──────┬──────┘                        └────────┬────────┘                 │
│         │                                        │                          │
│         │ BEGIN TRANSACTION                      │ Poll every 100ms         │
│         ▼                                        ▼                          │
│  ┌─────────────────────────────────┐    ┌─────────────────┐                 │
│  │         PostgreSQL              │    │ SELECT * FROM   │                 │
│  │  ┌───────────────────────────┐  │    │ outbox_events   │                 │
│  │  │ 1. INSERT ledger_entries  │  │    │ WHERE published │                 │
│  │  │ 2. INSERT transactions    │  │───▶│ = FALSE         │                 │
│  │  │ 3. INSERT outbox_events   │  │    │ ORDER BY seq    │                 │
│  │  └───────────────────────────┘  │    └────────┬────────┘                 │
│  │         COMMIT                  │             │                          │
│  └─────────────────────────────────┘             │                          │
│                                                  ▼                          │
│                                         ┌─────────────────┐                 │
│                                         │ Publish to      │                 │
│                                         │ Kafka/SQS       │                 │
│                                         └────────┬────────┘                 │
│                                                  │                          │
│                                                  ▼                          │
│                                         ┌─────────────────┐                 │
│                                         │ UPDATE outbox   │                 │
│                                         │ SET published   │                 │
│                                         │ = TRUE          │                 │
│                                         └─────────────────┘                 │
└─────────────────────────────────────────────────────────────────────────────┘
```

#### Outbox Table Schema
```sql
CREATE TABLE outbox_events (
    id BIGSERIAL PRIMARY KEY,
    
    -- Event identity
    event_id UUID NOT NULL DEFAULT uuid_generate_v4(),
    event_type VARCHAR(64) NOT NULL,           -- e.g., 'transaction.completed'
    
    -- Routing
    aggregate_type VARCHAR(64) NOT NULL,       -- e.g., 'wallet', 'transaction'
    aggregate_id UUID NOT NULL,                -- e.g., wallet_id
    
    -- Payload
    payload JSONB NOT NULL,
    
    -- Publishing state
    published BOOLEAN NOT NULL DEFAULT FALSE,
    published_at TIMESTAMP WITH TIME ZONE,
    publish_attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    
    -- Ordering
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    
    -- Cleanup
    expires_at TIMESTAMP WITH TIME ZONE DEFAULT (NOW() + INTERVAL '7 days')
);

CREATE INDEX idx_outbox_unpublished ON outbox_events(created_at) 
    WHERE published = FALSE;
CREATE INDEX idx_outbox_aggregate ON outbox_events(aggregate_type, aggregate_id);
```

#### Event Types
| Event | Trigger | Payload | Consumers |
|-------|---------|---------|-----------|
| `wallet.created` | Wallet creation | Wallet details | Analytics, Notifications |
| `wallet.frozen` | Freeze action | Wallet ID, reason | Compliance, Notifications |
| `transaction.pending` | Txn initiated | Transaction details | Webhooks, Monitoring |
| `transaction.completed` | Txn success | Full transaction + entries | Webhooks, Analytics, Reconciliation |
| `transaction.failed` | Txn failure | Transaction + error | Webhooks, Alerts |
| `transaction.reversed` | Reversal posted | Original + reversal txn | Webhooks, Reconciliation |
| `balance.updated` | Balance change | Wallet ID, new balance | Real-time dashboards |
| `hold.created` | Hold placed | Hold details | Webhooks |
| `hold.released` | Hold released/captured | Hold resolution | Webhooks |

#### Consumer Idempotency
Since we guarantee at-least-once delivery, consumers MUST be idempotent:

```python
# Consumer idempotency pattern
async def handle_transaction_completed(event: Event):
    # Check if already processed
    if await event_store.is_processed(event.event_id):
        logger.info(f"Event {event.event_id} already processed, skipping")
        return
    
    try:
        # Process event
        await process_webhook(event)
        
        # Mark as processed
        await event_store.mark_processed(event.event_id)
    except Exception as e:
        # Will be retried (at-least-once)
        raise
```

#### Failure Handling
| Failure | Detection | Recovery | SLA |
|---------|-----------|----------|-----|
| Poller crash | K8s liveness probe | Auto-restart, resume from last position | < 30s |
| Kafka unavailable | Publish timeout | Exponential backoff, DLQ after 5 attempts | Events delayed, not lost |
| Consumer crash | Consumer group rebalance | Another consumer picks up | < 10s |
| Poison message | Repeated failures | Move to Dead Letter Queue + alert | Manual intervention |

---

### 4. Security Trust Boundaries

#### Trust Zone Architecture
```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         TRUST BOUNDARY ARCHITECTURE                          │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ZONE 0: PUBLIC (Untrusted)                                                  │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │  Mobile Apps  │  Web Apps  │  Third-party Partners  │  Public Internet│  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                              │ HTTPS + mTLS (partners)                       │
│                              ▼                                               │
│  ─────────────────────── WAF / DDoS Protection ─────────────────────────    │
│                              │                                               │
│                              ▼                                               │
│  ZONE 1: DMZ (Semi-trusted)                                                  │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │                        API GATEWAY                                     │  │
│  │  • Rate limiting      • JWT validation     • Request sanitization     │  │
│  │  • API key validation • IP allowlisting    • Request/response logging │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                              │ Internal mTLS                                 │
│                              ▼                                               │
│  ZONE 2: APPLICATION (Trusted)                                               │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐                    │  │
│  │  │   Public    │  │  Internal   │  │   Admin     │                    │  │
│  │  │   API       │  │  API        │  │   API       │                    │  │
│  │  │ (Customer)  │  │ (Services)  │  │ (Operators) │                    │  │
│  │  └─────────────┘  └─────────────┘  └─────────────┘                    │  │
│  │        │                │                │                             │  │
│  │        └────────────────┴────────────────┘                             │  │
│  │                         │                                              │  │
│  │                    Service Mesh (Istio)                                │  │
│  │                    • mTLS between services                             │  │
│  │                    • Authorization policies                            │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                              │ Private subnet only                           │
│                              ▼                                               │
│  ZONE 3: DATA (Highly Trusted)                                               │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐                    │  │
│  │  │ PostgreSQL  │  │   Redis     │  │   Kafka     │                    │  │
│  │  │ (Encrypted) │  │  (Auth'd)   │  │  (SASL)     │                    │  │
│  │  └─────────────┘  └─────────────┘  └─────────────┘                    │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

#### API Classification

| API Type | Audience | Authentication | Authorization | Rate Limit |
|----------|----------|----------------|---------------|------------|
| **Public API** | End users, mobile apps | OAuth 2.0 + JWT | User can only access own resources | 100 req/min |
| **Partner API** | B2B integrations | API Key + mTLS | Scoped to partner's users | 1000 req/min |
| **Internal API** | Microservices | Service account + mTLS | Service-to-service policies | 10000 req/min |
| **Admin API** | Platform operators | SSO + MFA + JWT | RBAC with approval workflows | 100 req/min |

#### Authentication Flow
```
┌─────────────────────────────────────────────────────────────────────────────┐
│                      AUTHENTICATION FLOWS                                    │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  PUBLIC API (End Users)                                                      │
│  ┌──────────┐     ┌──────────┐     ┌──────────┐     ┌──────────┐           │
│  │  Client  │────▶│  Auth0/  │────▶│   JWT    │────▶│  API     │           │
│  │   App    │     │  Cognito │     │ (15min)  │     │ Gateway  │           │
│  └──────────┘     └──────────┘     └──────────┘     └──────────┘           │
│       │                                                   │                 │
│       └─────────── Refresh Token (7 days) ───────────────┘                 │
│                                                                              │
│  PARTNER API (B2B)                                                          │
│  ┌──────────┐     ┌──────────┐     ┌──────────┐     ┌──────────┐           │
│  │ Partner  │────▶│  API Key │────▶│   mTLS   │────▶│  API     │           │
│  │  Server  │     │ (Header) │     │  Cert    │     │ Gateway  │           │
│  └──────────┘     └──────────┘     └──────────┘     └──────────┘           │
│                                                                              │
│  INTERNAL API (Service-to-Service)                                          │
│  ┌──────────┐     ┌──────────┐     ┌──────────┐                             │
│  │ Service  │────▶│  SPIFFE/ │────▶│  Target  │                             │
│  │    A     │     │  mTLS    │     │ Service  │                             │
│  └──────────┘     └──────────┘     └──────────┘                             │
│                                                                              │
│  ADMIN API (Operators)                                                       │
│  ┌──────────┐     ┌──────────┐     ┌──────────┐     ┌──────────┐           │
│  │  Admin   │────▶│   SSO    │────▶│   MFA    │────▶│  Admin   │           │
│  │   User   │     │  (Okta)  │     │  (TOTP)  │     │   API    │           │
│  └──────────┘     └──────────┘     └──────────┘     └──────────┘           │
└─────────────────────────────────────────────────────────────────────────────┘
```

#### Authorization Matrix

| Resource | End User | Partner | Internal Service | Admin |
|----------|----------|---------|------------------|-------|
| Own wallet balance | ✅ Read | ✅ Read (their users) | ✅ Read/Write | ✅ Full |
| Other wallet balance | ❌ | ❌ | ✅ Read | ✅ Read |
| Create transaction | ✅ Own wallet | ✅ Their users | ✅ Any | ✅ Any |
| Reverse transaction | ❌ | ✅ Their transactions | ✅ Any | ✅ Any |
| Freeze wallet | ❌ | ❌ | ❌ | ✅ With approval |
| View audit logs | ❌ | ❌ | ❌ | ✅ Compliance role |
| System configuration | ❌ | ❌ | ❌ | ✅ Super admin |

#### Secret Management
```yaml
# Secrets hierarchy (HashiCorp Vault)
secret/
├── ledgerx/
│   ├── prod/
│   │   ├── database/           # DB credentials (rotated daily)
│   │   ├── redis/              # Redis auth
│   │   ├── kafka/              # SASL credentials
│   │   ├── jwt/                # JWT signing keys (rotated weekly)
│   │   └── encryption/         # Data encryption keys
│   ├── staging/
│   └── dev/
└── partners/
    ├── partner-a/              # Partner-specific API keys
    └── partner-b/
```

---

### 5. Design Tradeoffs & Rationale

#### Key Architectural Decisions

| Decision | Chosen Approach | Alternative | Why This Choice |
|----------|-----------------|-------------|-----------------|
| **Balance Storage** | Cached + computed from ledger | Direct balance column | Audit trail + consistency > raw speed |
| **Event Delivery** | At-least-once with outbox | Exactly-once (2PC) | Simpler, faster, idempotent consumers |
| **Database** | PostgreSQL (single logical DB) | Microservice DBs | ACID for financial data, simpler transactions |
| **Sharding** | Application-level by wallet_id | Database sharding | Flexibility, avoid cross-shard transactions |
| **API Style** | REST + async events | GraphQL / gRPC | Simpler client integration, better caching |
| **Reversal Model** | Compensating entries | Soft delete / mutation | Immutable audit trail, compliance |

#### Tech Stack Rationale

**Why PostgreSQL over distributed databases (CockroachDB, Spanner)?**
- Financial transactions require strict SERIALIZABLE isolation
- Simpler operational model for < 100K TPS
- Proven reliability for banking workloads
- Native partitioning handles scale requirements
- Lower cost and complexity

**Why Kafka over RabbitMQ/SQS?**
- Log-based architecture matches ledger philosophy
- Event replay for reconciliation and debugging
- Higher throughput for event streaming
- Consumer groups for parallel processing
- Exactly-once semantics with transactions

**Why FastAPI over Django/Go?**
- Async-first for I/O-bound financial operations
- Auto-generated OpenAPI for partner integration
- Type hints catch errors at development time
- Python ecosystem for data analysis/ML fraud detection
- Acceptable performance with uvicorn + gunicorn

#### CAP Theorem Positioning
```
              Consistency
                  △
                 /|\
                / | \
               /  |  \
              /   |   \
             / CP |    \
            /  ●  |     \
           / LedgerX     \
          /      |       \
         /       |        \
        /________|_________\
   Availability ──────── Partition
                          Tolerance

LedgerX chooses CP (Consistency + Partition Tolerance):
- Financial accuracy is non-negotiable
- Brief unavailability > incorrect balances
- Async operations provide eventual availability
```

---

### 6. Sequence Diagrams

#### Transfer Flow (Happy Path)
```
┌─────────┐     ┌─────────┐     ┌─────────┐     ┌──────────┐     ┌─────────┐
│ Client  │     │   API   │     │ Transfer│     │ Ledger   │     │  Kafka  │
│         │     │ Gateway │     │ Service │     │ (Postgres)│    │         │
└────┬────┘     └────┬────┘     └────┬────┘     └────┬─────┘     └────┬────┘
     │               │               │               │                │
     │ POST /transfer│               │               │                │
     │──────────────▶│               │               │                │
     │               │ Validate JWT  │               │                │
     │               │──────────────▶│               │                │
     │               │               │               │                │
     │               │               │ BEGIN TXN     │                │
     │               │               │──────────────▶│                │
     │               │               │               │                │
     │               │               │ Check balance │                │
     │               │               │──────────────▶│                │
     │               │               │◀──────────────│                │
     │               │               │               │                │
     │               │               │ INSERT entries│                │
     │               │               │──────────────▶│                │
     │               │               │               │                │
     │               │               │ INSERT outbox │                │
     │               │               │──────────────▶│                │
     │               │               │               │                │
     │               │               │ COMMIT        │                │
     │               │               │──────────────▶│                │
     │               │               │◀──────────────│                │
     │               │               │               │                │
     │               │◀──────────────│               │                │
     │◀──────────────│               │               │                │
     │  201 Created  │               │               │                │
     │               │               │               │ Poll outbox    │
     │               │               │               │───────────────▶│
     │               │               │               │                │
     │               │               │               │    Publish     │
     │               │               │               │───────────────▶│
     │               │               │               │                │
```

#### Hold → Capture Flow
```
┌─────────┐     ┌─────────┐     ┌─────────┐     ┌──────────┐
│Merchant │     │   API   │     │ Hold    │     │ Database │
│         │     │         │     │ Service │     │          │
└────┬────┘     └────┬────┘     └────┬────┘     └────┬─────┘
     │               │               │               │
     │ POST /holds   │               │               │
     │──────────────▶│──────────────▶│               │
     │               │               │ Check avail   │
     │               │               │──────────────▶│
     │               │               │◀──────────────│
     │               │               │               │
     │               │               │ INSERT hold   │
     │               │               │──────────────▶│
     │               │               │               │
     │               │               │ UPDATE balance│
     │               │               │ (held_balance)│
     │               │               │──────────────▶│
     │               │◀──────────────│               │
     │◀──────────────│ hold_id       │               │
     │               │               │               │
     │    ....       │   (time passes - up to 7 days)│
     │               │               │               │
     │ POST /capture │               │               │
     │──────────────▶│──────────────▶│               │
     │               │               │ Validate hold │
     │               │               │──────────────▶│
     │               │               │               │
     │               │               │ CREATE debit  │
     │               │               │ entries       │
     │               │               │──────────────▶│
     │               │               │               │
     │               │               │ UPDATE hold   │
     │               │               │ status=CAPTURED
     │               │               │──────────────▶│
     │               │◀──────────────│               │
     │◀──────────────│ transaction   │               │
```

---

## �📄 License

MIT License - see [LICENSE](LICENSE) for details.

---

## 🤝 Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for contribution guidelines.

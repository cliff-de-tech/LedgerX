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
8. [Getting Started](#getting-started)

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

## 📄 License

MIT License - see [LICENSE](LICENSE) for details.

---

## 🤝 Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for contribution guidelines.

---
name: postgres-orm-safety
description: design and modify Django ORM models and PostgreSQL-backed workflows safely. use when the task involves schema design, migrations, constraints, indexes, transactions, concurrency, or performance-sensitive data access.
---

Use Django ORM and PostgreSQL deliberately.

Always:
1. Prefer Django ORM first and justify any raw SQL.
2. Use `DecimalField` for money and avoid float-based financial calculations.
3. Add database-level safety with constraints, indexes, unique rules, and foreign keys where the business needs them.
4. Plan write operations with `transaction.atomic()` and row locking when concurrent edits could corrupt financial state.
5. Think through migration safety, backfills, defaults, and rollback impact before changing schema.
6. Keep queries readable and optimize only where measured or obviously necessary.

Output format:
- Goal
- Models or tables involved
- Constraints and indexes
- Transaction strategy
- Migration notes
- Validation and performance checks

---
name: cash-ops-domain
description: model and protect the business rules for cash boxes, shifts, financial movements, and closings. use when defining or changing the domain logic of a financial operations system that must replace spreadsheet workflows safely.
---

Treat the financial domain as the source of truth.

Always:
1. Identify the core entities, ownership rules, and lifecycle states involved.
2. Model boxes, shifts, openings, movements, adjustments, and closings in a way that is auditable and easy to reason about.
3. Use Decimal-based money handling and explicit validation rules.
4. Define invariants such as who can open, move, adjust, or close a box and under what conditions.
5. Consider concurrency, duplicate actions, partial failures, and correction workflows.
6. Add or recommend tests that prove totals and closure validations are trustworthy.

Typical invariants:
- one active box context per user when required by the workflow
- shift-aware registration for `T.M.` and `T.T.`
- closings cannot silently skip validation
- movement history should stay auditable
- balances should be derivable and explainable

Output format:
- Goal
- Core entities
- Business invariants
- Validation rules
- Risky edge cases
- Tests to add

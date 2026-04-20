# Context

Last updated: 2026-04-20

## Product Snapshot

- Django monolith for operational cash management and treasury.
- Main apps:
  - `cashops`: operational boxes, shifts, movements, closings, alerts
  - `treasury`: suppliers, payables, payments, account-control records, central cash, disponibilidad reports
  - `users`: custom user + role
  - `core`: entry/dashboard shell
- Current usage assumption for treasury:
  - internal control first
  - no real bank integration required for the current demo
  - account/bank concepts are being used as internal registry structures

## Important Domain Notes

- `CuentaPorPagar` is the source of debt state.
- Payments must be registered through domain services, not by direct model save.
- Partial and full payment status is derived from registered non-annulled payments.
- Cash payments (`PagoTesoreria.MedioPago.EFECTIVO`) should route through central cash, not a bank account.
- Demo focus should stay on:
  - suppliers
  - payables
  - payments
  - central cash
  - arqueos
  - monthly/internal availability visibility

## Repo Conventions For Agents

- Read this file before deep exploration.
- Update this file whenever you discover:
  - architectural constraints
  - critical bugs
  - temporary workarounds
  - useful commands
- Keep notes actionable and compact.

## Current Session

### Objective

- Stabilize treasury for a same-day internal-control demo.
- Reduce emphasis on bank integration features that are not part of the real operating model.

### Findings Before Fixes

- `treasury/forms.py` and `treasury/services.py` were out of sync:
  - supplier forms sent `direccion` and `sitio_web`, services did not accept them
  - payable forms sent `sucursal`, services did not accept it
  - bank account forms sent `sucursal`, services did not accept it
- `register_echeq_payment()` used an undefined variable and failed at runtime.
- `build_disponibilidades_snapshot()` used `Q(...)` without importing it.
- `link_payment_to_bank_movement()` tried to use a non-existing bank status `ACREDITADO`.
- accreditation filters used non-ORM properties and could fail when filtering from the UI.
- treasury dashboard copy overpromised bank integration for a workflow that is really internal-control oriented.

### Files Touched In This Session

- `AGENTS.md`
- `context.md`
- `docs/manual-demo-camino-feliz.md`
- `docs/manual-demo-camino-feliz.pdf`
- `docs/generate_demo_manual_pdf.py`
- `treasury/services.py`
- `treasury/views.py`
- `treasury/urls.py`
- `treasury/tests.py`

### Changes Applied

- `treasury/services.py`
  - accepted UI fields already exposed by forms:
    - suppliers: `direccion`, `sitio_web`
    - bank accounts: `sucursal`
    - payables: `sucursal`
  - fixed ECHEQ registration to use `PagoTesoreria.MedioPago.ECHEQ`
  - fixed payment-to-bank-movement status to `IMPACTADO`
  - fixed reconciliation typo `creada_en` -> `creado_en`
  - fixed disponibilidad snapshot import/use of `Q`
- `treasury/views.py`
  - started shifting copy toward internal control
  - added payment-in-cash route path and UI actions
  - made payment registration helper compatible with forms that do not have `cuenta_bancaria`
  - adjusted payment detail to tolerate cash payments with no bank account
  - fixed accreditation date filters to use `movimiento_bancario__fecha`
- `treasury/tests.py`
  - added coverage for ECHEQ registration
  - added coverage for cash payment flow + central cash movement
- `AGENTS.md`
  - established the repository rule that every AI agent must read and update `context.md`
- `docs/manual-demo-camino-feliz.md`
  - added a happy-path demo manual for non-technical viewers
  - aligned wording with current internal-control treasury scope
- `docs/generate_demo_manual_pdf.py`
  - added a reproducible PDF generator for the demo manual
- `docs/manual-demo-camino-feliz.pdf`
  - generated the final shareable PDF output

### Validation Results

- Passed:
  - `python manage.py test treasury.tests.TreasuryServiceTests treasury.tests.TreasuryViewTests treasury.tests_ep05 -v 2`
  - `python manage.py test treasury.tests.TreasuryAdminProtectionTests treasury.tests.TreasuryPermissionTests -v 2`
  - `python manage.py test treasury.tests.TreasuryServiceTests treasury.tests.TreasuryViewTests treasury.tests_ep05 -v 1`
- Treasury status after this session:
  - supplier create/update flow works again
  - payable create/update flow works again
  - payment-in-cash flow exists in UI and creates central cash movement
  - ECHEQ registration works again
  - disponibilidad snapshot and monthly close tests are green
  - a PDF demo guide can be regenerated from the markdown source

### Known Remaining Risks

- `cashops/test_migration_safety.py` still failed earlier because legacy user creation hit `users_user.dni` on SQLite test migration flow.
- `treasury/views.py` contains duplicated imports and some mojibake/encoding noise in labels. Not a blocker for behavior, but worth cleanup later.
- Monthly closing by `sucursal` still deserves a separate design pass if branch-specific treasury closings become mandatory.

## Useful Commands

- Focused treasury tests:
  - `python manage.py test treasury.tests.TreasuryServiceTests treasury.tests.TreasuryViewTests treasury.tests_ep05 -v 2`
- Non-treasury regression scan:
  - `python manage.py test cashops.tests cashops.tests_commands cashops.test_migration_safety users.tests core.tests -v 1`
- Quick enum check:
  - `python manage.py shell -c "from treasury.models import PagoTesoreria; print([x for x,_ in PagoTesoreria.EstadoBancario.choices])"`
- Regenerate the demo PDF:
  - `python docs/generate_demo_manual_pdf.py`

## Next Steps

- Clean `treasury/views.py` imports and encoded labels when there is time for non-demo refactor work.
- Decide whether `CuentaBancaria` should be renamed at UI level to reflect internal-control usage more clearly.
- Revisit branch-specific treasury closing if the operation requires separate monthly closure per `sucursal`.
- If demo scope remains internal-control only, keep bank reconciliation/accreditation out of the presentation path.

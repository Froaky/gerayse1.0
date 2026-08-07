# Context

Last updated: 2026-07-08

## Current Session

### EP-04/EP-10 Runtime Slices 2026-07-08 (US-4.9, US-10.13, US-10.14)

- Usuario pidio avanzar con las US/epicas pendientes. Se implemento el trio de tesoreria
  ya diagnosticado (plan P1-6 Slice A y B + consolidado):
- `US-4.9` (EP-04, cerrada): `CuentaBancaria.empresa` FK a `cashops.Empresa` (PROTECT,
  nullable por legacy). `clean()` deriva empresa desde la sucursal si falta, la exige solo
  en altas nuevas (`_state.adding`) y rechaza sucursal de otra empresa. Migraciones:
  `treasury/0022` (AddField) y `0023` (backfill: sucursal.empresa; si no hay sucursal,
  empresa unanime de los `sucursal_gasto` de sus movimientos; ambiguo queda NULL para
  completar por UI). Scoping centralizado en `bank_account_empresa_scope_query(empresa_ids,
  prefix=...)` (services.py) con escape legacy para cuentas sin empresa: se usa en
  listados de cuentas/saldos iniciales/movimientos/lotes/acreditaciones, snapshots y
  acceso directo por URL (update/toggle devuelven 404 fuera de contexto). `BankAccountForm`
  exige empresa y scopea empresa/sucursal por contexto activo. NOTA: los selectores de
  cuenta en forms de pagos/lotes/acreditaciones siguen sin scopear (comportamiento previo,
  anotado en la epica como mejora incremental).
- `US-10.13` (EP-10): `MovimientoBancario.clean()` exige rubro/sucursal/periodo para TODO
  debito `REGISTRADO` sin importar origen; `estado=ANULADO` queda exento (anular historicos
  incompletos sigue funcionando). Se elimino la regla vieja "rubro o categoria por clase"
  (subsumida; una categoria legacy sola ya no alcanza). `periodo_pago` se normaliza a dia 1
  en `clean()`. `create/update_bank_movement` ganaron `periodo_pago`; `BankMovementForm`
  ahora incluye periodo. `link_payment_to_bank_movement` hereda sucursal/periodo de la
  deuda pagada cuando faltan y valida con `full_clean` (antes hacia `save()` directo sin
  validar). Nuevo servicio `complete_bank_movement_imputation` + vista/URL
  `bancos/<pk>/imputar/` (en `TREASURY_WRITE_VIEW_NAMES`) que completa imputacion de
  cualquier origen, incluidos debitos vinculados a pagos que la edicion manual bloquea.
  Listado de banco: filtro `imputacion` (pendientes/imputados), resumen "Egresos pendientes
  de imputacion" y marca "PENDIENTE DE IMPUTAR" por fila; detalle muestra Periodo y boton
  "Completar imputación". DECISION ECONOMICA: `_pending_bank_treasury_expenses` ahora
  incluye origen MANUAL (antes solo EGRESO_TESORERIA), asi los 73 debitos historicos de
  prod aparecen en "Gasto sin imputar" hasta completarse; PAGO_TESORERIA sigue fuera del
  gasto tesoreria economico (esa plata ya entro como deuda, evitar doble conteo).
- `US-10.14` (EP-10): `build_financial_period_snapshot` devuelve `debt_vs_bank_difference`
  (= `total_bank_balance` - `pending_total`, misma base US-4.8/US-10.6, sin caja fuerte ni
  acreditacion pendiente) y `debt_vs_bank_covered`; tarjeta "Banco menos deuda pendiente"
  en el dashboard con fecha de corte y leyenda cubre/no cubre. SUPUESTO documentado en la
  epica: "deuda pendiente del periodo" = deuda viva total a la fecha de corte (mismo numero
  que la tarjeta "Deuda pendiente"), no deuda del mes de referencia; y sigue abierta la
  ambiguedad deuda-vs-acreditacion-pendiente anotada el 2026-07-08 para confirmar con
  el cliente.
- Files touched: `treasury/models.py`, `treasury/services.py`, `treasury/forms.py`,
  `treasury/views.py`, `treasury/urls.py`, `treasury/admin.py`,
  `treasury/management/commands/reporte_sin_sucursal.py` (cuentas sin empresa ahora se
  reportan por `empresa__isnull`), `templates/treasury/dashboard.html`,
  `treasury/migrations/0022-0023`, `treasury/tests.py` (21 tests nuevos en
  `EP04BankAccountEmpresaTests`, `EP10BankDebitImputationTests`,
  `EP10DebtVsBankCoverageTests`; fixtures de debitos actualizados con imputacion completa;
  tests de historicos pasados a `objects.create` para simular filas pre-regla),
  `docs/epics/EP-04...md`, `docs/epics/EP-10...md`, `docs/epics/README.md`, `context.md`.
- Validacion: suite completa `py -3.14 manage.py test` con `PYTHONPATH=.venv\Lib\site-packages`
  => 283 tests OK (1 skip); `makemigrations --check --dry-run` => sin drift;
  `compileall cashops treasury users core` => OK. En esta maquina `py -3.13` no existe;
  usar `py -3.14` (el `.venv\Scripts\python.exe` sigue roto, solo sirve su `site-packages`).
- Deploy note: al deployar corren `0022`+`0023`; en prod las 2 cuentas sin sucursal quedan
  con empresa si sus egresos imputados son unanimes; si alguna queda "Sin empresa asignada"
  en el listado de cuentas, se completa editandola (el form ya exige empresa).
- Review adversarial post-implementacion (workflow multi-agente) confirmo defectos que se
  corrigieron en la misma sesion:
  - `clase=RETIRO` quedo EXENTO de la imputacion obligatoria y EXCLUIDO de gasto tesoreria
    mapeado/pendiente: un retiro banco->caja fuerte mueve fondos, no es gasto; contarlo
    duplicaba contra los `EGRESO_ADMIN` de caja fuerte. `reporte_sin_sucursal` sigue
    listando retiros sin sucursal como incompletos (diagnostico read-only, no bloquea);
    ajustarlo queda como detalle menor pendiente.
  - "Gasto sin imputar" economico ahora tambien scopea por empresa DUENA DE LA CUENTA
    (los pendientes tienen sucursal NULL y el escape isnull los filtraba a TODAS las
    empresas); el resumen del listado de banco y la alerta economica vuelven a coincidir.
  - La vista/form de imputacion quedaron scopeados: URL directa a movimiento de cuenta de
    otra empresa -> 404; el selector de sucursal se limita a la empresa de la cuenta (o al
    contexto activo si la cuenta es legacy sin empresa); el servicio rechaza cruce
    cuenta-empresa vs sucursal-empresa. Cuenta inactiva -> mensaje claro en vez de error
    de campo inexistente. OJO: detail/update/link de movimientos siguen sin scoping por
    empresa (gap PRE-EXISTENTE, no se toco en este slice; anotar si se quiere cerrar).
  - Tarjeta "Banco menos deuda pendiente" gateada a la vista consolidada (en vista por
    sucursal el banco excluye cuentas de empresa sin sucursal y el numero enganaba).
  - `pending_total` (deuda pendiente del snapshot financiero) bajo contexto de empresa
    ahora INCLUYE deudas sin sucursal (antes las omitia y sobreestimaba cobertura); con
    contexto vacio devuelve 0. Esto tambien afecta la tarjeta "Deuda pendiente".
  - `link_payment_to_bank_movement` ya no pisa un rubro cargado con el rubro NULL de una
    categoria legacy; el error de vinculacion se muestra legible (join de messages, no
    dict crudo).
  - `periodo_pago` se oculta/limpia en el form de movimiento bancario cuando tipo=CREDITO
    (mismo mecanismo JS que sucursal, generalizado en `form_card.html`).
  - Resumen "Egresos pendientes de imputacion" del listado se calcula ANTES de los
    filtros del usuario (backlog real del contexto, no del filtro).
  - Copy unificado sin acentos en el flujo de imputacion ("Completar imputacion").
  - Riesgo residual documentado (sin fix automatico posible): un debito manual que en
    realidad pago una `CuentaPorPagar` y se "completa" por el worklist en vez de
    vincularse duplica el gasto economico (deuda + gasto banco). Mitigacion: el form de
    imputacion advierte explicitamente usar "Vincular a pago" en ese caso.
- Validacion final post-fixes: suite completa 287 tests OK (1 skip), `makemigrations
  --check` sin drift, `compileall` OK. Los agentes del review dejaron 4 archivos
  `treasury/test_*_tmp.py` sueltos que fueron eliminados (no eran del repo).

### Backlog Intake 2026-07-08

- Usuario pidio traducir 4 pedidos funcionales a epicas/US tecnicas para avanzar, y ademas
  dejar un plan/workflow separado de presupuesto para otro grupo de pedidos, explicito como
  "no implementar sin mi OK".
- Grupo 1 (agregado a backlog activo, para avanzar):
  - "las acreditaciones van todas juntas, por empresa no por sucursal" -> gap real es que
    `CuentaBancaria` no tiene campo `empresa` (solo `sucursal` opcional); la lectura financiera
    ya consolida acreditaciones sin repartir por sucursal desde `EP-10` `US-10.11`. Agregado
    `EP-04` `US-4.9`.
  - "agregar periodo en movimiento bancario" -> el campo `periodo_pago` ya existe en
    `MovimientoBancario`; lo que falta es exigirlo (con rubro y sucursal) en todo egreso
    bancario, no solo en `origen=EGRESO_TESORERIA`. Cubierto junto con el siguiente punto.
  - "todo lo que sea banco, obligatorio los 3 parametros" -> mismo gap: hoy `rubro_operativo`,
    `sucursal_gasto` y `periodo_pago` solo son obligatorios para `origen=EGRESO_TESORERIA`
    (ver `treasury/models.py` `MovimientoBancario.clean()`); coincide con el plan P1-6 Slice B
    ya anotado el 2026-07-03. Agregado `EP-10` `US-10.13`.
  - "en el consolidado, diferencia entre pendiente y banco" -> interpretado como deuda
    pendiente (`CuentaPorPagar`) vs disponibilidad real en banco, distinto de la acreditacion
    pendiente que ya cubre `US-10.5`. Queda anotada la ambiguedad para confirmar con el
    cliente. Agregado `EP-10` `US-10.14`.
  - Backlog files actualizados: `docs/epics/EP-04-bancos-y-conciliacion.md` (US-4.9),
    `docs/epics/EP-10-situacion-financiera-y-alertas-consolidadas.md` (US-10.13, US-10.14),
    `docs/epics/README.md` (EP-04 y EP-10 reabiertas).
- Grupo 2 (propuesta para presupuestar, NO aprobada, NO implementar sin OK):
  - usuarios cajero por sucursal con egreso de caja cargado como deuda; validacion de efectivo
    para que un ingreso cargado por un cajero no cuente hasta ser validado por un usuario con
    permiso especifico; permisos mas finos (por accion, no solo lectura/escritura); deudas
    impactando situacion economica al cargarse y financiera al pagarse.
  - Diagnostico: caja por sucursal ya existe (`EP-08`); rol "cajero" acotado y permiso por
    accion NO existen (hoy los permisos son por modulo completo, `EP-09` `US-9.11` sigue
    pendiente); estado "pendiente de validar" para efectivo NO existe; egreso de caja generando
    `CuentaPorPagar` automatica NO existe; la regla de deuda en economica (al cargarse) vs
    financiera (al pagarse) YA esta implementada hoy (`build_economic_period_snapshot` usa
    `importe_total` de deuda no anulada; `build_financial_period_snapshot` solo refleja pagos
    reales), pendiente solo blindarla con tests explicitos.
  - Nuevo documento: `docs/epics/PROPUESTA-EP-13-cajeros-y-validacion-efectivo.md`, con slices,
    orden sugerido, riesgos y preguntas abiertas para cotizar. Referenciado en
    `docs/epics/README.md` bajo una seccion separada de "Propuestas para presupuestar", fuera
    del orden de implementacion activo.
- Validacion: solo se tocaron archivos markdown de backlog y este archivo; no se corrio
  la suite porque no hubo cambio de codigo ni de esquema.

### Hardening de produccion 2026-07-03 (slices seguros, sin migraciones)

- Auditoria general del sistema pedida por el usuario; se acordo aplicar mejoras sin
  tocar datos ni esquema (esta en produccion). Ninguna de estas slices agrega migraciones.
- Slice 1 (suite verde): `cashops/tests_commands.py` fallaba con `NOT NULL constraint failed`
  porque el INSERT raw de `_create_legacy_expense_without_category` no seteaba columnas que
  se volvieron NOT NULL (`actualizado_en`, `estado`, `motivo_anulacion`). Se completaron. Fix
  solo de test, sin impacto en producto.
- Slice 2 (boton "Reiniciar datos"): antes solo lo protegia una nota manual en `PRODUCCION.md`.
  Ahora esta gateado por setting `ENABLE_DANGER_RESET` (default = `DEBUG`). La vista
  `cashops.reset_operational_data` responde 404 si esta apagado; el boton no se renderiza
  (menu Config, Empresas, Disponibilidades) via `enable_danger_reset` en el context processor.
  Archivos: `config/settings.py`, `core/context_processors.py`, `cashops/views.py`,
  `treasury/views.py`, `templates/cashops/layout.html`, `PRODUCCION.md`, tests en cashops/treasury.
- Slice 3 (higiene): se quitaron del tracking `runserver.err.log`/`runserver.out.log` (eran de
  otra maquina, ruta `C:\code\gerayse1.0`) y se agrego `*.log` al `.gitignore`.
- Slice 4 (defaults seguros): confirmado en Railway que prod setea `DEBUG=False` y
  `DJANGO_SECRET_KEY` real. En `config/settings.py`: `DEBUG` ahora default `False`; si
  `DEBUG=False` con la SECRET_KEY insegura por default, se levanta `ImproperlyConfigured`
  (fail-hard). La guarda exime `runserver` y `test` (RUNNING_DEV_SERVER / RUNNING_TESTS) para
  que dev local y CI anden sin `.env`. Solo muerde al servir por WSGI/gunicorn con key insegura.
  Se agrego `.env` local minimo (gitignoreado, solo `DEBUG=True`, sin DATABASE_URL -> SQLite)
  para mantener el flujo de dev previo. Verificado: prod key-real arranca (reset off), prod
  key-insegura falla; env vars de Railway pisan al `.env`.
- Slice 5 (CI): nuevo `.github/workflows/ci.yml` que en push/PR a main corre check,
  makemigrations --check, compileall y la suite completa (Python 3.13, SQLite, DEBUG=True).
- Estado suite: 257 tests OK (1 skip), makemigrations sin drift, cero migraciones agregadas.
- OBSERVACION de seguridad (no actuada): en Railway el superusuario es `admin` /
  `admin123!` (`DJANGO_SUPERUSER_*`). Conviene rotar a una password fuerte; queda a decision
  del usuario.
- Diagnostico P1-6 corrido en prod (`reporte_sin_sucursal`): deudas, compromisos y egresos
  admin de caja central sin sucursal = 0 (lo sensible esta limpio). Unicos NULL relevantes:
  2 cuentas bancarias sin sucursal y 73 debitos bancarios sin `sucursal_gasto`.
- Reglas de dominio CONFIRMADAS por cliente 2026-07-03 (ver detalle en skill de tesoreria,
  `references/gerayse-tesoreria-scope.md` seccion 10):
  - `MAPOGO SRL` = 1 sola sucursal (`Vivre`); su cuenta de banco es de esa empresa.
  - `ARMADI SRL` = varias sucursales, TODAS sincronizadas a UNA sola cuenta -> la cuenta es a
    nivel EMPRESA, no de una sucursal. => la cuenta bancaria debe poder pertenecer a una empresa.
  - No hay cuentas compartidas entre empresas; solo 2 bancos.
  - TODO gasto bancario (impuestos, comisiones, transferencias) se imputa POR SUCURSAL; no
    existe gasto bancario "comun" por diseno. Un DEBITO sin `sucursal_gasto` es un hueco de carga.
  - Efecto de un egreso sin `sucursal_gasto`: cae en "Gasto sin imputar" de Situacion economica
    y NO suma al gasto economico por rubro/sucursal hasta tener rubro+sucursal+periodo; si afecta
    saldo bancario/disponibilidad.
- Plan P1-6 (pendiente de OK del usuario):
  - Slice A (fix fuga cross-company, migracion compatible): agregar `empresa` a `CuentaBancaria`,
    backfill cuenta#2->MAPOGO / cuenta#1->ARMADI, scopear cuentas y movimientos por empresa.
  - Slice B (calidad de imputacion, sin migracion de datos): exigir `sucursal_gasto` en alta de
    debitos/egresos bancarios + vista/worklist para que la usuaria complete los 73 existentes.

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
  - bank reconciliation is manual-assisted by the system and must not be automated unless the user explicitly requests that later

## Important Domain Notes

- Standing rule reaffirmed 2026-06-04: companies are isolated by default.
  - Every company-scoped view, form, service, dashboard and report must respect the selected company context.
  - The selected context can include one or more companies through `empresa_ids`.
  - Branches belong to one company; branch filters, selectable branches, boxes, accounts, debts, payments, bank movements and totals must not cross into unselected companies.
  - Any new work touching `empresa` or `sucursal` must check list visibility, form querysets, direct URL access, service validation and tests for cross-company leakage.
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

## Engineering Rules

- Repo-level engineering guidance now lives in `docs/engineering-guidelines.md`.
- Architecture expectation:
  - models own invariants
  - services own money/debt/closing/payment workflows and shared formulas
  - forms validate input and adapt to services
  - views stay thin
  - templates do not hold business rules
- Compatibility expectation:
  - harden legacy data in steps: compatible schema, backfill, then required constraint
- Testing expectation:
  - changes touching money, debt, permissions, migrations, dashboards or operational controls require proportional tests

## Current Session

### Cashops Semaphore Compact UI 2026-07-01

- User requested the `Semaforo operativo` cards take less vertical screen space.
- Behavior changed: the operational semaphore now uses dashboard-only compact classes for its item wrapper and metric cells, reducing padding, gaps and metric height while preserving the same rubro, spent amount, consumed percentage, limit and status labels.
- Scope: UI/layout only; no changes to cashops services, views, models, permissions, alert rules or dashboard formulas.
- Files touched: `templates/cashops/dashboard.html`, `templates/cashops/layout.html`, `context.md`.
- Evidence: `py -3.14 manage.py test cashops.tests.CashopsViewTests.test_dashboard_shows_operational_semaphore_and_active_alert -v 2` with `PYTHONPATH=.venv\Lib\site-packages` => OK; `py -3.14 -m compileall cashops` => OK; `py -3.14 manage.py makemigrations --check --dry-run` => no changes detected. A first focused test command used an outdated test name and was rerun with the correct test name.

### Treasury Dashboard Visual Alignment 2026-07-01

- User reported the lower treasury dashboard cards looked overlapped, asymmetric and visually rough.
- Behavior changed: the three lower two-column dashboard rows now use a dashboard-only `dashboard-pair` class so paired cards stretch to the same row height, reducing uneven gaps without moving data, formulas, filters or actions.
- Scope: UI/layout only; no changes to treasury services, views, models, permissions or financial calculations.
- Files touched: `templates/treasury/dashboard.html`, `templates/treasury/layout.html`, `context.md`.
- Evidence: `py -3.14 manage.py test treasury.tests.TreasuryViewTests.test_dashboard_supports_period_and_branch_financial_view -v 2` with `PYTHONPATH=.venv\Lib\site-packages` => OK; `py -3.14 -m compileall treasury` => OK; `py -3.14 manage.py makemigrations --check --dry-run` => no changes detected.

### Users Minimal Create Flow 2026-07-01

- User requested that creating an operational user should only ask for username, first name, last name and a temporary password, then return to the user list with a temporary bottom-right success message.
- Behavior changed: `user_create` now uses a minimal create-only form, sets the temporary password, marks `must_change_password=True`, redirects to `users:user_list`, and shows `usuario creado correctamente` through Django messages.
- Compatibility: full user editing, role assignment, fixed-user branch assignment, company access and active state remain available through edit/detail flows; fields were hidden from creation only, not removed from the model.
- Files touched: `users/forms.py`, `users/views.py`, `templates/cashops/layout.html`, `users/tests.py`, `context.md`.
- Evidence: `py -3.14 manage.py test users.tests -v 1` with `PYTHONPATH=.venv\Lib\site-packages` => 40 OK; `py -3.14 -m compileall users` => OK; `py -3.14 manage.py makemigrations --check --dry-run` => no changes detected.

### Dashboard/Economic Totals Investigation 2026-06-27

- User reported mismatches in dashboard and economic readings:
  - `Caja fuerte general` shows about 66M ARS difference.
  - `Situacion economica` `Ventas base` should exclude panificacion billing and include only the intended period base.
  - `Resultado economico` should subtract caja expenses, treasury expenses and period debt from base sales.
  - Central cash book should show period income/expense totals to identify the branch/source where a movement is missing.
  - Some mistaken duplicated caja entries cannot yet be removed cleanly from the user's flow.
- Findings:
  - `build_economic_period_snapshot()` counted all active income channel codes, including channels marked `excluir_de_totales` such as `PANIFICACION`.
  - Treasury financial/economic snapshots did not filter out `MovimientoCaja.estado=ANULADO` or `Caja.estado=ANULADA`, while cashops period reports already did.
  - Anulling a closed box annulled its operational movements but did not reverse the `MovimientoCajaCentral` created by `close_box()`, leaving central cash inflated by deleted/duplicated boxes.
- Behavior changed:
  - Economic `Ventas base` now excludes income channels configured with `excluir_de_totales` and ignores annulled movements/boxes.
  - Financial cash period totals and digital sales now ignore annulled movements/boxes.
  - Whole-box annulment now creates an audited central-cash reversal movement for the closing transfer instead of deleting the original central-cash record.
- User-facing impact: panification billing and annulled duplicate cajas stop inflating dashboard/economic totals; deleting a closed duplicated caja also corrects `Caja fuerte general`.
- Files touched: `treasury/services.py`, `treasury/tests.py`, `cashops/services.py`, `cashops/tests.py`, `context.md`.
- Validation:
  - `py -3.13 manage.py test treasury.tests.TreasuryServiceTests.test_economic_period_snapshot_excludes_panificacion_and_annulled_cash_movements treasury.tests.TreasuryServiceTests.test_financial_period_snapshot_excludes_annulled_cash_movements cashops.tests.CashopsViewTests.test_annul_closed_box_reverses_central_cash_closure_movement -v 2` with `PYTHONPATH=.venv\Lib\site-packages`: 3 OK.
  - `py -3.13 manage.py test treasury.tests.TreasuryServiceTests treasury.tests.TreasuryViewTests -v 1` with `PYTHONPATH=.venv\Lib\site-packages`: 75 OK.
  - `py -3.13 manage.py test cashops.tests.CashopsServiceTests cashops.tests.CashopsViewTests -v 1` with `PYTHONPATH=.venv\Lib\site-packages`: 89 OK.
  - `py -3.13 -m compileall cashops treasury` with `PYTHONPATH=.venv\Lib\site-packages`: OK.
  - `py -3.13 manage.py makemigrations --check --dry-run` with `PYTHONPATH=.venv\Lib\site-packages`: no changes detected.
  - `py -3.14` was unavailable in this environment; used installed Python 3.13.
- Client-verification note:
  - To validate panification exclusion, client must check that internal sales were loaded with income channel `PANIFICACION` or another channel marked `excluir_de_totales`; exclusion is by channel, not by rubro name alone.
  - To validate treasury expenses in economic view, client must load them from `Registrar egreso` with `Rubro`, `Sucursal correspondiente`, and `Periodo que se esta pagando`; manual central-cash adjustments without those fields affect financial cash but do not enter economic expenses.
  - Focused recheck 2026-06-27: panification/excluded income, treasury expense persistence, and dashboard economic/financial tests passed; one initial test command used a wrong test name and was rerun with the correct dashboard test.
- Client-closeout fixes 2026-06-28:
  - Branch financial `Banco debitos` now follows `MovimientoBancario.sucursal_gasto` for debits, with account branch only as a legacy fallback when no expense branch exists. This covers expenses paid from Terminal/global bank but assigned to Heladeria or another branch.
  - Branch dashboard hides `Banco creditos` and `Banco neto`; those remain consolidated-only because card/bank accreditations are common money without branch assignment. Branch view still shows bank debits imputable to that branch.
  - Economic snapshot already uses `sucursal_gasto`/`periodo_pago` for treasury cash/bank expenses; added regression coverage for central cash paid from one branch but economically assigned to another.
  - Central cash book now has an opt-in `Imputacion` filter for pending or complete admin expenses, so administration can list expenses missing branch/rubro/period. `INGRESO_CAJA` rows without `periodo_pago` show the period derived from the cash date instead of `sin periodo`.
  - Files touched: `treasury/services.py`, `treasury/forms.py`, `treasury/views.py`, `templates/treasury/dashboard.html`, `treasury/tests.py`, `context.md`.
  - Evidence: focused 4-test regression OK; `py -3.13 manage.py test treasury.tests.TreasuryServiceTests treasury.tests.TreasuryViewTests -v 1` with `PYTHONPATH=.venv\Lib\site-packages` => 78 OK; `py -3.13 manage.py makemigrations --check --dry-run` => no changes detected; `py -3.13 -m compileall treasury cashops` => OK; `git diff --check` => only CRLF working-copy warnings.

### Bank Movement Corrections And User Settings 2026-06-19

- User reported wrong bank movements and requested visible `Editar` and `Eliminar` buttons with confirmation.
- Decision in progress: treat bank movement "delete" as audited annulment, not physical delete, so balances stop counting the movement but the trace remains.
- Linked/generated bank movements need extra care: do not silently edit/delete records whose source is payment/accreditation without preserving accounting trace.
- User also requested UTF-8 Spanish labels with correct accents, personal user settings, password change with old password and double confirmation, and pointer cursor on clickable buttons.
- Implemented direction:
  - manual registered bank movements can be edited after confirmation; balances/reporting read the persisted corrected values.
  - manual registered bank movements can be "eliminated" only by audited annulment (`estado=ANULADO`, reason, user, timestamp); financial snapshots and lists exclude them.
  - bank movements linked to treasury payments or card accreditations are blocked from direct bank edit/delete.
  - users now have `Mi cuenta` from the username chip to edit own basic data and change password with current password plus double confirmation.
  - visible Spanish copy and clickable cursor styles are being normalized in active UI templates.
- Files touched for this slice:
  - `treasury/models.py`, `treasury/services.py`, `treasury/forms.py`, `treasury/views.py`, `treasury/urls.py`, `treasury/admin.py`
  - `templates/treasury/confirm_action.html`, treasury list/detail/form/layout/dashboard/accreditation/reconciliation templates
  - `users/forms.py`, `users/views.py`, `users/urls.py`, `users/models.py`, user account/password/templates/tests
  - shared cursor/layout CSS in `static/css/gerayse.css`, `templates/cashops/layout.html`, plus selected visible copy in cashops templates
  - migrations `treasury/0019_bank_movement_annulment.py`, `treasury/0020_alter_movimientobancario_clase_and_more.py`, `users/0009_utf8_spanish_user_labels.py`
- Validation:
  - focused new bank/user tests passed: 10 tests OK
  - `py -3.14 manage.py test treasury.tests users.tests -v 1` with `PYTHONPATH=.venv\Lib\site-packages`: 115 tests OK, 1 skipped
  - `py -3.14 -m compileall treasury users`: OK
  - `py -3.14 manage.py makemigrations --check --dry-run`: no changes detected
  - search for common broken Spanish strings found only benign partial matches (`seleccionados`) and old historical migrations.

### EP-04 Runtime Slice 2026-06-19

- User asked to work on permissions and bank movements.
- Implemented `EP-04` `US-4.8` saldo inicial bancario por cuenta.
- Decision: initial bank balance must be an audited starting point for account balances, not a real bank movement, accreditation or transfer.
- Permission decision: reuse existing treasury module permissions; read can view initial balances and write is required to create/correct them.
- Behavior:
  - new `SaldoInicialCuentaBancaria` stores one audited starting balance per account/reference date
  - correcting the same account/date updates the record and stores previous value, correction user/date and motive
  - bank availability reads `initial balance + real bank movements from the reference date through the cutoff date`
  - initial balances are shown from the bank account list and the treasury dashboard but are not `MovimientoBancario`
- Files touched:
  - `treasury/models.py`, `treasury/forms.py`, `treasury/services.py`, `treasury/views.py`, `treasury/urls.py`, `treasury/admin.py`
  - `templates/treasury/dashboard.html`
  - `treasury/migrations/0018_saldo_inicial_cuenta_bancaria.py`
  - `treasury/tests.py`
  - `docs/epics/EP-04-bancos-y-conciliacion.md`, `docs/epics/README.md`, `context.md`
- Validation:
  - focused initial-balance service/view tests passed: 6 tests OK
  - `py -3.14 manage.py test treasury.tests treasury.tests_ep05 -v 1` with `PYTHONPATH=.venv\Lib\site-packages` passed: 80 tests OK, 1 skipped
  - `py -3.14 -m compileall treasury` with `PYTHONPATH=.venv\Lib\site-packages` passed
  - `py -3.14 manage.py makemigrations --check --dry-run` still reports only the pre-existing `treasury\migrations\0019_alter_movimientocajacentral_tipo.py` drift

### Backlog Intake 2026-06-12

- New user feedback:
  - user wants to see the composition of sales from the system whenever a total sale is shown, instead of inferring it from isolated records
  - user also wants a complete tracking view of loaded cajas: by sucursal, one or many sucursales, operational date, status, and point where the load was left unfinished so they can continue it
  - closed cajas must remain visible in listings, not only active/open ones
- Scope decision:
  - keep this intake in `EP-08` because both requests are operational caja traceability, not treasury financial reading and not economic rubro composition
  - explicit assumption: `composicion de la venta` here means drilldown of operational sales/totals shown in caja views or listings; economic rubro composition is already covered by `EP-11` `US-11.10`
- Backlog files updated:
  - `docs/epics/EP-08-ajustes-operativos-de-caja-y-sucursales.md`: added `US-8.16`, `US-8.17` and `US-8.18`, and strengthened epic scope/rules/closure for caja follow-up, sales composition drilldown and caja activity history
  - `docs/epics/README.md`: expanded `EP-08` pending scope to `US-8.14` through `US-8.18`
- Validation:
  - reviewed numbering and epic fit; application tests not run because this intake only changed backlog markdown and project memory

### EP-08 Runtime Slice 2026-06-12

- Continued after backlog intake to implement the caja follow-up slice.
- Implemented:
  - new `Seguimiento de cajas` view with filters by one or many sucursales, operational date range and estado, showing open and closed cajas in the active company context
  - each caja row now shows responsible user, last activity, loaded sales total, cash balance and quick actions to retake, inspect composition or review history
  - new caja detail view with sales/income breakdown, full movement list and auditable activity timeline from opening to closing or latest activity
  - dashboard now links to tracking and to the detailed caja view; selected caja shows `Ventas cargadas` with direct drilldown to composition
  - movement labels in caja templates now render meaningful names for seeded channels and operational movement types
- Scope decision:
  - `US-8.16`, `US-8.17` and `US-8.18` are implemented
  - `US-8.15` stays pending because the current tracking view lists real loaded cajas but does not yet surface explicit expected-missing cajas by turno/sucursal
- Files touched:
  - `cashops/services.py`, `cashops/views.py`, `cashops/urls.py`, `cashops/tests.py`
  - `templates/cashops/dashboard.html`, `templates/cashops/layout.html`, `templates/cashops/partials/movement_list.html`
  - `templates/cashops/box_tracking.html`, `templates/cashops/box_detail.html`
  - `docs/epics/EP-08-ajustes-operativos-de-caja-y-sucursales.md`, `docs/epics/README.md`, `context.md`
- Validation:
  - `py -3.14 manage.py test cashops.tests.CashopsViewTests -v 1` with `PYTHONPATH=.venv\Lib\site-packages` passed: 44 tests OK
  - `py -3.14 manage.py test cashops.tests -v 1` with `PYTHONPATH=.venv\Lib\site-packages` passed: 90 tests OK
  - `py -3.14 -m compileall cashops` with `PYTHONPATH=.venv\Lib\site-packages` passed
  - `git diff --check` returned only CRLF working-copy warnings

### EP-10 Runtime Slice 2026-06-08

- User asked to keep advancing pending stories.
- Selected next implementable slice: `EP-10` `US-10.11` + `US-10.12`.
- Goal: financial dashboard should treat card/bank accreditations as consolidated common money while branch views include central-cash expenses imputable to that branch.
- Scope guardrail: do not derive debt from bank/accreditation records; do not duplicate central-cash expenses as operational cash movements.
- Implemented:
  - `build_financial_period_snapshot()` now calculates digital sales/accreditations as consolidated by selected company/period, not by selected branch.
  - Branch financial views now expose `central_cash_income_period`, `central_cash_expense_period`, and `central_cash_net_period` using central-cash movement scope with `sucursal_gasto`.
  - Treasury dashboard shows `Egresos caja fuerte` and explains that accreditations are common consolidated money while egresos remain imputable by branch.
  - `EP-10` `US-10.11` and `US-10.12` marked done; `docs/epics/README.md` marks `EP-10` implemented again.
- Files touched: `treasury/services.py`, `templates/treasury/dashboard.html`, `treasury/tests.py`, `docs/epics/EP-10-situacion-financiera-y-alertas-consolidadas.md`, `docs/epics/README.md`, `context.md`.
- Validation:
  - `C:\Users\theco\AppData\Local\Programs\Python\Python313\python.exe -m compileall treasury` with `PYTHONPATH=.venv\Lib\site-packages` passed.
  - Focused EP-10 tests passed: consolidated accreditation branch view, central-cash branch expense, and dashboard branch view.
  - Broader treasury regression passed: `C:\Users\theco\AppData\Local\Programs\Python\Python313\python.exe manage.py test treasury.tests treasury.tests_ep05 -v 1` with `PYTHONPATH=.venv\Lib\site-packages`: 68 tests OK, 1 skipped.
  - `git diff --check` passed with only CRLF normalization warnings.

### EP-11 Runtime Slice 2026-06-08

- Continuing pending work after EP-10.
- Selected slice: `EP-11` `US-11.7`.
- Goal: economic snapshot should include administrative treasury expenses paid from central cash/bank when they have rubro, sucursal and periodo, without duplicating operational cash expenses or payable debt.
- Formula decision: treasury expenses enter by `MovimientoCajaCentral.periodo_pago`/`MovimientoBancario.periodo_pago`, grouped by `rubro_operativo`; operational caja expenses and debt remain separate components.
- Implemented:
  - `build_economic_period_snapshot()` now includes treasury expenses from central cash admin expenses and bank debits when rubro, sucursal and period are present.
  - Economic dashboard shows `Gasto tesoreria` separately from operational cash expense and period debt.
  - Incomplete treasury expenses are exposed as pending imputation totals/counts and excluded from rubro totals until sucursal/rubro/period are complete.
  - `EP-11` `US-11.7` marked done; remaining EP-11 backlog is `US-11.8` to `US-11.10`.
- Files touched: `treasury/services.py`, `templates/treasury/dashboard.html`, `treasury/tests.py`, `docs/epics/EP-11-rentabilidad-y-situacion-economica.md`, `docs/epics/README.md`, `context.md`.
- Validation:
  - Focused EP-11 tests passed: economic snapshot includes treasury expenses and dashboard branch view renders `Gasto tesoreria`.
  - Broader treasury regression passed after EP-11: `C:\Users\theco\AppData\Local\Programs\Python\Python313\python.exe manage.py test treasury.tests treasury.tests_ep05 -v 1` with `PYTHONPATH=.venv\Lib\site-packages`: 69 tests OK, 1 skipped.
  - `git diff --check` passed with only CRLF normalization warnings.

### EP-05 Runtime Slice 2026-06-08

- User asked to advance pending US/EP work.
- Selected next implementable slice: `EP-05` `US-5.10` + `US-5.11`.
- Goal: central-cash availability book should show administrative expense branch/rubro detail and period income/expense totals derived from listed central-cash movements.
- Scope guardrail: do not mix bank movements into central-cash totals; keep branch filters constrained by active company context.
- Implemented:
  - central-cash movement scope now includes `EGRESO_ADMIN` by `sucursal_gasto` when filtering by sucursal.
  - company-scoped central-cash queries include global/legacy movements only when they are not imputable to another selected-out company.
  - central-cash list now filters by year/month/sucursal, shows period income/expense totals, and exposes branch, rubro, period and user metadata per movement.
  - disponibilidades report shows central-cash income/expense totals and links to the filtered central-cash detail.
- Files touched: `treasury/services.py`, `treasury/views.py`, `templates/treasury/disponibilidades_report.html`, `treasury/tests.py`, `treasury/tests_ep05.py`, `docs/epics/EP-05-flujo-de-disponibilidades.md`, `docs/epics/README.md`, `context.md`.
- Validation:
  - `C:\Users\theco\AppData\Local\Programs\Python\Python313\python.exe -m compileall treasury` with `PYTHONPATH=.venv\Lib\site-packages` passed.
  - Focused tests passed with Python 3.13 + venv site-packages: `treasury.tests_ep05.EP05DisponibilidadesTests`, `treasury.tests.TreasuryViewTests.test_central_cash_book_shows_admin_expense_imputation_and_period_totals`, and `treasury.tests.TreasuryViewTests.test_disponibilidades_report_exposes_reset_and_company_scoped_global_cash`.
  - Broader treasury regression passed: `C:\Users\theco\AppData\Local\Programs\Python\Python313\python.exe manage.py test treasury.tests treasury.tests_ep05 -v 1` with `PYTHONPATH=.venv\Lib\site-packages`: 66 tests OK, 1 skipped.
  - Post-cleanup focused recheck passed: branch-scope service test and central-cash book view test.
  - `git diff --check` passed with only CRLF normalization warnings.
- Known unrelated validation issue: `manage.py makemigrations --check` still reports existing treasury drift for `treasury\migrations\0018_alter_movimientocajacentral_tipo.py`; this slice did not change models.

### Backlog Intake 2026-06-08

- User feedback from WhatsApp splits into pending backlog for:
  - reassigning historical administrative/operational expenses from old branch `EB1` to the new bakery/pastry branch without losing audit trail
  - correcting already loaded caja sales/amounts after typing errors, with reason and before/after traceability
  - listing loaded cajas by date, shift and branch so administration can confirm every expected `TM`/`TT` caja was entered
  - fixing economic/disponibilidad readings so treasury-paid expenses show by branch/rubro/period and central-cash book details show branch plus period totals
  - excluding panification billing from the general sales base when it is an internal/special branch flow, per user note
- Scope decision: update existing epics `EP-05`, `EP-08`, and `EP-11`; no runtime behavior changed in this intake.
- Backlog files updated:
  - `docs/epics/EP-05-flujo-de-disponibilidades.md`: added `US-5.10` and `US-5.11`.
  - `docs/epics/EP-08-ajustes-operativos-de-caja-y-sucursales.md`: added `US-8.14` and `US-8.15`.
  - `docs/epics/EP-11-rentabilidad-y-situacion-economica.md`: added `US-11.7`, `US-11.8`, and `US-11.9`.
  - `docs/epics/README.md`: reopened EP-05, EP-08 and EP-11 status lines.
- Validation: reviewed diff and story numbering with `rg`; application tests not run because only markdown backlog changed.
- Follow-up requirement: economic rubro totals need drilldown. If `Almacen` shows `$100.000`, administration must see the source lines that compose that amount, with origin, date, sucursal, provider/concept and amount.
- Scope decision: add this as `EP-11` backlog, because it explains economic rubro totals and traceability of the profitability view.
- Backlog files updated for follow-up:
  - `docs/epics/EP-11-rentabilidad-y-situacion-economica.md`: added `US-11.10`.
  - `docs/epics/README.md`: EP-11 pending range now includes `US-11.10`.
- Business clarification: bank/card accreditations are not discriminated by branch because incoming money is a common pool; branch discrimination is required for expenses/egresos.
- Scope decision: adjust EP-10 accreditation wording to avoid branch-level accreditation promises; keep branch-level egreso visibility in EP-05/EP-11.
- Backlog files updated for accreditation clarification:
  - `docs/epics/EP-10-situacion-financiera-y-alertas-consolidadas.md`: added pending `US-10.11` and removed branch-discrimination expectation from accreditation pending criteria.
  - `docs/epics/EP-11-rentabilidad-y-situacion-economica.md`: clarified that branch discrimination applies to expenses/gastos/debt, not common bank accreditations.
  - `docs/epics/README.md`: reopened EP-10 for `US-10.11`.
- New user feedback: add an option in Banco to enter an initial bank balance; also, branch-specific financial/economic views must include central-cash expenses imputed to that branch, not only consolidated totals.
- Scope decision: add `US-4.8` for bank initial balances and `US-10.12` for branch-specific financial reading of central-cash expenses; strengthen `US-11.7` for economic reading.
- Backlog files updated for latest feedback:
  - `docs/epics/EP-04-bancos-y-conciliacion.md`: added `US-4.8`.
  - `docs/epics/EP-10-situacion-financiera-y-alertas-consolidadas.md`: added `US-10.12`.
  - `docs/epics/EP-11-rentabilidad-y-situacion-economica.md`: strengthened `US-11.7` criteria.
  - `docs/epics/README.md`: reopened EP-04 and expanded EP-10 pending scope.

### Pull Resolution 2026-06-08

- `git pull --ff-only` was blocked because local staged `.claude/settings.local.json` would be overwritten by the remote tracked version.
- Preserved the local pre-pull file in `stash@{0}` with message `preserve local claude settings before pull`.
- Fast-forwarded `main` from `1c4ef5d` to `59ac8c0`; branch is now aligned with `origin/main`.
- Decision: keep the remote `.claude/settings.local.json` in the working tree to avoid reintroducing the same pull blocker.

### Objective

- 2026-06-04 cashops expense form cleanup:
  - User asked to remove `Detalle corto` from `Egreso por rubro`.
  - `Observacion` remains visible and optional.
  - Implementation direction: keep internal `MovimientoCaja.categoria` populated from selected rubro name for legacy/report compatibility.
- 2026-06-04 cashops dashboard cash-balance clarification:
  - User reported caja/local can show negative cash after an expense, but general view did not reflect it clearly.
  - Decision: do not change expense behavior; add explicit `Resultado real de cajas` metric for global/branch period scopes.
  - This metric sums `saldo_fisico` for closed boxes and `Caja.saldo_esperado` for open boxes in the selected company/branch scope, including negative balances.
  - It stays separate from `Total operativo`, which remains the rubro/gastos control base.
  - Closing a box with negative physical balance now creates an audited negative central-cash adjustment so caja fuerte central reflects the real deficit.
  - Migration `cashops.0018_backfill_negative_closures_to_central_cash` backfills existing closed boxes with negative physical balance into central cash as `AJUSTE_NEGATIVO`.
  - Validation: focused cashops service/view tests for open negative, closed negative, central-cash negative adjustment and dashboard display passed.
- 2026-06-05 cashops PANIFICACION exclusion rule:
  - User reported branch sales totals were including `PANIFICACION` invoicing sales.
  - Rule: any income channel with `CanalIngreso.excluir_de_totales=True` must not add to main branch/global `Ingresos`, matrix daily income totals or operative net result.
  - Excluded channel amounts remain visible as separate calculation using label `Ventas facturacion de {CANAL}`.
  - `PANIFICACION` already uses `excluir_de_totales=True`; services now enforce that flag generically instead of hardcoding one channel.
  - Management matrix export now respects selected company context and uses raw movement type if no display helper exists.
  - Validation: focused service/view/matrix/export tests for excluded income and existing neighboring totals passed.
- 2026-06-11 backlog follow-up for economic view detail:
  - User reported a missing functional capability: from the economic/rentability reading they cannot see the breakdown of why a rubro total such as `Almacen` is composed by that amount.
  - Backlog decision after rebase: use `EP-11` `US-11.10 Desglose trazable de totales por rubro` because remote `US-11.7` is already assigned to treasury-paid expenses in economic reading.
  - Scope of the story: for the active period and sucursal filters, the system must let administracion open a rubro and see the movements/debts included in that total, with date, origin, reference/concept, state and amount; the detail total must match the summary total exactly.
  - User-facing impact when implemented: users will be able to explain each rubro total from the system without rebuilding the composition manually in Excel or by checking isolated records.
- 2026-06-11 EP-11 implementation in progress:
  - Targeting `US-11.10` as the next missing story.
  - Planned slice: service detail by rubro, dashboard link, detail view/template, tests proving detail totals match the economic summary.
- 2026-06-11 EP-11 `US-11.10` implemented:
  - Added rubro composition detail for the treasury economic/rentability reading.
  - The dashboard now links each economic rubro to a detail page preserving period and sucursal filters.
  - Detail lists included cash expenses, treasury-paid expenses and period payables with date, origin, reference/concept, branch, status, amount and pending debt where applicable.
  - The detail total is derived from the same sources as the summary: `MovimientoCaja` gastos by rubro, imputable `MovimientoCajaCentral`/`MovimientoBancario` treasury expenses and non-annulled `CuentaPorPagar.importe_total` for the economic period.
  - Fixed economic period payable aggregation to respect selected company context when no specific sucursal is selected, preventing cross-company debt leakage while preserving legacy/global payables without sucursal.
  - Files touched: `treasury/services.py`, `treasury/views.py`, `treasury/urls.py`, `templates/treasury/dashboard.html`, `templates/treasury/economic_rubro_detail.html`, `treasury/tests.py`, `docs/epics/EP-11-rentabilidad-y-situacion-economica.md`, `docs/epics/README.md`, `context.md`.
  - Validation:
    - `py -3.14 manage.py test treasury.tests.TreasuryServiceTests treasury.tests.TreasuryViewTests -v 1` with `PYTHONPATH=.venv\Lib\site-packages` passed after rebase conflict resolution: 59 tests OK.
    - `py -3.14 -m compileall treasury` with `PYTHONPATH=.venv\Lib\site-packages` passed.
    - `py -3.14 manage.py test treasury.tests treasury.tests_ep05 -v 1` with `PYTHONPATH=.venv\Lib\site-packages` passed: 67 tests OK, 1 skipped.
    - `git diff --check` passed with only CRLF working-copy warnings.
    - `py -3.14 manage.py makemigrations --check` remains red on pre-existing treasury drift wanting `treasury\migrations\0018_alter_movimientocajacentral_tipo.py`; this slice did not add schema changes.

- 2026-06-04 disponibilidades fix requested from WhatsApp report:
  - User reports `Flujo de Disponibilidades` shows `$0,00` and "no toma lo que esta cargado".
  - Business expectation: disponibilidades should say current cash in caja central, current bank money, and consolidated availability.
  - User approved implementation of the fix and asked to expose/reset button with explicit destructive confirmation and a list of deleted data.
  - Implemented:
    - `build_disponibilidades_snapshot()` now includes global/legacy `CajaCentral` movements when company context is selected, so central cash loaded without branch is not hidden.
    - cash outflow now subtracts `MovimientoCajaCentral.Tipo.EGRESO_ADMIN`, matching `CajaCentral.saldo_actual`.
    - financial dashboard helper `_central_cash_balance_until()` now also subtracts `EGRESO_ADMIN` and respects selected companies while including global/legacy central cash.
    - Disponibilidades branch filter now restricts sucursales to selected companies.
    - `Detalle de movimientos` for central cash includes global/legacy central cash under selected company context.
    - Disponibilidades page now exposes `Reiniciar datos`.
    - reset confirmation lists all operational/financial data it deletes before final confirmation.
  - Files touched: `treasury/services.py`, `treasury/views.py`, `treasury/forms.py`, `templates/treasury/disponibilidades_report.html`, `templates/cashops/reset_confirm.html`, `treasury/tests_ep05.py`, `treasury/tests.py`, `cashops/tests.py`, `context.md`.
  - Validation:
    - `py -3.14 -m compileall treasury cashops` with `PYTHONPATH=.venv\Lib\site-packages` passed.
    - Focused tests passed: `treasury.tests_ep05.EP05DisponibilidadesTests`, two `TreasuryViewTests` disponibilidad tests, and reset confirmation test.
    - `py -3.14 manage.py test treasury.tests treasury.tests_ep05 -v 1` passed: 64 tests OK, 1 skipped.
    - Broad `cashops.tests.CashopsViewTests` run remains red on unrelated existing bug: `management_matrix_export` calls missing `MovimientoCaja.get_tipo_display()`.

- 2026-06-02 backlog update from user feedback:
  - In bank movement creation, the Rubro/Categoria selector does not show rubros already loaded in the rubros master.
  - User wants bank movement UI to say Rubro only, not Categoria.
  - A bank transfer reportedly registered under "Coca" but the selected bank-movement section/list showed no records.
  - Screenshot shows the bank movement form has a bottom-right submit button with no visible text.
  - Runtime implementation completed for `US-10.8`, `US-10.9`, and `US-10.10`.

- 2026-05-14 users slice:
  - Build an operational `Usuarios` management view from the Config menu.
  - Replace the old personal-oriented admin flow with user listing, creation, detail/access management, archive/delete actions, default password, mandatory first password change, and first-entry link.
  - Preserve current real access rules: role controls admin/config/treasury/users access; `usuario_fijo` + `sucursal_base` controls operational assignment scope.
- External GitHub profile task:
  - Review and improve `github.com/Froaky` so it presents Mateo Coca as hireable.
  - Work target is the separate profile repository `Froaky/Froaky`, not Gerayse runtime behavior.
  - Main decision: use a visual profile README with business-app positioning, featured projects, stack, and contact CTA.
- Stabilize treasury for a same-day internal-control demo.
- Reduce emphasis on bank integration features that are not part of the real operating model.
- Convert the user-provided `Fixes y detalles para Gerayse.docx` requirements into executable backlog epics and user stories.
- Create specialized local skills for the new epic areas and make them directly invocable.
- Convert new client feedback from 2026-04-28 about apertura de caja, turnos, ventas por canal, egresos and caja fuerte into executable backlog stories.
- Convert new client feedback from 2026-04-28 about company contexts, branch-to-company assignment, data isolation, header navigation, module menu, and dashboard cash/channel separation into executable backlog.
- Convert new client feedback from 2026-04-29 about treasury administrative expense form into executable backlog: cash origin must not require bank account; expense needs amount, rubro, concept, branch and paid period.

### Current Users Slice Notes

- 2026-05-14 follow-up permissions slice:
  - User requested clickable read/write permissions from the user detail screen and a Roles submenu under Usuarios.
  - Implemented direction: role-level default permissions plus user-level overrides; user detail badges POST real permission changes.
  - Permission modules for this slice: cashops, config, treasury, users.
  - Read/write toggles now affect backend authorization, not only UI labels.
  - Files touched for this follow-up:
    - `users/models.py`, `users/forms.py`, `users/views.py`, `users/urls.py`, `users/admin.py`, `users/tests.py`
    - `users/migrations/0007_rolepermission_userpermission.py`
    - `templates/users/user_detail.html`, `templates/users/user_list.html`, `templates/users/role_list.html`, `templates/users/role_detail.html`
    - `cashops/permissions.py`, `cashops/views.py`, `treasury/permissions.py`, `treasury/views.py`
    - `templates/cashops/layout.html`, `templates/treasury/layout.html`
    - `docs/epics/EP-09-usuarios-operativos-y-datos-minimos.md`, `docs/epics/README.md`, `context.md`
  - Behavior added:
    - Roles submenu under Usuarios with role create/edit and default permission toggles.
    - User detail permission badges are POST buttons for read/write overrides.
    - Role permissions seed legacy behavior: admin roles get all modules; non-admin roles get caja by default.
    - User overrides take precedence over role defaults.
    - Write permission automatically implies read.
    - Users cannot remove their own users-management permission by accident.
    - Cashops, treasury and users guards now enforce module read/write permissions.
  - Scope still pending:
    - `US-9.11` covers future permissions by sucursal, empresa or operational location.
  - Validation:
    - `.venv\Scripts\python.exe -m compileall users cashops treasury` passed.
    - `.venv\Scripts\python.exe manage.py test users -v 1` passed: 33 tests OK.
    - `.venv\Scripts\python.exe manage.py test treasury.tests.TreasuryPermissionTests treasury.tests.TreasuryViewTests -v 1` passed: 20 tests OK.
    - `.venv\Scripts\python.exe manage.py test cashops.tests.CashopsPermissionUnitTests cashops.tests.CashopsViewTests -v 1` passed: 40 tests OK.
    - `.venv\Scripts\python.exe manage.py check` passed.
    - `.venv\Scripts\python.exe manage.py makemigrations users --check` passed.
    - `.venv\Scripts\python.exe manage.py makemigrations --check` remains red on pre-existing cashops/treasury drift (`cashops.0013...`, `treasury.0018...`); users has no pending migration drift.
- Files touched for 2026-05-14 users slice:
  - `users/models.py`, `users/forms.py`, `users/views.py`, `users/urls.py`, `users/admin.py`, `users/middleware.py`, `users/tests.py`
  - `users/migrations/0006_user_must_change_password.py`
  - `templates/users/user_list.html`, `templates/users/user_detail.html`, `templates/users/password_change_required.html`, `templates/users/first_access.html`
  - `templates/cashops/layout.html`, `templates/treasury/layout.html`, `config/settings.py`
  - `cashops/migrations/0011_turno_empresa_caja_fecha_operativa.py` only changed to skip PostgreSQL-only `SET CONSTRAINTS` on SQLite test DBs
  - `docs/epics/EP-09-usuarios-operativos-y-datos-minimos.md`, `docs/epics/README.md`, `context.md`
- Behavior added:
  - Config now links to `Usuarios`; old `/personal/` URLs remain compatible.
  - New users created/reset through the operational form get `must_change_password=True`.
  - Login with default password is blocked by middleware until the password is changed.
  - First-entry token link lets the user set a password without using the default; the link expires after password change.
  - User detail manages actual current access levers: role, active/archive state, `usuario_fijo`, and `sucursal_base`.
  - Archive disables login; delete is blocked if protected related operations exist.
- Scope decision:
  - Current permission view is based on rules the backend already enforces. True granular ACL by module/location/read/write remains `US-9.9` because it needs backend enforcement across every protected view/form.
- Validation:
  - `.venv\Scripts\python.exe -m compileall users config` passed.
  - `.venv\Scripts\python.exe manage.py test users -v 2` passed: 25 tests OK.
  - `.venv\Scripts\python.exe manage.py test users -v 1` passed after including inactive legacy roles in user forms and delete coverage: 26 tests OK.
  - `.venv\Scripts\python.exe manage.py check` passed.
  - `.venv\Scripts\python.exe manage.py makemigrations users --check` passed.
  - `.venv\Scripts\python.exe manage.py makemigrations --check` is still red because existing cashops/treasury model drift wants new migrations `cashops.0013...` and `treasury.0018...`; no users migration drift remains.

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
- 2026-05-04 client dashboard finding:
  - `CajaCentral.saldo_actual` subtracts `MovimientoCajaCentral.Tipo.EGRESO_ADMIN`, but `treasury.services._central_cash_balance_until()` does not.
  - `build_financial_period_snapshot()` uses `_central_cash_balance_until()` for the treasury dashboard cards, so caja fuerte general can overstate cash by ignoring administrative cash expenses.
  - `build_disponibilidades_snapshot()` also omits `EGRESO_ADMIN` from monthly cash outflow, so the EP-05 disponibilidades report likely has the same bug.
  - `EgresoTesoreriaForm` correctly only requires `cuenta_bancaria` when `fuente == BANCO`, but the reusable form template renders the bank-account field even when the user selected caja fuerte central; this is a UX/confidence issue already aligned with pending `US-5.9`.
  - Local DB inspection could not verify production-like data because the configured SQLite database has no migrated `treasury_movimientocajacentral` table.
- Current fix target:
  - Enforce `cuenta_bancaria` if and only if `EgresoTesoreriaForm.fuente == BANCO`.
  - Hide/disable the bank account field for caja fuerte central in the rendered form and clear it server-side if submitted anyway.
  - Files changed for this fix: `treasury/forms.py`, `templates/treasury/partials/form_card.html`, `treasury/tests.py`.
  - Validation: `.venv\Scripts\python.exe -m compileall treasury` passed.
  - Blocked validation: `.venv\Scripts\python.exe manage.py test treasury.tests.TreasuryViewTests -v 2` fails before tests run because untracked `cashops/migrations/0012_turno_remove_legacy_fields.py` tries to remove missing index `cashops_tur_sucursa_bc2124_idx`; `.venv\Scripts\python.exe manage.py check` fails on unrelated `cashops.admin.TurnoAdmin` autocomplete for unregistered `Empresa`.

### Files Touched In This Session

- `docs/epics/README.md`
- `docs/epics/EP-04-bancos-y-conciliacion.md`
- `AGENTS.md`
- `context.md`
- `README.md`
- `docs/engineering-guidelines.md`
- `docs/manual-demo-camino-feliz.md`
- `docs/manual-demo-camino-feliz.pdf`
- `docs/portfolio-gerayse.md`
- `docs/generate_demo_manual_pdf.py`
- `.agents/skills/analista-funcional-backlog/SKILL.md`
- `.agents/skills/analista-funcional-backlog/references/gerayse-backlog-format.md`
- `.agents/skills/analista-funcional-backlog/agents/openai.yaml`
- `.agents/skills/caja-sucursales-operativa/SKILL.md`
- `.agents/skills/caja-sucursales-operativa/references/gerayse-caja-scope.md`
- `.agents/skills/caja-sucursales-operativa/agents/openai.yaml`
- `.agents/skills/usuarios-operativos-admin/SKILL.md`
- `.agents/skills/usuarios-operativos-admin/references/gerayse-usuarios-scope.md`
- `.agents/skills/usuarios-operativos-admin/agents/openai.yaml`
- `.agents/skills/tesoreria-financiera-consolidada/SKILL.md`
- `.agents/skills/tesoreria-financiera-consolidada/references/gerayse-tesoreria-scope.md`
- `.agents/skills/tesoreria-financiera-consolidada/agents/openai.yaml`
- `.agents/skills/control-gestion-rentabilidad/SKILL.md`
- `.agents/skills/control-gestion-rentabilidad/references/gerayse-control-scope.md`
- `.agents/skills/control-gestion-rentabilidad/agents/openai.yaml`
- `.agents/skills/testing-riguroso-extremo/SKILL.md`
- `.agents/skills/testing-riguroso-extremo/references/gerayse-testing-playbook.md`
- `.agents/skills/testing-riguroso-extremo/agents/openai.yaml`
- `cashops/services.py`
- `cashops/models.py`
- `cashops/forms.py`
- `cashops/views.py`
- `cashops/urls.py`
- `cashops/tests.py`
- `cashops/tests_commands.py`
- `cashops/test_migration_safety.py`
- `cashops/migrations/0008_sucursal_razon_social.py`
- `templates/cashops/dashboard.html`
- `templates/cashops/management_matrix.html`
- `templates/cashops/sucursal_list.html`
- `core/templates/core/home.html`
- `users/forms.py`
- `users/views.py`
- `users/tests.py`
- `templates/users/personal_list.html`
- `docs/epics/README.md`
- `docs/epics/EP-04-bancos-y-conciliacion.md`
- `docs/epics/EP-05-flujo-de-disponibilidades.md`
- `docs/epics/EP-06-control-de-gestion-y-alertas.md`
- `docs/epics/EP-07-impuestos-planes-y-autorizaciones.md`
- `docs/epics/EP-08-ajustes-operativos-de-caja-y-sucursales.md`
- `docs/epics/EP-05-flujo-de-disponibilidades.md`
- `docs/epics/EP-09-usuarios-operativos-y-datos-minimos.md`
- `docs/epics/EP-10-situacion-financiera-y-alertas-consolidadas.md`
- `docs/epics/EP-11-rentabilidad-y-situacion-economica.md`
- `docs/epics/README.md`
- `context.md`
- `docs/epics/EP-12-empresas-contexto-y-navegacion.md`
- `treasury/admin.py`
- `treasury/models.py`
- `treasury/forms.py`
- `treasury/services.py`
- `treasury/views.py`
- `treasury/urls.py`
- `treasury/tests.py`
- `treasury/tests_ep05.py`
- `templates/treasury/dashboard.html`
- `treasury/migrations/0012_acreditaciontarjeta_modo_registro_and_more.py`
- `treasury/migrations/0013_ep11_rubro_period_foundation.py`
- `treasury/migrations/0014_ep11_period_reference_required.py`
- `treasury/migrations/0016_ep07_special_commitments.py`

### Changes Applied

- 2026-06-02 EP-10 implementation slice completed:
  - `US-10.8`, `US-10.9`, and `US-10.10` are marked done.
  - Bank movement creation now uses active non-system `RubroOperativo` options and labels the field `Rubro`.
  - Bank movement domain validation requires rubro or legacy category for classified debit classes; new manual form persists `rubro_operativo`.
  - Existing legacy bank movements with `CategoriaCuentaPagar` remain visible and show the legacy category name.
  - Bank movement list search now supports concept, reference, and exact amount; sucursal filter includes both bank-account branch and debit expense branch.
  - Company active filtering includes account branch and debit expense branch when available.
  - After manual bank movement creation, the user is redirected to the movement detail for immediate confirmation.
  - Reusable treasury form submit button now defaults to visible `Guardar`; bank movement creation passes `Guardar movimiento`.
  - Files touched: `treasury/models.py`, `treasury/forms.py`, `treasury/services.py`, `treasury/views.py`, `treasury/tests.py`, `templates/treasury/partials/form_card.html`, `templates/treasury/partials/list_items.html`, `docs/epics/EP-10-situacion-financiera-y-alertas-consolidadas.md`, `docs/epics/README.md`, `context.md`.
  - Decision for "Coca" report: treat visibility through persisted bank account, account branch, debit expense branch, and active company context; no new company-specific selector was introduced.

- 2026-06-02 bank movement backlog update:
  - Reopened `EP-10` in `docs/epics/README.md` because user feedback exposed pending bank-movement UX/data issues.
  - Added pending `US-10.8`, `US-10.9`, and `US-10.10` to `docs/epics/EP-10-situacion-financiera-y-alertas-consolidadas.md`.
  - Functional scope captured:
    - bank movement creation must select active operational rubros, not only treasury categories
    - bank movement UI should say `Rubro` instead of `Rubro / categoria` or `Categoria`
    - a newly registered transfer must remain visible in the expected account/sucursal/company selection
    - the form submit button must show a visible action label
  - Later resolved in the EP-10 implementation slice: visibility uses bank account, account branch, debit expense branch and active company context.

- External GitHub profile:
  - created and pushed `README.md` to `https://github.com/Froaky/Froaky`
  - commit: `46ba285 Add profile README`
  - profile README now uses visual header, hiring/contact badges, stack badges, featured project table and GitHub stats cards
  - validation: `git ls-remote origin refs/heads/main` and GitHub contents API both confirmed the pushed README
  - note: public GitHub profile page may show stale cached content briefly after first push
  - follow-up commit `59abaa0 Replace profile stats with current focus`
  - removed the `GitHub Snapshot` stats section because it highlighted early-profile metrics; replaced it with `Current Focus` table showing hireable service areas
  - follow-up commit `6331ede Strengthen profile positioning`
  - profile copy now positions Mateo as a fullstack developer who turns spreadsheet/manual-control operational pain into maintainable internal software, with a `Why Teams Hire Me` section
  - follow-up commit `014cd16 Add violet profile design`
  - added `assets/profile-banner.svg` to use a custom purple/violet visual banner instead of the external capsule-render banner
  - README badge colors now follow the violet palette; GitHub API confirmed the SVG exists in `Froaky/Froaky`
  - follow-up commit `0b581ce Add profile polish sections`
  - added animated typing tagline, `assets/delivery-flow.svg` visual delivery flow, and `Engineering Signals` section to make the profile read as polished and technically reliable
  - follow-up commit `203428c Clean up profile header layout`
  - removed `Available for work` from both the SVG banner and README badges; adjusted banner line/text positioning to avoid overlap in GitHub profile render
  - follow-up commit `f45ebc5 Bust cached profile banner`
  - renamed banner asset to `assets/profile-banner-v2.svg` and updated README reference because GitHub was still serving the old cached SVG in the profile overview
  - follow-up commit `e39a4cd Add profile case study`
  - added `Signature Case Study` for Gerayse and `Best Fit` section so the profile sells concrete proof of operational/business-software skill, not only visual polish
  - follow-up commit `b365a09 Add portfolio domain to profile`
  - verified `https://froaky.com` redirects to `https://www.froaky.com/` with HTTP 200 and added it as profile badge, featured portfolio link, and contact link
- `docs/epics/EP-04-bancos-y-conciliacion.md`
  - made explicit that bank reconciliation remains manual-assisted and that automatic reconciliation is out of scope until a later explicit decision
- `docs/epics/README.md`
  - added a backlog-wide implementation principle forbidding automatic bank reconciliation or import-based matching until the user requests it
- `context.md`
  - recorded the product decision that bank reconciliation stays manual for now so future agents do not assume automation
- `docs/epics/EP-08-ajustes-operativos-de-caja-y-sucursales.md`
  - reopened EP-08 with pending stories from client feedback:
    - `US-8.8` turnos operativos recurrentes without daily pre-creation
    - `US-8.9` apertura de caja must persist or show actionable validation errors
    - `US-8.10` apertura/carga inicial must separate efectivo fisico from card/QR/app channels
    - `US-8.11` caja screen should support daily sales by channel and operational egresos by rubro
    - `US-8.12` opening must prevent sucursal/turno/terminal mismatch
- `docs/epics/EP-05-flujo-de-disponibilidades.md`
  - reopened EP-05 with pending stories for central treasury concerns from the same client feedback:
    - `US-5.7` visible initial balance flow for caja fuerte central
    - `US-5.8` egresos administrativos from treasury separated from branch operational caja
- `docs/epics/README.md`
  - updated EP-05 and EP-08 status from implemented to reopened because the client feedback introduced new pending stories
- Decision:
  - Caja operativa remains separated from caja fuerte central/tesoreria. Client feedback was split between `EP-08` and `EP-05` to avoid mixing branch cash movements with central treasury availability.
- `docs/epics/EP-08-ajustes-operativos-de-caja-y-sucursales.md`
  - added pending `US-8.13` from client screenshot: dashboard must separate `saldo efectivo en caja` from ventas by card/QR/debit/credit/wallet/app; non-cash sales must not be presented as physical cash available.
- `docs/epics/EP-12-empresas-contexto-y-navegacion.md`
  - created a new cross-cutting proposed epic for:
    - Empresa master data
    - Sucursal-to-Empresa assignment
    - active company selector near the user menu
    - data isolation by active company across cashops/treasury/reports
    - totals by company and branch
    - global header with `Gerayse` home link
    - module dropdown navigation to reduce loose buttons
- `docs/epics/README.md`
  - added `EP-12` to backlog status, suggested specialist mapping and implementation order
  - reopened `EP-08` because `US-8.13` is pending
- Decision:
  - Company context is a cross-domain feature, not only a branch-field change. Implementation must avoid partial filtering that isolates caja but still leaks treasury, bank or report data from another company.
- `docs/epics/EP-05-flujo-de-disponibilidades.md`
  - reopened EP-05 with pending `US-5.9` from 2026-04-29 client feedback:
    - administrative treasury expense origin dropdown must support at least central cash and bank account
    - when origin is cash/`Caja fuerte central`, bank account must be hidden/disabled and not required
    - when origin is bank, active bank account is required
    - expense requires importe, rubro, concepto, sucursal correspondiente and periodo pagado; observation/comment remains optional
- `docs/epics/README.md`
  - marked EP-05 reopened due to pending `US-5.9`
- `cashops/*` and `treasury/*` EP-06/EP-07 closure slice
  - EP-06 closed: added admin-only daily management matrix and CSV export from persisted cash movements, grouped by operational date, income channel and expense rubro
  - EP-06 uses existing `LimiteRubroOperativo`, `AlertaOperativa`, `CierreCaja` and `Justificacion` for rubro targets, deviation alerts, dashboard follow-up and difference tracking
  - EP-07 closed: added `CompromisoEspecial` with fiscal, plan, embargo, advance and extraordinary salary metadata around `CuentaPorPagar`
  - EP-07 payment guardrail: special commitments that require approval block payment until approved, rejected/cancelled commitments cannot be paid, and fully paid commitments are marked executed
  - docs now mark `EP-06` and `EP-07` implemented in the backlog index

- `treasury/*` EP-11 third slice
  - closed `US-11.5` and `US-11.6`
  - active payable categories now require an associated operational rubro through services/forms, including activation toggles
  - new or edited payables now reject categories without `rubro_operativo`; new payable forms only offer mapped categories
  - legacy payables whose category has no `rubro_operativo` remain visible and payable, but are reported as pending migration and excluded from consolidated economic totals by rubro
  - payable list and admin now expose/filter by operational rubro
  - dashboard copy now states that unmapped legacy debt is outside economic consolidation and objective comparison
  - `treasury/tests_ep05.py` legacy fixture was corrected to include mandatory `periodo_referencia` while still preserving the unmapped-category case
  - `docs/epics/EP-11-rentabilidad-y-situacion-economica.md` marked `US-11.5` and `US-11.6` done; `docs/epics/README.md` marks `EP-11` implemented

- `treasury/views.py`
  - normalized corrupted middle-dot separators to ` - ` in treasury list/detail helper labels to avoid mojibake in UI text
- `docs/engineering-guidelines.md`
  - added a repo-level engineering guide with explicit architecture rules, SOLID translated to Django conventions, migration hardening policy, testing expectations, and review checklist
- `README.md`
  - linked the new engineering guide from the main repo entry point
- `context.md`
  - recorded the new engineering baseline so future agents continue under the same rules

- `treasury/*` EP-11 second slice in progress
  - target cut: `US-11.1` objetivos economicos por rubro sobre ventas con vigencia mensual y alcance global/sucursal, mas `US-11.2` comparacion objetivo vs real vs desvio en dashboard
  - compatibility rule: no volver obligatorio `rubro_operativo` en deuda legacy en este slice; la deuda sin rubro seguira visible como pendiente de migracion y fuera de la comparacion contra objetivo
  - files expected in this slice: `treasury/models.py`, `treasury/services.py`, `treasury/views.py`, `treasury/admin.py`, `templates/treasury/dashboard.html`, `treasury/tests.py`, `treasury/migrations/0015_*`

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
- `docs/portfolio-gerayse.md`
  - added a portfolio-ready project description with product summary, stack, architecture, implemented scope, backlog status and agent-ready context
- `.agents/skills/analista-funcional-backlog/SKILL.md`
  - added a local functional-analyst skill to draft and refine epics and user stories
  - aligned the workflow with the repo's existing `docs/epics` structure
- `.agents/skills/analista-funcional-backlog/references/gerayse-backlog-format.md`
  - documented the observed epic format, numbering, and scope rules for this product
- `.agents/skills/analista-funcional-backlog/agents/openai.yaml`
  - added UI-facing metadata so the skill can appear as a named specialist agent
- `.agents/skills/analista-funcional-backlog/agents/openai.yaml`
  - updated the default prompt to use explicit `$analista-funcional-backlog` invocation
- `.agents/skills/caja-sucursales-operativa/*`
  - added a dedicated skill for caja, sucursales, traspasos, arrastres, and period totals
- `.agents/skills/usuarios-operativos-admin/*`
  - added a dedicated skill for simplifying operational users without breaking auth or role behavior
- `.agents/skills/tesoreria-financiera-consolidada/*`
  - added a dedicated skill for treasury, bank movement taxonomy, disponibilidades, and financial alerts
- `.agents/skills/control-gestion-rentabilidad/*`
  - added a dedicated skill for period-based control, rubros, profitability, and economic views
- `.agents/skills/testing-riguroso-extremo/*`
  - added a dedicated testing specialist skill with strict rules for regression, risk, evidence, permissions, migrations, commands, and financial assertions
  - added a repo-specific testing playbook with test matrices and useful Django test commands
- `.agents/skills/analista-funcional-backlog/*`
  - deepened the skill with backlog-cut rules, story readiness checks, epic hygiene rules, and repo-specific closure criteria
- `.agents/skills/caja-sucursales-operativa/*`
  - deepened the skill with source-of-truth rules, stop-ship cases, and decision matrices for transfers, carry-overs, and branch totals
- `.agents/skills/usuarios-operativos-admin/*`
  - deepened the skill with hide-vs-keep-vs-remove rules, explicit semantics requirements for `usuario fijo`, and compatibility guardrails
- `.agents/skills/tesoreria-financiera-consolidada/*`
  - deepened the skill with financial source matrices, formula discipline, and hard red flags against double counting and bad dashboards
- `.agents/skills/control-gestion-rentabilidad/*`
  - deepened the skill with KPI-definition discipline, period-imputation rules, and comparability guardrails for management reporting
- `.agents/skills/testing-riguroso-extremo/*`
  - deepened the skill with execution sequencing, anti-false-green rules, stop-ship criteria, and stronger app-specific test matrices
- `treasury/models.py`
  - in progress on EP-10 second slice: bank movement taxonomy now being hardened with explicit financial classes plus optional `categoria` and `proveedor`
- `treasury/services.py`
  - in progress on EP-10 second slice: grouped accreditations are being added with duplicate guardrails and period-aware financial reading
- `treasury/forms.py`, `treasury/views.py`, `treasury/tests.py`
  - in progress on EP-10 second slice: UI and evidence are being aligned to daily-vs-period accreditation input and typed bank movements
- `cashops/services.py`
  - blocked new transfers between branches at the domain layer with a clear validation message
- `cashops/models.py`
  - added `razon_social` to `Sucursal` and enforced it in model validation
- `cashops/forms.py`
  - made `razon_social` explicit and required in the branch admin form
- `cashops/views.py`
  - renamed the expense flow copy to `egreso por rubro`
  - made the branch-transfer screen unavailable to stop new usage from the UI path
  - added branch search by `codigo`, `nombre` and `razon_social`
  - used period summaries for global and branch dashboard scopes while keeping box scope daily
- `templates/cashops/dashboard.html`
  - renamed `Gasto rapido` to `Egreso por rubro`
  - removed the action card for branch transfers
  - exposed `saldo neto` and period inputs in the operational dashboard
- `templates/cashops/sucursal_list.html`
  - added a dedicated branch master list with search, edit, and controlled activate/deactivate actions
- `cashops/tests.py`
  - added coverage for branch create/update/toggle, business-name search, and dashboard period summaries with visible net balance
- `cashops/tests_commands.py`
  - aligned branch fixtures with the new required `razon_social`
- `cashops/test_migration_safety.py`
  - fixed the legacy migration test to create the historical user against the current `users` schema safely
- `cashops/migrations/0008_sucursal_razon_social.py`
  - added the schema migration for `Sucursal.razon_social`
- `core/templates/core/home.html`
  - removed branch-transfer wording from the public landing copy
  - aligned the cash module copy with `egreso por rubro`
- `cashops/tests.py`
  - updated coverage to enforce that branch transfers are disabled
  - added coverage for the new `egreso por rubro` copy and dashboard visibility
- `docs/epics/EP-08-ajustes-operativos-de-caja-y-sucursales.md`
  - grouped docx requirements about caja, sucursales, traspasos, and carry-over scenarios into a dedicated operational epic
  - marked `US-8.2` and `US-8.6` as done after the first implementation slice
  - marked `US-8.4` and `US-8.5` as done after adding the branch master and range-based totals
- `docs/epics/README.md`
  - marked `EP-08` as started in the backlog index
- `users/forms.py`
  - removed `legajo` from the operational create/update flow while preserving the model field for historical data
  - added `usuario_fijo` and `sucursal_base` to the operational form with active-branch queryset and required validation
- `users/models.py`
  - added `usuario_fijo` plus optional `sucursal_base` as the preferred operational assignment anchor
  - fixed-user validation now requires a base branch at model level
- `users/admin.py`
  - exposed `usuario_fijo` and `sucursal_base` in Django admin fieldsets, filters, and branch-aware search
- `users/migrations/0004_user_usuario_fijo_user_sucursal_base.py`
  - added schema support for fixed users and preferred base branch
- `users/views.py`
  - added search by name, last name, and role in `personal_list`
  - aligned user-management copy with an operational rather than HR-focused scope
- `templates/users/personal_list.html`
  - reduced the list view to name, last name, and role
  - hid `dni`, `legajo`, `telefono`, and username from the operational list view
- `users/tests.py`
  - added view tests for hidden `legajo`, minimal list rendering, search behavior, and editing users with legacy `legajo`
  - added admin coverage for `UserAdmin` role fieldsets, admin add flow with role assignment, changelist search by role code, and changelist filter by role
- `docs/epics/EP-09-usuarios-operativos-y-datos-minimos.md`
  - marked `US-9.1`, `US-9.3`, and `US-9.4` as done after the first implementation slice
- `users/tests.py`
  - added second-slice coverage for `usuario fijo`: model validation, form validation, admin add flow persistence, create/update persistence
- `docs/epics/README.md`
  - marked `EP-09` as started in the backlog index
- `docs/epics/EP-09-usuarios-operativos-y-datos-minimos.md`
  - separated user/personal simplification from cash and treasury scope to keep backlog slices smaller
- `docs/epics/EP-10-situacion-financiera-y-alertas-consolidadas.md`
  - grouped dashboard unification, bank movement taxonomy, pending accreditations, and due alerts into one financial-reading epic
- `treasury/forms.py`
  - added a dashboard filter form with `sucursal`, `fecha_desde`, and `fecha_hasta`
- `docs/epics/EP-11-rentabilidad-y-situacion-economica.md`
  - grouped profitability, period-based debt, and economic views into one later-stage management epic
- `docs/epics/README.md`
  - added the four new proposed epics to the backlog index and implementation order
  - marked `EP-10` as started after the first financial dashboard slice
- `treasury/services.py`
  - added `build_financial_period_snapshot()` to consolidate caja fisica, banco, disponibilidades, deuda, vencimientos, and pending accreditations by period and optional branch
- `treasury/views.py`
  - moved the treasury dashboard to a period-based financial reading with branch filtering and reference-date visibility
- `templates/treasury/dashboard.html`
  - replaced the old monthly treasury summary with a unified financial dashboard for caja, banco, consolidado, due buckets, and accreditation pending
- `treasury/tests.py`
  - added service and view coverage for the new financial dashboard slice, including branch scope, accruals, due buckets, and pending accreditations
- `docs/epics/EP-10-situacion-financiera-y-alertas-consolidadas.md`
  - marked `US-10.1`, `US-10.2`, `US-10.5`, `US-10.6`, and `US-10.7` as done after the first implementation slice
- `treasury/models.py`
  - added hard financial taxonomy to `MovimientoBancario` with `clase`, plus optional `categoria` and `proveedor` guarded by business rules
  - added daily-vs-period registration metadata to `AcreditacionTarjeta`
- `treasury/services.py`
  - inferred and enforced bank-movement classes for accreditations and payment-linked debits
  - added duplicate guardrails for card accreditations and period-aware accreditation reading in the financial snapshot
- `treasury/forms.py`
  - required typed bank movements in the manual bank form and enabled daily or grouped accreditation input in one form
- `treasury/views.py`
  - exposed financial taxonomy in bank movement list/detail and enabled grouped accreditation registration through the existing treasury UI
- `treasury/migrations/0012_acreditaciontarjeta_modo_registro_and_more.py`
  - added schema support for bank movement taxonomy and grouped accreditations, plus backfill for legacy movement classes
- `treasury/tests.py`
  - added hard coverage for typed bank movements, grouped accreditations, duplicate prevention, payment-link classification, and period-aware accreditation impact on the dashboard
- `docs/epics/EP-10-situacion-financiera-y-alertas-consolidadas.md`
  - marked `US-10.3` and `US-10.4` as done after the second implementation slice
- `docs/epics/README.md`
  - marked `EP-10` as implemented after closing the remaining stories
- `treasury/models.py`
  - mapped `CategoriaCuentaPagar` to optional `RubroOperativo`
  - added `CuentaPorPagar.periodo_referencia` as the economic imputation period
- `treasury/services.py`
  - defaulted `periodo_referencia` from `fecha_emision`
  - added `build_economic_period_snapshot()` to relate ventas, gasto caja y deuda del periodo by rubro and optional sucursal
- `treasury/forms.py`
  - exposed rubro mapping in category maintenance and made `periodo_referencia` visible but backward-compatible in payable forms
- `treasury/views.py`
  - added the economic snapshot to the treasury dashboard
  - exposed rubro operativo and periodo economico in payable detail and payable list helpers
- `templates/treasury/dashboard.html`
  - added the `Situacion economica y rentabilidad` section with ventas base, deuda del periodo, resultado economico and breakdown by rubro
- `treasury/migrations/0013_ep11_rubro_period_foundation.py`
  - added schema support for rubro mapping and period reference with backfill from `fecha_emision`
- `treasury/migrations/0014_ep11_period_reference_required.py`
  - locked `periodo_referencia` as mandatory after backfilling legacy rows
- `treasury/tests.py`
  - added coverage for rubro mapping, default economic period, economic snapshot aggregation, and dashboard visibility for EP-11
- `docs/epics/EP-11-rentabilidad-y-situacion-economica.md`
  - marked `US-11.3` and `US-11.4` as done after the first implementation slice
- `docs/epics/README.md`
  - marked `EP-11` as started
- `docs/epics/README.md`
  - completed backlog status visibility for `EP-03` through `EP-11`
  - mapped each epic to the most specific local specialist skill so future work can start with the right agent
- `docs/epics/EP-04-bancos-y-conciliacion.md`
  - normalized the epic to the repo backlog format with `No incluye todavia`, executable stories and `Orden tecnico sugerido`
- `docs/epics/EP-05-flujo-de-disponibilidades.md`
  - normalized implemented stories to `[x]` format and added the missing epic sections required by the backlog skill
- `docs/epics/EP-06-control-de-gestion-y-alertas.md`
  - closed a backlog gap by adding `US-6.7 Seguimiento de diferencias y faltantes`
  - clarified the scope boundary against `EP-10` and `EP-11`
  - completed missing epic sections and made every story executable
- `docs/epics/EP-07-impuestos-planes-y-autorizaciones.md`
  - closed backlog gaps for `embargos` and `sueldos extraordinarios` with new `US-7.7` and `US-7.8`
  - completed missing epic sections and tightened approval/control criteria
- `docs/epics/EP-08-ajustes-operativos-de-caja-y-sucursales.md`
  - made explicit that carry-overs/unifications do not cross branches and require an auditable reason
  - tightened `US-8.3` so the simplification target is implementable without guessing
  - added `US-8.16` for follow-up/continuation of cajas by sucursal, date, state and last activity
  - added `US-8.17` for drilldown of visible sales and operational totals from caja views/lists
  - added `US-8.18` for an auditable caja timeline showing opening, loads, corrections, closing and interruption point
- `docs/epics/README.md`
  - expanded `EP-08` reopened scope from `US-8.14`/`US-8.15` to `US-8.14` through `US-8.18`
- `docs/epics/EP-09-usuarios-operativos-y-datos-minimos.md`
  - defined `usuario fijo` as preferred operational assignment rather than a hard lock, leaving the future model/UI cut implementable
- `docs/epics/EP-11-rentabilidad-y-situacion-economica.md`
  - tightened pending stories so objective history, period-based comparison and legacy-category compatibility are explicit
  - reopened the epic with `US-11.7` so the rentability/economic view must expose the breakdown of each rubro total and not only the consolidated amount
- `docs/epics/README.md`
  - marked `EP-11` as reopened because the consolidated view exists but still lacks the rubro-composition detail requested by the user

### Validation Results

- Passed:
  - `python manage.py test treasury.tests.TreasuryServiceTests treasury.tests.TreasuryViewTests treasury.tests_ep05 -v 2`
  - `python manage.py test treasury.tests.TreasuryAdminProtectionTests treasury.tests.TreasuryPermissionTests -v 2`
  - `python manage.py test treasury.tests.TreasuryServiceTests treasury.tests.TreasuryViewTests treasury.tests_ep05 -v 1`
  - `python C:\Users\theco\.codex\skills\.system\skill-creator\scripts\quick_validate.py .agents\skills\analista-funcional-backlog`
  - `python C:\Users\theco\.codex\skills\.system\skill-creator\scripts\quick_validate.py .agents\skills\caja-sucursales-operativa`
  - `python C:\Users\theco\.codex\skills\.system\skill-creator\scripts\quick_validate.py .agents\skills\usuarios-operativos-admin`
  - `python C:\Users\theco\.codex\skills\.system\skill-creator\scripts\quick_validate.py .agents\skills\tesoreria-financiera-consolidada`
  - `python C:\Users\theco\.codex\skills\.system\skill-creator\scripts\quick_validate.py .agents\skills\control-gestion-rentabilidad`
  - `python C:\Users\theco\.codex\skills\.system\skill-creator\scripts\quick_validate.py .agents\skills\testing-riguroso-extremo`
  - `python manage.py test cashops.tests.CashopsServiceTests cashops.tests.CashopsViewTests -v 2`
  - `python manage.py test cashops.tests.CashopsServiceTests cashops.tests.CashopsViewTests cashops.tests_commands cashops.test_migration_safety -v 2`
  - `python manage.py test users.tests -v 2`
  - `.venv\Scripts\python.exe manage.py test users.tests -v 2`
  - `.venv\Scripts\python.exe manage.py test users.tests -v 2` after adding `usuario_fijo` + `sucursal_base`
  - `.venv\Scripts\python.exe manage.py test cashops.tests.CashopsPermissionUnitTests cashops.tests.CashopsServiceTests cashops.tests.CashopsViewTests -v 1`
  - `python manage.py test treasury.tests.TreasuryServiceTests treasury.tests.TreasuryViewTests treasury.tests_ep05 -v 2`
  - `python manage.py test treasury.tests.TreasuryAdminProtectionTests treasury.tests.TreasuryPermissionTests -v 2`
  - revalidated the six local specialist skills after deepening workflows, stop-ship rules, and repo references
  - `python manage.py test treasury.tests.TreasuryServiceTests treasury.tests.TreasuryViewTests -v 2`
  - `python manage.py test treasury.tests.TreasuryServiceTests treasury.tests.TreasuryViewTests treasury.tests.TreasuryAdminProtectionTests treasury.tests.TreasuryPermissionTests treasury.tests_ep05 -v 2`
  - `python -m compileall treasury`
  - `python manage.py test treasury.tests -v 1` after EP-11 first slice
  - `python -m compileall treasury` after EP-11 first slice
  - `.venv\Scripts\python.exe manage.py test treasury.tests -v 1` after adding `ObjetivoRubroEconomico`
  - `.venv\Scripts\python.exe manage.py makemigrations --check`
  - `.venv\Scripts\python.exe -m compileall cashops treasury users`
  - `.venv\Scripts\python.exe manage.py test treasury.tests -v 1` after EP-11 third slice
  - `.venv\Scripts\python.exe manage.py test treasury.tests treasury.tests_ep05 -v 1` after EP-11 third slice
  - `.venv\Scripts\python.exe manage.py makemigrations --check` after EP-11 third slice
  - `.venv\Scripts\python.exe -m compileall treasury` after EP-11 third slice
  - `.venv\Scripts\python.exe manage.py test cashops.tests.CashopsServiceTests cashops.tests.CashopsViewTests -v 1` after EP-06 matrix/export slice
  - `.venv\Scripts\python.exe manage.py test treasury.tests treasury.tests_ep05 -v 1` after EP-07 commitments slice
  - `.venv\Scripts\python.exe manage.py test cashops.tests.CashopsServiceTests cashops.tests.CashopsViewTests treasury.tests treasury.tests_ep05 -v 1` after EP-06/EP-07 closure slice: 111 tests OK, 1 skipped
  - `.venv\Scripts\python.exe manage.py test treasury.tests.TreasuryViewTests -v 1` after removing duplicate treasury view helpers
  - `.venv\Scripts\python.exe manage.py makemigrations --check` after EP-06/EP-07 closure slice
  - `.venv\Scripts\python.exe -m compileall cashops treasury` after EP-06/EP-07 closure slice
  - `.venv\Scripts\python.exe -m compileall treasury` after treasury view cleanup
  - `git diff --check` after EP-06/EP-07 closure slice and view cleanup
  - `py -3.14 manage.py test treasury.tests.TreasuryViewTests -v 1` with `PYTHONPATH=.venv\Lib\site-packages` after EP-10 movement-rubro slice: 23 tests OK
  - `py -3.14 manage.py test treasury.tests treasury.tests_ep05 -v 1` with `PYTHONPATH=.venv\Lib\site-packages` after EP-10 movement-rubro slice: 61 tests OK, 1 skipped
  - `py -3.14 -m compileall treasury` after EP-10 movement-rubro slice passed
- Not run:
  - application tests for the 2026-04-29 treasury-expense backlog update, because only epic markdown and context were changed
  - application tests for the 2026-04-28 backlog update, because only epic markdown and context were changed
  - application tests for the EP-12/company-context backlog update, because only markdown docs and context were changed
  - first `.venv\Scripts\python.exe manage.py test treasury.tests_ep05 -v 1` run failed because a legacy fixture missed mandatory `periodo_referencia`; fixture was fixed and the combined treasury suite passed
  - full non-treasury regression was not run after EP-11 third slice because the behavior changed only in treasury category/payable/economic reporting surfaces
  - application tests after the `treasury/views.py` text-normalization pass, because this slice only adjusted UI labels/separators and did not change business behavior
  - application tests for `docs/engineering-guidelines.md` and `README.md`, because this task only added repository documentation and no runtime behavior changed
  - application tests for the new epic docs, because this task only added backlog markdown
  - application tests for the 2026-06-12 EP-08 backlog update, because this task only changed epic markdown and project memory
  - application tests for the 2026-06-11 `US-11.7` backlog update, because this task only changed epic markdown and project memory
  - application tests for the new skill files, because they only add skill metadata and instructions
  - application tests for the backlog-normalization pass on `docs/epics`, because no runtime code changed in this slice
  - application tests for `docs/portfolio-gerayse.md`, because this task only added descriptive documentation
  - `.venv\Scripts\python.exe ...` commands for EP-10 movement-rubro slice because `.venv\Scripts\python.exe` points to missing `C:\Python312\python.exe`; workaround was `py -3.14` plus venv `site-packages`
  - `py -3.14 manage.py makemigrations --check` remains red on pre-existing treasury drift wanting `treasury\migrations\0018_alter_movimientocajacentral_tipo.py`; EP-10 movement-rubro slice did not add schema changes
- Treasury status after this session:
  - supplier create/update flow works again
  - payable create/update flow works again
  - payment-in-cash flow exists in UI and creates central cash movement
  - ECHEQ registration works again
  - disponibilidad snapshot and monthly close tests are green
  - a PDF demo guide can be regenerated from the markdown source

### Known Remaining Risks

- Monthly closing by `sucursal` still deserves a separate design pass if branch-specific treasury closings become mandatory.
- The source doc mixes immediate UI fixes with larger business capabilities; the backlog split into EP-08..EP-11 is an analytic decision, not an explicit grouping from the user.
- `P2` was interpreted as the current personal/users screen because the source document does not define that label.
- EP-08 review snapshot:
  - implemented: quick path principal enfocado en ingresos, egreso como acceso secundario, detalle minimo de movimientos, venta por rubro sin `producto`, arrastre auditado entre cajas de la misma sucursal incluso entre turnos o dias
  - reopened 2026-04-28: pending `US-8.13` to avoid mixing physical cash balance with card/QR/debit/credit/wallet/app sales in the caja dashboard
  - reopened scope expanded 2026-06-12 and then partially closed: `US-8.16`, `US-8.17` and `US-8.18` now implemented; pending `US-8.14` for audited corrections and `US-8.15` for explicit missing-caja control by turno/sucursal
  - guardrails: sigue bloqueado el cruce entre sucursales y el dominio rechaza arrastres fuera de la misma sucursal
  - key refs: `cashops/models.py`, `cashops/forms.py`, `cashops/services.py`, `cashops/views.py`, `templates/cashops/dashboard.html`, `templates/cashops/sucursal_list.html`
- EP-12 initial scope:
  - create `Empresa`, assign each `Sucursal` to one active company, add active-company selector, filter cashops/treasury/reports by active company, and clean global navigation with a Gerayse home link plus module dropdown
  - compatibility need: backfill or assisted migration from current `Sucursal.razon_social` text to `Empresa`
  - key risk: partial company filtering can leak or mix financial/cash data across companies
- EP-09 review snapshot after first slice:
  - implemented: `legajo` fuera del flujo operativo, vista minima de personal, busqueda operativa y `usuario fijo` con modelo, validacion, admin, persistencia y efecto en apertura de caja
  - key refs: `users/forms.py`, `users/views.py`, `templates/users/personal_list.html`, `users/tests.py`
  - admin evidence now also covers that `role` sigue visible y usable en Django admin para alta, busqueda y filtro
- EP-06 review snapshot:
  - done: matriz diaria de gestion, export CSV trazable, objetivos/alertas por rubro con vigencia, dashboard de seguimiento y diferencias/faltantes auditables
  - residual risk: el export es CSV operativo; no replica formato visual de Excel ni genera XLSX
  - key refs: `cashops/services.py`, `cashops/views.py`, `cashops/urls.py`, `templates/cashops/management_matrix.html`, `cashops/tests.py`
- EP-07 review snapshot:
  - done: compromisos especiales para impuestos, planes, requerimientos, adelantos, embargos y sueldos extraordinarios; autorizacion con efecto real sobre pagos; registro de capital/intereses y trazabilidad de sustento
  - residual risk: no hay integracion externa fiscal/contable ni liquidacion formal de sueldos; el alcance queda como control operativo interno sobre `CuentaPorPagar`
  - key refs: `treasury/models.py`, `treasury/forms.py`, `treasury/services.py`, `treasury/views.py`, `treasury/admin.py`, `treasury/migrations/0016_ep07_special_commitments.py`, `treasury/tests.py`
- EP-10 review snapshot after first slice:
  - done: dashboard financiero por periodo y sucursal, visibilidad de caja fuerte general, banco y total consolidado, buckets de vencimientos, lectura de acreditaciones pendientes, taxonomia dura de movimientos bancarios, carga diaria o agrupada de acreditaciones con guardrails de duplicado, rubros operativos en alta de movimientos bancarios, visibilidad post-alta y boton principal con etiqueta
  - closed again 2026-06-02 after completing `US-10.8`, `US-10.9`, and `US-10.10`
  - residual risk: la taxonomia nueva se apoya en `CategoriaCuentaPagar` como rubro operativo-financiero compartido; si negocio exige un maestro de rubros bancarios separado, eso seria una nueva capa de modelado y no un bug del slice actual
  - key refs: `treasury/models.py`, `treasury/forms.py`, `treasury/services.py`, `treasury/views.py`, `treasury/migrations/0012_acreditaciontarjeta_modo_registro_and_more.py`, `treasury/tests.py`
- EP-11 review snapshot after third slice:
  - done: rentabilidad visible por sucursal y periodo en dashboard de tesoreria, vista economica consolidada por rango, resultado economico, margen, deuda del periodo, detalle por rubro y comparacion objetivo vs real
  - foundation done: `CuentaPorPagar.periodo_referencia` y mapping `CategoriaCuentaPagar -> RubroOperativo`
  - second slice done: `ObjetivoRubroEconomico` agrega objetivos vigentes por rubro con alcance global o por sucursal, y el dashboard muestra objetivo parametrizado, real comparado y desvio
  - third slice done: deuda nueva/editada exige categoria con rubro operativo, categorias activas exigen rubro, filtros/listados/admin muestran rubro, y deuda legacy sin rubro queda visible como pendiente de migracion pero fuera de la lectura economica consolidada
  - residual risk: no se hizo constraint DB `NOT NULL` sobre `CategoriaCuentaPagar.rubro_operativo`; fue intencional para no cortar deuda legacy ni pagos historicos
  - key refs: `treasury/models.py`, `treasury/forms.py`, `treasury/services.py`, `treasury/views.py`, `treasury/admin.py`, `templates/treasury/dashboard.html`, `treasury/tests.py`, `treasury/tests_ep05.py`
- EP-08 current slice 2026-06-23:
  - user requested edit/delete for already closed cajas because operators sometimes load movements incorrectly
  - decision: implement correction/anulacion of closed-caja movements with audit trail and recalculated cierre/control totals; do not hard-delete caja or movement rows
  - permission to add: user/role assignable write permission for closed-caja corrections, separate from regular caja operation
  - implemented: `cashops_closed_fix` permission, edit/anular buttons on closed box detail, `MovimientoCaja.estado`, `MovimientoCajaCorreccion`, recalculated `CierreCaja` expected/difference and operational control snapshots
  - guardrail: apertura, transferencias and cierre auto-ajustes are not editable through this correction button; they need a specific circuit if business asks
  - key refs: `cashops/models.py`, `cashops/services.py`, `cashops/forms.py`, `cashops/views.py`, `cashops/urls.py`, `templates/cashops/partials/movement_list.html`, `users/models.py`, `users/views.py`
  - evidence: `py -3.14 -m compileall users cashops`; `py -3.14 manage.py test cashops.tests.CashopsServiceTests cashops.tests.CashopsViewTests users.tests -v 1` => 120 OK; `py -3.14 manage.py makemigrations --check --dry-run` => no changes detected
- Company access rule 2026-06-23:
  - user corrected the access rule: empty `empresas_permitidas` means no company access, never all companies.
  - behavior: users without marked companies get no available/active companies and no company-scoped caja/treasury data; users with marked companies default to all explicitly allowed companies when no session filter exists.
  - guardrail: `empresa_principal` must be included in `empresas_permitidas`; stale session company ids are ignored unless explicitly allowed.
  - compatibility decision: legacy/global rows without branch/company can remain visible only when the user has at least one explicitly allowed company; users with zero companies see none.
  - files touched: `core/context_processors.py`, `cashops/views.py`, `cashops/forms.py`, `cashops/services.py`, `treasury/views.py`, `treasury/forms.py`, `treasury/services.py`, `users/forms.py`, `templates/users/user_detail.html`, plus focused tests in `core/tests.py`, `users/tests.py`, `cashops/tests.py`, `treasury/tests.py`.
  - evidence: `py -3.14 -m compileall core users cashops treasury`; `py -3.14 manage.py makemigrations --check --dry-run` => no changes detected; `py -3.14 manage.py test core.tests users.tests cashops.tests.EP12EmpresasTests cashops.tests.CashopsViewTests treasury.tests.TreasuryViewTests -v 1` => 130 OK.
- Closed-box movement submit fix 2026-06-23:
  - user reported the delete confirmation stayed on the same screen and did not advance.
  - cause: closed-box edit/delete views returned a normal redirect to an HTMX form submit, so the browser could keep the interaction inside the form target instead of navigating back to box detail.
  - fix: successful edit/delete now returns `HX-Redirect` for HTMX requests and normal `redirect()` for non-HTMX requests; invalid submissions still render the form partial.
  - follow-up fix: edit/delete links now carry a safe `next` URL so confirmation returns to the exact previous screen/filter, and success messages identify the caja and movement, e.g. `Caja #X: movimiento #Y eliminado correctamente.`
  - final hardening: closed-box edit/delete confirmation forms disable `hx-post` and use plain POST with hidden `next`, so production does not depend on HTMX/CDN behavior for critical deletion confirmation.
  - files touched: `cashops/views.py`, `cashops/tests.py`, `templates/cashops/partials/form_card.html`.
  - evidence: `py -3.14 manage.py test cashops.tests.CashopsViewTests.test_closed_box_movement_delete_confirmation_uses_plain_post cashops.tests.CashopsViewTests.test_closed_box_movement_delete_view_requires_specific_permission cashops.tests.CashopsViewTests.test_closed_box_movement_delete_view_annuls_movement cashops.tests.CashopsViewTests.test_closed_box_movement_delete_view_redirects_htmx_to_box_detail cashops.tests.CashopsViewTests.test_closed_box_movement_edit_view_updates_movement -v 2` => 5 OK; `py -3.14 -m compileall cashops` => OK; `py -3.14 manage.py makemigrations --check --dry-run` => no changes detected.
- Whole-box edit/delete 2026-06-24:
  - user clarified they meant edit/delete complete cajas from `Seguimiento`, not only individual movements inside a caja.
  - implemented `Caja.Estado.ANULADA` and `CajaCorreccion` audit log; deleting a caja is logical annulment, not physical delete.
  - behavior: tracking cards with `cashops_closed_fix` permission show `Editar` and `Eliminar`; editing can correct responsible user, branch, shift, operational date and initial cash with mandatory reason; deleting annuls the caja and all registered movements, resolves box alerts, hides it from normal tracking and excludes it from period totals.
  - guardrail: all whole-box edits/deletes require the same specific closed-box correction permission and keep a reason/user/timestamp audit trail.
  - files touched: `cashops/models.py`, `cashops/forms.py`, `cashops/services.py`, `cashops/views.py`, `cashops/urls.py`, `templates/cashops/box_tracking.html`, `templates/cashops/partials/form_card.html`, `cashops/tests.py`, migration `cashops/0020_alter_caja_estado_cajacorreccion.py`.
  - evidence: `py -3.14 manage.py test cashops.tests.CashopsServiceTests cashops.tests.CashopsViewTests -v 1` => 88 OK; `py -3.14 -m compileall cashops` => OK; `py -3.14 manage.py makemigrations --check --dry-run` => no changes detected.
- Pull 2026-06-30:
  - `git pull --ff-only` updated `main` from `1fb3fe9` to `786ff8c`; remote added agent docs/protocol files and changes in `cashops`, `treasury`, templates, tests and `context.md`.
  - No app tests run; this task only synchronized the local branch.
- Dashboard expense clarification 2026-06-30:
  - User reported that `Egresos caja fuerte` and `Gasto tesoreria` look inconsistent, and that the economic view still misses expenses.
  - Decision: do not make economic totals subtract all central-cash outflows blindly because that would mix payments/deposits/adjustments with period expenses and can double-count payable debt.
  - Behavior changed: dashboard now labels total central-cash outflows as `Salidas caja fuerte`, adds `Gastos caja fuerte` for `EGRESO_ADMIN`, keeps `Gasto tesoreria` as economic expenses already imputed by rubro/sucursal/period, and shows `Gasto sin imputar` when treasury expenses are missing those fields.
  - User-facing impact: administration can see why `Salidas caja fuerte` may be higher than `Gasto tesoreria`, and can identify expenses that must be completed before entering economic/rubro totals.
  - Files touched: `treasury/services.py`, `templates/treasury/dashboard.html`, `treasury/tests.py`, `context.md`.
  - Evidence: focused 3-test regression OK; `python manage.py test treasury.tests.TreasuryServiceTests treasury.tests.TreasuryViewTests -v 1` with `PYTHONPATH=.venv\Lib\site-packages` => 79 OK; `python -m compileall treasury` with `PYTHONPATH=.venv\Lib\site-packages` => OK.
  - Environment note: `.venv\Scripts\python.exe` points to missing `C:\Python312\python.exe`; used global Python 3.14 with venv `site-packages`.

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
- Use `analista-funcional-backlog` for new backlog work under `docs/epics` so future epics keep the same structure and numbering rules.
- Use the specialist mapping now documented in `docs/epics/README.md`:
  - `tesoreria-financiera-consolidada` for `EP-03`, `EP-04`, `EP-05` and `EP-10`
  - `control-gestion-rentabilidad` for `EP-06`, `EP-07` and `EP-11`
  - `caja-sucursales-operativa` for `EP-08`
  - `usuarios-operativos-admin` for `EP-09`
- Use `testing-riguroso-extremo` when a slice changes business rules, money, permissions, migrations, commands, or when a story should not be marked done without hard test evidence.
- Next EP-06 candidates in order:
  - `EP-06` quedo funcionalmente cerrado; posible mejora futura: export XLSX con formato similar a la matriz original si negocio lo pide
- Next EP-08 candidates in order:
  - implementar `US-8.14` para correccion auditada de ventas, importes y egresos ya cargados
  - implementar `US-8.15` para marcar explicitamente cajas faltantes esperadas por turno/sucursal, no solo las ya cargadas
- Next EP-07 candidates in order:
  - `EP-07` quedo funcionalmente cerrado; posibles mejoras futuras: integracion fiscal externa, documentos adjuntos formales y circuito especifico de liquidacion de sueldos
- Next EP-10 candidates in order:
  - EP-10 is closed again after `US-10.8`, `US-10.9`, and `US-10.10`.
  - If production confirms "Coca" means a different selector than account branch, expense branch or active company context, add a new follow-up story before changing filters again.
- Next EP-11 candidates in order:
  - `EP-11` quedo funcionalmente cerrada otra vez tras `US-11.7`.
  - reevaluar si hace falta un comando de migracion asistida para categorias legacy sin rubro si negocio quiere limpiar historicos

### Treasury Economic Unmapped Bank Debit Fix 2026-07-02

- User reported that `Situacion economica` showed a large `Gasto sin imputar` while `Libro de Efectivo Central` had no pending imputation rows; this inflated the apparent margin because generic bank debits were being counted as pending treasury expenses.
- Behavior changed: bank debits created through `Registrar egreso` now use explicit origin `EGRESO_TESORERIA`; economic snapshots only treat explicit treasury bank expenses, plus legacy complete manual bank expenses with rubro/sucursal/periodo, as treasury expenses.
- Incomplete generic manual bank debits are no longer counted as `Gasto sin imputar`; pending bank treasury expenses are limited to explicit `EGRESO_TESORERIA` records missing imputation data.
- `register_egreso_tesoreria()` now validates rubro, sucursal and periodo at service level before creating either cash-central or bank expenses.
- Compatibility: complete legacy bank expenses created before `EGRESO_TESORERIA` remain included in `Gasto tesoreria`; incomplete manual bank movements remain bank ledger movements but no longer pollute the economic pending-imputation alert.
- Files touched: `treasury/models.py`, `treasury/services.py`, `treasury/tests.py`, `treasury/migrations/0021_bank_treasury_expense_origin.py`, `context.md`.
- Evidence: focused economic/service/view tests OK; `TreasuryServiceTests` 48 OK; `TreasuryViewTests` 35 OK; `treasury.tests_ep05` 8 OK; `makemigrations --check --dry-run` no changes detected; `compileall treasury` OK. Commands emitted an environment warning from `sitecustomize` about missing PIL `_imaging`, but Django checks/tests completed successfully.

### EP-13 Deuda sobre caja cerrada 2026-07-22

- Pedido (video administradora): los cajeros deben poder cargar el "gasto como deuda" aunque la caja este CERRADA (backfill de julio y dia a dia), con fecha de factura, solo de su sucursal. Ella (admin) ya ve todas las sucursales y filtra por proveedor en Cuentas por Pagar (sin cambios).
- Opcion: OPTIMA. Regla en servicio, permiso reutilizable, no toca efectivo, tests proporcionales.
- Cambio:
  - Nuevo permiso por accion `CASHOPS_DEBT_CLOSED` ("Cargar deuda en caja cerrada"), asignable/removible por rol o por usuario (igual patron que `CASHOPS_VALIDATE`). Agregado a `PermissionModule` y a `PERMISSION_MODULE_META` (UI de usuarios).
  - `register_box_expense_debt` admite caja CERRADA solo si `permitir_caja_cerrada=True` (la vista lo deriva de `can_load_debt_on_closed_box`); nunca ANULADA. Nuevo helper `_lock_box_for_debt` mantiene `select_for_update` + `ensure_can_operate_box` (caja propia).
  - Nuevo campo de form `fecha_factura` (DateInput ISO); define `fecha_emision` y `periodo_referencia`; si no viene, usa `caja.fecha_operativa` (compat con llamadas viejas).
  - Boton "Cargar deuda" en el dashboard, rama caja cerrada, gateado por `request.user.can_load_debt_on_closed_box`.
- Salvaguardas: sin el permiso, la caja cerrada sigue bloqueada ("La caja esta cerrada."); NUNCA se agregan movimientos de efectivo a caja cerrada (solo la deuda, que no crea `MovimientoCaja`); no reabre la caja, no toca su efectivo ni su `validacion_estado`; scope a la caja propia del cajero; auditado (`creado_por`).
- Datos: `users/migrations/0013_alter_rolepermission_module_and_more.py` es solo `AlterField` de `choices` (no toca datos). Deudas ya cargadas quedan intactas. `migrate` local aplicado sin cambios de schema.
- Archivos: `users/models.py`, `users/views.py`, `cashops/permissions.py`, `cashops/services.py`, `cashops/forms.py`, `cashops/views.py`, `templates/cashops/dashboard.html`, `users/migrations/0013_*`, `cashops/tests.py`, `users/tests.py`, `context.md`.
- Tests: 7 casos nuevos en `EP13BoxExpenseDebtTests` (bloqueo sin permiso, alta con permiso sin tocar la caja, ANULADA rechazada, efectivo aun bloqueado, fecha_factura->emision/periodo, vistas con/sin permiso) -> 17/17; ajuste `users` conteo modulos 6->7; suite completa 342 verde; `makemigrations --check` sin cambios; `compileall` OK.
- Pendiente operativo: la admin asigna el permiso a los cajeros para el backfill de julio y lo quita despues (Config -> Usuarios). Para clasificar (ej "cerveza") el cliente crea Rubro (permiso Config) + CategoriaCuentaPagar (permiso Tesoreria). OJO: el cajero solo carga sobre SUS propias cajas cerradas (`usuario_id`); si una caja de julio la abrio otro responsable, ese cajero no la vera.

### EP-13 Deuda: elegir sucursal (multi-sucursal por usuario) 2026-07-22

- Pedido (audio admin ARMADI SRL): la cajera Belen Marsengo (base Belgrano) tambien recibe proveedores de "Oveja Negra" (panaderia cargada como sucursal aparte, misma empresa). En "gasto como deuda" el form la ataba a su sucursal y no la dejaba elegir Oveja Negra. Pedido: que ESE usuario pueda elegir entre Estacion Belgrano y Oveja Negra. (Contradice la regla original "no puede elegir otra": ahora es opt-in por usuario.)
- Opcion: OPTIMA. Lista explicita por usuario (respeta "no todas"), regla en servicio, sin tocar el modelo de sucursales.
- Cambio:
  - `users.User.sucursales_deuda` (M2M a `cashops.Sucursal`, blank) = sucursales EXTRA (ademas de la base) donde el usuario puede imputar deuda. Metodo `User.sucursales_para_deuda()` = base + extras, activas.
  - `GastoComoDeudaForm`: campo opcional `sucursal`; el selector SOLO aparece si el usuario tiene >1 sucursal habilitada (si tiene 1, la deuda va a la de la caja, como antes).
  - `register_box_expense_debt(sucursal=None)`: si se elige otra, valida que este en `sucursales_para_deuda()` del actor Y misma empresa que la caja; si no, usa `caja.sucursal`. La deuda queda con `caja_origen` = la caja (Belgrano) pero `sucursal` = la elegida (imputacion economica correcta).
  - `PersonalForm` y `UserAccessForm` (users): nuevo campo `sucursales_deuda` (checkbox multiple) para que la admin lo asigne. Se renderiza solo (form.visible_fields).
- Datos: `users/migrations/0014_user_sucursales_deuda.py` agrega tabla M2M (aditiva, no toca datos). Deudas y usuarios existentes intactos.
- Archivos: `users/models.py`, `users/forms.py`, `cashops/forms.py`, `cashops/services.py`, `cashops/views.py`, `users/migrations/0014_*`, `cashops/tests.py`, `context.md`.
- Tests: 6 casos nuevos en `EP13BoxExpenseDebtTests` (metodo del conjunto, imputa a extra, rechaza no-permitida, rechaza cross-empresa, vista con/sin selector) -> 23/23; suite completa verde.
- Workaround inmediato para el gasto puntual: la admin lo carga desde Tesoreria -> Cuentas por Pagar -> Nueva (ahi se elige cualquier sucursal, sin caja).
- Operativo: para habilitar a Belen, la admin va a Config -> Usuarios (o ficha de acceso), y en "Sucursales adicionales para cargar deuda" tilda Oveja Negra.

### EP-13 Deuda: elegir RUBRO en vez de "categoria del gasto" 2026-07-23

- Pedido (audio admin): en "gasto como deuda" el form pedia proveedor + "Categoria del gasto" (concepto de treasury) que confunde; quieren que pida proveedor + RUBRO (los rubros que ellos manejan).
- Opcion: OPTIMA. El cajero elige un RubroOperativo; por debajo se mapea a una CategoriaCuentaPagar (reusa la activa del rubro, o crea una canonica). El modelo de deuda sigue guardando `categoria` y el economico imputa por rubro igual que antes.
- Cambio:
  - `GastoComoDeudaForm`: campo `categoria` (CategoriaCuentaPagar) -> `rubro` (RubroOperativo activo, no-sistema).
  - `treasury.services.get_or_create_payable_category_for_rubro(rubro, actor)`: reusa la categoria activa del rubro o la crea con `_save_instance` (SIN el gate `_require_actor`/ensure_treasury_admin de create_payable_category: el cajero no gestiona tesoreria, es artefacto interno del alta de deuda).
  - `register_box_expense_debt`: acepta `rubro=` ademas de `categoria=` (compat); si no viene categoria, la resuelve del rubro dentro del atomic.
  - Vista pasa `rubro=form.cleaned_data["rubro"]`.
- Datos: SIN migracion (no cambia modelos). Deudas existentes intactas. Puede crear una CategoriaCuentaPagar "sombra" por rubro (reusa si ya existe una activa).
- Archivos: `cashops/forms.py`, `cashops/services.py`, `cashops/views.py`, `treasury/services.py`, `cashops/tests.py`, `context.md`.
- Tests: 3 nuevos en EP13BoxExpenseDebtTests (servicio acepta rubro y mapea; vista pide rubro y ya no "Categoria del gasto"; rubro sin categoria crea una) -> 26/26; suite completa 354 verde.

### EP-13 Multi-caja: regla de caja abierta por FECHA 2026-07-23

- Pedido: durante el backfill de julio los cajeros se trababan porque solo podian tener UNA caja abierta por (usuario, turno, sucursal); necesitaban abrir varios dias a la vez. Pidieron poder tener varias abiertas de forma reversible.
- Opcion elegida: **B (regla por fecha)**, sobre el toggle con flag. OPTIMA: resuelve el backfill (varias fechas abiertas a la vez), sigue impidiendo duplicar el MISMO dia, no necesita flag ni acordarse de revertir, y en el uso normal (una fecha por vez) se comporta como "una sola caja". Mantiene los tests existentes.
- Verificado antes de tocar (workflow de impacto, wf_30446823): NADA revienta con varias cajas abiertas — no hay MultipleObjectsReturned, todo es box-pk-driven (`_get_box_for_request`), los listados iteran, y el auto-redirect del dashboard esta guardado con `count()==1`. La regla vivia en 3 lugares acoplados.
- Cambio:
  - `Caja.Meta` constraint: `unique_open_box_by_user_turn_branch` (usuario,turno,sucursal) -> `unique_open_box_by_user_turn_branch_date` (+ fecha_operativa), condicion estado=ABIERTA. Migracion `cashops/0022` (RemoveConstraint + AddConstraint, reversible).
  - guard de `open_box` (services.py): agrega fecha_operativa al `.exists()`; mensaje "...en este turno, sucursal y fecha."
  - guard de `update_box_metadata` (services.py): idem + fecha_operativa; mensaje "...responsable, sucursal, turno y fecha."
  - `dashboard.html`: las etiquetas de cajas abiertas ahora muestran turno + fecha (antes dos cajas que diferian solo por fecha se veian identicas).
  - comando `cajas_abiertas`: copy actualizado (regla por fecha).
- Datos: migracion `0022` segura — la constraint vieja ya garantizaba 0 duplicados por (usuario,turno,sucursal), asi que la nueva (con fecha) tampoco tiene duplicados y AddConstraint no falla. Reversibilidad: re-imponer la constraint vieja fallaria si en ese momento hubiera 2+ cajas ABIERTA del mismo (usuario,turno,sucursal); habria que cerrar/deduplicar antes (como hizo 0011).
- Archivos: `cashops/models.py`, `cashops/services.py` (2 guards), `cashops/migrations/0022_*`, `templates/cashops/dashboard.html`, `cashops/management/commands/cajas_abiertas.py`, `cashops/tests.py`, `context.md`.
- Tests: nuevo `test_open_box_allows_different_date_same_user_turn_branch` (otra fecha permitida, misma fecha rechazada); los tests de duplicado (misma fecha) siguen verdes; `cashops.tests_commands` 13/13.

### Permiso configurable "Eliminar movimientos de caja" 2026-07-23

- Pedido (audio admin): entra a una caja -> editar -> "ver composicion de la caja" -> ve todos los movimientos; las chicas cargan gastos de mas y quiere poder borrar los errores sin recargar todo. Pidio ademas que sea un permiso configurable asignable "al que yo quiera".
- Decision de alcance (confirmada con el usuario): borrar tanto movimientos en efectivo (MovimientoCaja) COMO gasto-como-deuda (CuentaPorPagar), en cajas ABIERTAS y CERRADAS. Borrado = anulacion auditada (soft-delete), NUNCA hard-delete (filosofia del repo).
- Hallazgo clave del mapeo (workflow wf_16cd80b8): la maquinaria de anulacion de movimiento YA existia pero solo para cajas CERRADAS bajo `CASHOPS_CLOSED_FIX` (`annul_closed_box_movement`); no habia camino para cajas abiertas ni para deudas puntuales. El motor de saldo (`Caja.saldo_esperado`) y los agregados economicos ya filtran estado=REGISTRADO/no-ANULADA, asi que anular revierte solo.
- Slice 1 (permiso): nuevo `PermissionModule.CASHOPS_MOV_DELETE` ("Eliminar movimientos de caja", value `cashops_mov_del`, 15<=20 chars) + `User.can_delete_box_movement()` + entrada en `PERMISSION_MODULE_META` (users/views) + helpers `can_delete_box_movement`/`can_delete_movement_in_box`/`ensure_delete_movement_in_box` en `cashops/permissions.py`. `can_delete_movement_in_box` = permiso nuevo (cualquier caja) OR compat closed-fix (solo cerradas). Migracion `users/0015` (AlterField choices en RolePermission+UserPermission, sin data migration; default OFF salvo ADMIN/superuser).
- Slice 2 (movimiento efectivo): servicio `annul_box_movement` (abiertas+cerradas): valida con `_validate_box_movement_for_deletion` (caja no ANULADA, mov REGISTRADO, tipo no en CLOSED_BOX_CORRECTION_BLOCKED_TYPES -> transferencias/apertura/ajuste bloqueados), crea `MovimientoCajaCorreccion(ANULACION)`, estado=ANULADO+auditoria, y bifurca: caja CERRADA -> `_recalculate_closed_box_after_correction`; ABIERTA -> `resync_operational_control_for_caja` (no hay CierreCaja, no revienta). Renombre: vista/ruta/servicio `closed_box_movement_delete*` -> `box_movement_delete*` (el EDIT sigue closed-fix, intacto). Composicion (`movement_list.html`): boton Editar bajo `can_fix_closed_box`, Eliminar bajo `can_delete`. Aislamiento empresa/sucursal via `_get_box_for_request` en la vista.
- Slice 3 (gasto-como-deuda): servicio `annul_box_originated_debt` (soft-delete de CuentaPorPagar: estado ANULADA, saldo 0, motivo/quien/cuando; bloquea si hay pagos REGISTRADO -> resolver en tesoreria primero). Se extrajo `_apply_debt_annulment` compartido con `annul_box` (evita duplicar la formula de anulacion de deuda; annul_box sigue verde). En el timeline el evento GASTO_DEUDA lleva `debt_id`+`debt_active`; la vista arma `delete_url` a `box_debt_delete` cuando hay permiso; boton en el historial (`box_detail.html`). NO usa `treasury.annul_payable` (tiene gate treasury-admin via `_require_actor`); replica el patron inline de annul_box, gateado por el permiso de caja.
- Datos: solo migracion `users/0015` (choices). No toca datos existentes.
- Archivos: `users/models.py`, `users/views.py`, `users/migrations/0015_*`, `users/tests.py`, `cashops/permissions.py`, `cashops/services.py`, `cashops/views.py`, `cashops/urls.py`, `templates/cashops/partials/movement_list.html`, `templates/cashops/box_detail.html`, `cashops/tests.py`, `context.md`.
- Tests: users +1 clase `MovementDeletePermissionTests` (4) + conteo modulos 7->8; cashops +3 servicio movimiento, +4 vista movimiento (abierta/permiso/aislamiento), +3 servicio deuda, +4 vista deuda. Renombrados los tests de delete de caja cerrada (siguen verdes por la logica OR).

### Cierre de los pendientes cosmeticos de la auditoria + resolver alertas 2026-07-31

Se cerraron los 4 cosmeticos que quedaban de la auditoria del 29/07 y el bug de
resolver alertas a mano.

- RESOLVER ALERTAS (era un 500 esperando): `resolve_alert` usaba `AlertaOperativa`
  sin tenerlo importado a nivel de modulo (el unico import vivia dentro de
  `reset_operational_data`) -> NameError al entrar. Se agrego al import de
  `cashops/views.py` y se saco el import local que quedo redundante. Ademas la ruta
  no estaba enlazada en NINGUN template: se agrego el boton "Marcar como resuelta"
  en `alert_panel.html`, gateado por `puede_resolver_alertas`
  (= `request.user.is_cashops_admin()`), porque el panel se abre con Configuracion en
  LECTURA pero resolver es escritura. Y se limito la vista a POST con
  `@require_http_methods(["POST"])`: antes un GET alcanzaba para cerrar una alerta.
  POR QUE IMPORTA: las alertas de DIFERENCIA GRAVE nacen de un cierre concreto y no
  se auto-resuelven nunca (solo las de RUBRO EXCEDIDO se resuelven y reabren solas),
  asi que sin esto quedaban en el panel para siempre.
- TYPO: "conciliacion de de ventas" -> "conciliación de ventas"
  (`reconciliation_page.html`).
- BARRA HARDCODEADA: la distribucion efectivo/banco de Disponibilidades estaba fija
  en 40%/60%, o sea dibujaba una proporcion inventada. Ahora usa `{% widthratio %}`
  sobre `saldo_final_efectivo` y `total_bancos_final` contra `total_consolidado`, y
  solo se dibuja si el total y las dos partes son positivos (con banco negativo la
  proporcion no tiene sentido visual). Verificado en la demo: 97% / 3%, que coincide
  con $244.834.600 de efectivo contra $7.193.000 de banco.
- VARIABLES CSS INEXISTENTES en `disponibilidades_report.html`: `var(--transition)`,
  `var(--success)` y `var(--gradient-primary)` no estan definidas en ningun layout,
  por eso el encabezado se veia gris roto. Reemplazadas por las del tema
  (`--accent`, `--forest-deep`, `--accent-soft`).
- LINKS MUERTOS en `templates/base.html`: la barra lateral y la barra inferior movil
  apuntaban a `#acciones`, `#movimientos` y `#cierre`, anclas que no existen en
  ninguna pagina que use ese layout. Importa porque ese layout lo usa
  `password_change_required.html`, que SI ve un usuario autenticado (la landing y el
  login redirigen al autenticado, esa pantalla no). Se dejo un solo link "Ir al
  sistema" y se elimino la barra movil completa: las pantallas de operacion tienen su
  propia navegacion en cashops/layout.html y treasury/layout.html.
- Tests: `ResolveAlertViewTests` en cashops/tests.py (4: admin resuelve, GET da 405,
  operador sin Configuracion da 403, y el boton aparece solo para quien puede
  escribir config, usando un UserPermission de solo lectura).
- Suite: 435 -> 439 tests, todos en verde.
- PENDIENTE DE DATOS DE DEMO (no de producto): la base de demo quedo con 188 alertas
  de RUBRO_EXCEDIDO sin resolver porque el sembrado puso limites de 6% y 4% en
  Mantenimiento y Limpieza. El motor de alertas funciona bien, pero para una reunion
  comercial conviene subir esos limites en `Demo/sembrar-demo.py` y regenerar.

### URGENTE 1 de la auditoria: el pago ahora SI baja el saldo del banco 2026-07-31

Hallazgo de la auditoria del 29/07: `register_payment` generaba movimiento de
disponibilidad SOLO para EFECTIVO (caja fuerte). Transferencia, cheque y ECHEQ
bajaban `saldo_pendiente` de la deuda pero NO tocaban el banco, asi que el saldo
bancario quedaba inflado y el KPI "Banco menos deuda pendiente" era
sistematicamente OPTIMISTA: la deuda bajaba y el banco no. Urgente porque se
acababa de entregar "pagar por proveedor" para atacar los $497M y pagan casi todo
por transferencia: cada pago agrandaba el error.

- `_create_bank_movement_for_payment` (treasury/services.py): crea el DEBITO con
  `origen=PAGO_TESORERIA` heredando proveedor, categoria, rubro, sucursal y periodo
  de la deuda pagada (misma herencia que ya hacia `link_payment_to_bank_movement`),
  y marca el pago `estado_bancario=IMPACTADO`. La clase sale de
  `_infer_bank_movement_class`: TRANSFERENCIA_TERCEROS / CHEQUE / ECHEQ.
- Se llama en `register_payment` como rama `elif bank_account is not None` del if de
  EFECTIVO. ORDEN CRITICO: va ANTES de `_recalculate_payable_locked`, porque una vez
  que la deuda queda PAGADA el `clean()` de PagoTesoreria rechaza cualquier
  re-guardado del pago ("La cuenta por pagar ya esta cancelada") y el save de
  estado_bancario explotaria. El test de pago total cubre esa dependencia.
- SALIDA SEGURA: si la deuda no tiene rubro + sucursal + periodo, NO se crea el
  debito (el clean del modelo lo exigiria) y el pago se registra igual con
  estado_bancario PENDIENTE. Preferimos no bloquear una cobranza por un dato de
  catalogo faltante. En produccion las 694 deudas nacen de cajas y tienen los tres
  datos, asi que el caso normal queda cubierto.
- `generado_por_pago` (BooleanField, migracion `treasury/0029`, aditiva sin
  backfill): distingue el debito que genero el sistema del que alguien cargo a mano
  y despues vinculo. Cambia la ANULACION del pago:
  * generado por el sistema -> el debito se ANULA (nunca existio en el banco; si
    quedara vigente como MANUAL inflaria el egreso y contaria el gasto DOS VECES,
    porque la deuda ya lo conto al cargarse y un debito MANUAL cuenta por si mismo).
  * cargado a mano y vinculado -> se libera a MANUAL sin anular, como antes: esa
    plata SI salio del banco y borrarla es decision de la persona.
  Los historicos quedan en False = comportamiento previo intacto.
- Se desvincula siempre antes de anular: el `clean()` de MovimientoBancario exige
  pago REGISTRADO cuando hay pago vinculado, sin exencion para el movimiento anulado.
- PROBADO EN LA BASE DEMO (pago de $180.000): banco 31.873.000 -> 31.693.000 y
  deuda 109.360.000 -> 109.180.000, con la COBERTURA IGUAL en -77.487.000. Antes
  solo bajaba la deuda y la cobertura "mejoraba" $180.000 sin que saliera un peso.
  Al anular, los tres numeros volvieron exactos al original.
- Tests: `treasury/tests_bank_impact.py` (7 nuevos: transferencia crea el debito con
  imputacion heredada, cheque/ECHEQ con su clase, efectivo intacto, deuda sin
  sucursal no bloquea el pago, banco+cobertura reflejan el pago, anular anula el
  autogenerado, anular NO anula el cargado a mano). Helper
  `_discard_auto_generated_bank_movement` en TreasuryTestCase para los casos que
  ejercitan vincular a mano (pago_tesoreria es OneToOne y el pago ya trae el suyo).
  Se actualizo `test_transfer_payment_hits_financial_only_with_real_bank_movement`
  -> `..._hits_financial_when_paid_and_economic_counts_once`: documentaba el
  comportamiento viejo; su invariante real (el gasto no se cuenta dos veces en la
  lectura economica) se mantiene y sigue verde.
- BUG PREEXISTENTE ENCONTRADO, NO arreglado en este slice: "Vincular a pago" no
  funciona si la deuda quedo PAGADA, porque `link_payment_to_bank_movement`
  re-guarda el pago y el `clean()` de PagoTesoreria lo rechaza. Afecta a la salida
  manual del caso sin imputacion completa.
- Suite: 428 -> 435 tests, todos en verde.

### Suite 24x mas rapida + baja de codigo muerto en core 2026-07-29

Pregunta del usuario: "es necesario 430 tests o podriamos sacar algunos?". Se
midio antes de opinar y la respuesta fue NO sacar tests: el cuello de botella no
era la cantidad.

- DIAGNOSTICO: Django 5.2 hashea con PBKDF2 a 1.000.000 de iteraciones = 384 ms
  por hash (medido). Cada setUp crea 3-5 usuarios y corre una vez por test, asi
  que 430 tests x ~4 usuarios x 384 ms ~= 660 s, contra los 570 s que tardaba la
  suite: practicamente TODO el tiempo era hasheo, no tests.
- FIX (`config/settings.py`): `if RUNNING_TESTS: PASSWORD_HASHERS = [MD5, PBKDF2]`.
  Se reusa el flag `RUNNING_TESTS` que ya existia (mismo que exime la guarda de
  SECRET_KEY), asi corre rapido en local y en CI sin `--settings` ni tocar el
  workflow. PBKDF2 queda segundo para poder VERIFICAR hashes fuertes.
  Verificado: sin "test" en argv el hasher sigue siendo PBKDF2 (produccion intacta).
  MEDIDO: suite 570 s -> 23,5 s (24x). CashopsViewTests 97,8 s -> 3,6 s.
- Comparacion que justifica la decision: borrar 100 tests (un cuarto de la
  cobertura) habria ahorrado ~130 s y dejado la suite arriba de 7 minutos.
- BAJA DE CODIGO MUERTO (el unico test que si sobraba, por otra razon):
  `CoreShellFilesTests` (2 tests) solo verificaba que dos .html existieran y
  contuvieran un texto, sin ejercitar comportamiento, y encima FIJABA codigo
  muerto impidiendo borrarlo. Se elimino junto con lo que protegia:
  `core/views.py::dashboard` (60 lineas de datos inventados a mano, "Caja 04",
  "AR$ 248.500"), la linea `home = dashboard` que quedaba sobrescrita dos lineas
  despues por `home = public_home`, `core/urls.py` (definia core:dashboard pero
  config/urls.py nunca incluyo core.urls: nadie lo referenciaba, verificado por
  grep), y 3 plantillas: `core/templates/core/dashboard.html` (solo la renderizaba
  la vista muerta) mas `core/templates/base.html` y
  `core/templates/registration/login.html`, que estaban TAPADAS por las de
  `templates/` (verificado con el loader real de Django antes de borrar).
  Queda vivo `core/templates/core/home.html`, que es la landing publica.
  Post-borrado verificado: las 3 plantillas vivas resuelven al mismo archivo que
  antes, y landing (/), login (/login/) y el ingreso de un cajero dan 200.
- Suite: 430 -> 428 tests, todos en verde.

### "Cobro en efectivo" reservado a administracion 2026-07-29

Pedido de la administracion por WhatsApp ("Podes borrarle esta opcion a los
chicos... deberian cargar todo en una venta nomas"). El motivo real: `box_income`
(`register_cash_income`) es el UNICO ingreso que entra sin rubro (categoria de
texto libre), asi que esa plata no cae en ningun rubro del analisis economico.

- `cashops/permissions.py`: `can_register_cash_income(user)` = `is_cashops_admin`
  (CONFIG write) + `ensure_cash_income`. La politica vive SOLO ahi: si despues se
  quiere asignar por usuario, se reemplaza por un PermissionModule propio y no hay
  que tocar vistas ni templates.
- `cashops/views.py`: `register_cash_income_view` suma `_require_cash_income`
  DESPUES de `_require_cashops_write` (403 por URL directa, no alcanza con ocultar
  el boton); el dashboard expone `can_register_cash_income` al contexto.
- `templates/cashops/dashboard.html`: la tarjeta va dentro de
  `{% if can_register_cash_income %}`, y "Registrar venta" ahora dice
  "efectivo, tarjeta, QR, PedidosYa o transferencia" para que el cajero sepa por
  donde cargar el efectivo.
- CAMINO DE REEMPLAZO VERIFICADO: `VentaGeneralForm` ofrece todos los CanalIngreso
  activos, incluido `INGRESO_EFECTIVO` (`impacta_saldo_caja=True`), y
  `register_general_sale` exige rubro. O sea: el cajero sigue pudiendo cargar
  efectivo, mejor clasificado. Sin esto el cambio lo dejaria sin poder cobrar.
- ALCANCE: tambien lo pierde el ENCARGADO (no tiene CONFIG write). Es deliberado:
  el argumento del rubro obligatorio aplica igual. Si la admin lo quiere solo para
  cajeros, se cambia la condicion de `can_register_cash_income`.
- Tests (cashops/tests.py, CashopsViewTests): el test existente
  `test_cash_income_view_registers_income_and_redirects` pasa a correr como admin
  (antes operator); +4 nuevos: 403 para no-admin sin mover el saldo, tarjeta oculta
  para no-admin, visible para admin, y que el canal Efectivo de la venta sigue
  disponible para el operador con rubro imputado.
- `docs/manual-demo-camino-feliz.md`: paso 2.4 marcado como solo administracion.

### Separador de miles en toda la UI (USE_THOUSAND_SEPARATOR) 2026-07-29

Los importes se mostraban crudos ($275858100) en toda pantalla que no usara el filtro
`|money` (solo 3 plantillas lo usaban; 53 importes vivos quedaban sin formato). Fix:

- `config/settings.py`: `USE_THOUSAND_SEPARATOR = True`. Con `LANGUAGE_CODE=es-ar`
  todo int/float/Decimal renderizado por template sale "1.234.567,89". Los widgets de
  formulario, los args de `{% url %}` y el CSV (csv.writer de Python) NO se ven afectados.
- CONTRAPARTIDA: el setting tambien localiza ids/anios interpolados A MANO en
  href/value/querystring ("?box=1.234" rompe el link; `value="2.026"` rompia el form de
  CERRAR MES hoy mismo, no a futuro). Se blindaron con `{% load l10n %}` + `|unlocalize`
  los 26 casos funcionales + los "Caja #" visibles, en 13 templates: cashops
  dashboard/layout/alert_panel/box_tracking/box_validation_queue/box_detail/box_reject/
  management_matrix y treasury dashboard/layout/disponibilidades_report/selection_page/
  supplier_payment_batch. Mapeo exhaustivo por workflow (399 interpolaciones clasificadas,
  0 JS que parsee numeros del DOM, verificacion adversarial de cada funcional).
- `payment_list.html` y `bank_movement_list.html` tienen ids crudos pero son plantillas
  MUERTAS (ninguna vista las renderiza): no se tocaron a proposito.
- Red de regresion: `core/tests_localization.py` (9 tests) renderiza las pantallas clave
  con pks >= 1000 forzados y caza con regex cualquier numero con separador dentro de
  href/action/value; ademas asserts positivos de montos con separador y del
  `value="2026"` del form de cierre. Si un template nuevo interpola un id sin blindar,
  esto lo detecta antes del deploy.
- Regla para templates nuevos: todo id/pk/anio interpolado en un atributo o querystring
  lleva `|unlocalize`; los counts y porcentajes visibles se dejan localizar.

### Tesoreria: pedido de la administracion (pagos, mes cerrado y arreglos) 2026-07-29

Pedido de 5 puntos por WhatsApp. Clasificado con el usuario en MANTENIMIENTO (arreglos
de funciones ya existentes, sin cargo) vs FUNCIONALIDAD NUEVA. El punto de vincular
transferencias queda FUERA de main (ver `NOTA-FEATURE-OCULTA.md`).

- **Desglose de deuda (+400M)**: NO era bug de conteo. Comando `desglose_deuda` corrido
  en produccion: deuda viva = importe = devengado = $497.740.001 (694 deudas), 149
  anuladas bien excluidas, 0 sin sucursal, 0 arrastre viejo. El 100% son gastos cargados
  como deuda desde las cajas, sin ningun pago registrado, concentrados en SAVHA (46%) y
  sucursal VIVRE (92%). O sea: deuda real mal cargada, no error del sistema. Se entrego
  reporte PDF a tesoreria.
- **A1 mes cerrado**: `close_treasury_month` solo miraba `validacion_estado`
  PENDIENTE/RECHAZADA; una caja ABIERTA nace con NO_REQUERIDA (el estado se define al
  cerrar), asi que pasaba de largo y el mes congelaba un saldo que despues cambiaba.
  Ahora rechaza con mensaje propio. El cierre es GLOBAL (unique solo por mes, la
  sucursal nunca se escribe): NO filtrar por sucursal, daria siempre False.
- **A2 apertura en mes cerrado**: nuevo `treasury_month_is_closed()` en cashops/services
  (import perezoso `apps.get_model`, mismo patron que `_push_box_closure_to_central_cash`)
  + guard en `open_box` y en `update_box_metadata` (esta ultima permitia MOVER una caja a
  un mes cerrado; se bloquea solo si cambia de mes). Decision: reemplaza la tolerancia
  anterior; el re-fechado al dia de validacion queda como red de seguridad para datos
  LEGACY (se adapto `test_validation_after_month_close_redates_central_push` para
  construir ese estado salteando el servicio).
- **VALVULA**: no existia NINGUNA forma de reabrir un mes (ni vista, ni admin, ni
  comando), asi que A2 podia dejar la operacion trabada. Se registro
  `CierreMensualTesoreria` en el admin (saldos read-only, sin alta) para destildar
  "cerrado" en emergencia.
- **A4 fuga de empresa**: los 4 forms de pago ofrecian deudas y cuentas bancarias de
  TODAS las empresas (el listado de cuentas por pagar si filtraba). Nuevo helper
  `open_payables_queryset(empresa_ids)` + scope de cuenta bancaria, inyectado desde
  `_register_payment_view` (unico punto de instanciacion de los 4 forms).
- **B1+B2 pago por proveedor (NUEVO)**: pantalla nueva en dos pasos por GET (sin JS):
  elegir proveedor (solo los que tienen facturas impagas) -> tildar 1 o varias facturas
  con importe editable precargado en el saldo. Registra UN PagoTesoreria POR FACTURA (no
  se toca el esquema: sigue 1 pago -> 1 deuda, asi el recalculo de saldo, la guarda de
  sobrepago y el compromiso especial siguen valiendo). Atomico: si falla una linea no se
  registra ninguna. La referencia se sufija "(1/3)" por la unicidad
  (cuenta_bancaria, medio_pago, referencia). Solo TRANSFERENCIA y EFECTIVO: cheque/ECHEQ
  son instrumentos individuales. `form_card.html` acepta `form_method="get"` para el paso
  de seleccion. `PayableChoiceField` muestra el saldo en las pantallas viejas.
- **A3 pago anulado dejaba el movimiento bancario COLGADO**: apuntaba a un pago anulado
  con origen PAGO_TESORERIA, combinacion que su propio `clean()` rechaza (exige pago
  REGISTRADO) -> no se podia editar, ni eliminar, ni re-vincular, ni imputar; solo se
  arreglaba por shell. Nuevo `_release_bank_movement_from_annulled_payment`: lo devuelve
  a MANUAL conservando la imputacion y el proveedor, ANTES de flipear el pago (si no, el
  full_clean del movimiento falla). NO se anula el movimiento: el debito es real, la
  plata salio del banco; lo que se deshizo es su imputacion a esa deuda.
- **A5**: `build_supplier_history_snapshot.historical_total` no excluia ANULADA pero
  `historical_pending` si -> historial inflado e incoherente consigo mismo. Ahora ambos
  excluyen.
- **A6 etiquetas del dashboard (causa del susto del 400M)**: sin tocar ningun calculo.
  "Deuda pendiente" -> "Deuda pendiente (acumulada)" aclarando que es de toda la
  historia (las tarjetas vecinas si son del periodo); "Deuda del periodo" -> "Gasto en
  deuda del periodo" aclarando que suma el importe TOTAL incluidas las ya pagadas.
- **A7**: `build_treasury_dashboard_snapshot` era codigo muerto (importado en views, sin
  ningun call site en .py ni .html) y sin filtro de empresa: si alguien lo cableaba
  filtraba TODAS las empresas. Eliminado junto con su import.
- Ademas: el error del cierre mensual se mostraba como `['mensaje']`
  (`str(ValidationError)` es `repr(list)`); ahora sale limpio.

Archivos: `treasury/services.py`, `treasury/views.py`, `treasury/forms.py`,
`treasury/urls.py`, `treasury/admin.py`, `treasury/tests.py`,
`treasury/management/commands/desglose_deuda.py`, `treasury/tests_commands.py`,
`cashops/services.py`, `cashops/tests.py`,
`templates/treasury/supplier_payment_batch.html`,
`templates/treasury/partials/form_card.html`, `templates/treasury/dashboard.html`,
`NOTA-FEATURE-OCULTA.md`, `context.md`.

### Cartelito temporal de error en acciones HTMX 2026-07-22

- Pedido: un cajero tocaba "Abrir caja" y "no pasaba nada, ni un mensaje de error". Objetivo: cuando una accion no se puede completar, mostrar arriba un cartelito temporal con el motivo en lenguaje simple, para que la persona resuelva sola (ej: falta un campo) y solo si no puede, avise al encargado. Sin mensajes tecnicos.
- Causa raiz: los formularios se envian por HTMX (`hx-post` + `hx-target="#form-card"` + `hx-swap="outerHTML"`) y las vistas devuelven HTTP 400 cuando el form no valida o una regla de negocio lo rechaza (`open_box_view` y demas usan `status=400`). HTMX 1.9.12 NO intercambia respuestas 4xx por defecto y no habia handler `htmx:beforeSwap`, asi que el form re-renderizado (con el error adentro) se descartaba en silencio. Nota: por ser un 400 controlado, tampoco aparecia en logs de Railway.
- Opcion: OPTIMA. Slice 100% de presentacion. Los textos de error ya viven humanos en `forms.py`/`services.py` (ej "Ya existe una caja abierta para ese usuario en este turno y sucursal."); este cambio solo los muestra. No toca vistas, forms, servicios, modelos ni permisos.
- Cambio:
  - Nuevo partial compartido `templates/partials/htmx_error_toast.html` (contenedor + CSS + JS) incluido antes de `</body>` en `cashops/layout.html` y `treasury/layout.html`.
  - `htmx:beforeSwap`: en 400/422 fuerza `shouldSwap=true` e `isError=false` (asi el form muestra ademas los errores inline por campo) y levanta un cartelito arriba, temporal (auto-cierre ~8s, tap para cerrar). Arma el texto leyendo `.errors li` (regla/no-campo, prioritarios) y `.field .error` con su `<label>` ("Campo: mensaje"); si hay varios, resume 3 + "(+N mas)".
  - `htmx:responseError` (403/500): mensaje generico "No pudimos completar la accion... si sigue igual, avisa a tu encargado." `htmx:sendError` (sin conexion): aviso de red. El 400/422 no duplica cartelito.
- Alcance: solo errores. El toast de exito (bottom-right, Django messages en redirect) sigue igual.
- Archivos: `templates/partials/htmx_error_toast.html` (nuevo), `templates/cashops/layout.html`, `templates/treasury/layout.html`, `context.md`.
- Tests: sin cambios de servidor (el contrato ya estaba cubierto por `cashops.tests` `test_duplicate_open_box_returns_validation_feedback_without_500`: 400 + mensaje humano en el body). Regresion de render: `cashops` 167 OK, `treasury` 132 OK (1 skip), `manage.py check` limpio. Logica del JS verificada en navegador real con harness sintetico de eventos htmx: 10/10 asserts OK (texto de regla, campo+label, resumen 3+extra, fallback 400, 403 generico, no-duplicado, 200 no toca nada).
- Pendiente (aparte, no incluido): la causa concreta por la que a ese cajero no lo dejaba abrir queda por confirmar con el dato real (lo mas probable: ya tenia una caja abierta en ese turno+sucursal). Con el cartelito ya se ve el motivo.

### Comando diagnostico `cajas_abiertas` 2026-07-22

- Pedido: confirmar por que un cajero (Victor Cruz) no podia abrir caja, sin esperar a que vuelva a pasar. Ligado a la entrada anterior (cartelito de error).
- Recordatorio de la regla: `Caja` tiene `UniqueConstraint unique_open_box_by_user_turn_branch` (una sola caja ABIERTA por usuario+turno+sucursal) y `open_box()` valida lo mismo antes de crear; si ya existe, la apertura falla con "Ya existe una caja abierta para ese usuario en este turno y sucursal.".
- Opcion: OPTIMA. Management command de SOLO LECTURA (patron ya usado: `resync_operational_engine`, `reporte_sin_sucursal`), seguro para correr en Railway (`railway run python manage.py cajas_abiertas --usuario victor`).
- Que hace: lista cajas ABIERTAS, filtrable por `--usuario` (username/nombre/apellido, icontains) y `--empresa`. Por cada caja muestra responsable (marca si es usuario fijo + su sucursal base), sucursal, turno+empresa, fecha operativa, apertura, monto inicial y estado de validacion, y la nota de que bloquea abrir otra en ese turno+sucursal. Si el usuario no tiene ninguna caja abierta, aclara que el bloqueo NO es por duplicado y sugiere revisar turno/empresa y sucursal base (usuario fijo). No escribe nada.
- Archivos: `cashops/management/commands/cajas_abiertas.py` (nuevo), `cashops/tests_commands.py` (nueva clase `CajasAbiertasCommandTests`), `context.md`.
- Tests: `CajasAbiertasCommandTests` 3/3 OK (lista + nota de conflicto por usuario; caso vacio con guia; no persiste cambios); `compileall` OK.

### Mensaje humano para referencia duplicada en CuentaPorPagar 2026-07-25

- Pedido (foto del form "Gasto como deuda"): al cargar una deuda con una referencia ya usada por ese proveedor, el cartelito mostraba el texto tecnico crudo "No se cumple la restricción «unique_payable_reference_by_supplier»". Regla del usuario, repetida: NUNCA mostrar mensajes tecnicos al usuario final.
- Causa raiz: `CuentaPorPagar` tiene `UniqueConstraint unique_payable_reference_by_supplier` (proveedor + referencia_comprobante, cuando referencia != ""). `register_box_expense_debt` hace `payable.full_clean()`, que valida constraints, y como la constraint no definia `violation_error_message`, Django uso su default tecnico. El cartelito HTMX solo repite el texto del backend: el problema era el texto de origen, no el cartelito.
- Opcion: OPTIMA. Mensaje humano en la fuente (modelo); la regla anti-duplicado queda intacta (el rechazo estaba bien: ya existia una deuda con esa referencia para ese proveedor).
- Cambio: `violation_error_message="Ya existe una deuda cargada con esa referencia/comprobante para este proveedor."` en la constraint. Migracion `treasury/0026_alter_cuentaporpagar_unique_payable_reference_by_supplier.py` (`AlterConstraint`: solo metadata de validacion, no toca schema ni datos).
- Archivos: `treasury/models.py`, `treasury/migrations/0026_*`, `context.md`.
- Tests: suite completa `treasury` + `cashops` 302 OK (1 skip); `makemigrations --check` sin cambios pendientes.
- Pendiente: el resto de las constraints del proyecto (~37, en `users`/`cashops`/`treasury`) siguen con el default tecnico de Django; barrer en slice aparte las alcanzables desde formularios (rubro/producto duplicado, proveedor/CUIT, cuentas bancarias, referencia de pago, `payable_due_after_issue`, etc.).

### Filtro de sucursal en Pendientes de validacion 2026-07-25

- Pedido (foto del modulo con 133 cajas pendientes): "en este modulo por fa podes ponerme el filtro de las sucursales tambien. Asi controlo sucursal por sucursal".
- Opcion: OPTIMA. Filtro real en backend (queryset), mismo patron que `management_matrix`/`alert_panel`; no toca reglas de validacion ni permisos.
- Cambio:
  - `box_validation_queue`: lee `?sucursal=` (GET) y lo valida contra sucursales ACTIVAS de las empresas seleccionadas (invalido o cross-empresa => se ignora y muestra todas); filtra el queryset. Contexto nuevo: `sucursales`, `selected_sucursal`.
  - Template cola: select "Sucursal" (Todas + activas) + boton Aplicar, linea "Mostrando solo X." y empty-state especifico por sucursal. El contador "N cajas" refleja el filtro.
  - El filtro NO se pierde al operar: `validate_url`/`reject_url` de cada fila llevan `?sucursal=`, y `box_validate_view`/`box_reject_view` redirigen con `_validation_queue_url(request)` (helper nuevo: solo acepta int, arma la URL con `reverse`, sin open redirect). `box_reject.html` usa `back_url` para "Volver a pendientes"/"Cancelar".
- Archivos: `cashops/views.py`, `templates/cashops/box_validation_queue.html`, `templates/cashops/box_reject.html`, `cashops/tests.py`, `context.md`.
- Tests: 6 casos nuevos en `EP13CashValidationViewTests` (filtra por sucursal, ignora invalido, ignora cross-empresa, acciones llevan el filtro, redirect de validar lo conserva, redirect+back de rechazo lo conservan) -> clase 15/15; suite completa `cashops` 176 OK; `makemigrations --check` sin cambios; `compileall` OK.

### Barrida: mensajes humanos en TODAS las constraints 2026-07-25

- Pedido: tras el fix puntual de `unique_payable_reference_by_supplier`, "fixeate los otros". Objetivo: que ninguna constraint pueda mostrar el default tecnico de Django ("No se cumple la restricción «nombre»") en pantalla.
- Opcion: OPTIMA. Mismo mecanismo que el fix puntual (`violation_error_message` en la fuente), mecanico y sin tocar reglas: ninguna condicion de constraint cambia.
- Cambio: las 37 constraints restantes de `users` (2), `cashops` (11) y `treasury` (24) ahora tienen `violation_error_message` en espanol y lenguaje de negocio. Donde ya existia una frase en services/forms se reuso identica (ej. "Ya existe una caja abierta para ese usuario en este turno, sucursal y fecha." — alineada a la regla por fecha de `unique_open_box_by_user_turn_branch_date` —, "Ya existe un rubro con ese nombre.", "El rubro es obligatorio para gastos operativos."). Verificado por script: 38/38 constraints con mensaje humano.
- Datos: migraciones `users/0016_*`, `cashops/0023_*`, `treasury/0027_*` — solo operaciones `AlterConstraint` (en Django 5.2 es no-op a nivel base de datos: solo metadata de validacion). Cero `AddConstraint`/`RemoveConstraint`/`RunSQL`. Rebaseado sobre la regla nueva de caja por fecha (commit `0115147`); `treasury/0027` depende de `cashops/0022_remove_...` (la del remoto).
- Archivos: `users/models.py`, `cashops/models.py`, `treasury/models.py`, 3 migraciones nuevas, `context.md`.
- Tests: suite completa `users`+`cashops`+`treasury`+`core` verde (ver commit); ningun test asertaba los defaults tecnicos (grep "is violated"/"no se cumple" en tests: 0). `makemigrations --check` limpio; `compileall` OK.

### Token de alta: el reenvio de un formulario ya no duplica plata 2026-07-27

- Pedido (foto de "Ultimos movimientos"): dos "Egreso operativo" identicos, $89.350 al rubro BEBIDAS SIN ALCOHOL, mismo minuto. "hay algo que pueda llegar a hacer que se dupliquen egresos?". Se confirmo que si, y se pidio aplicarlo para dejar de arrastrar el problema.
- Causa raiz, dos capas y ninguna defendia:
  - Servidor: `register_expense` (y las demas altas) hacian `MovimientoCaja.objects.create()` directo. El modelo solo tenia los checks de monto>0 y rubro obligatorio: dos POST identicos = dos movimientos validos, los dos descontando efectivo. El `select_for_update()` de la caja serializa pero no descarta: el segundo espera al primero y graba igual.
  - Cliente: `form_card.html` tenia `hx-post` pelado, sin `hx-disabled-elt` ni `hx-sync`, y el boton quedaba clickeable durante todo el request. Cero ocurrencias de esos atributos en el repo.
  - Tres caminos reales: doble click; Enter + click; y el peor, timeout donde el server ya grabo pero la respuesta no volvio y el toast de `htmx:sendError` dice "probá de nuevo" — el sistema invitaba a reintentar algo que ya estaba grabado.
  - Descartado que fuera duplicado de pantalla: `recent_movements` es `selected_box.movimientos.select_related(...)`, reverse FK sin join multiplicador. Las dos filas eran reales.
- Opcion: OPTIMA (token de idempotencia). Se descarto la ventana anti-repetido por tiempo (monto+rubro+hora) por dos motivos: no distingue "el mismo envio otra vez" de "otra operacion que casualmente es igual", asi que puede bloquear trabajo legitimo; y en el caso del timeout le contesta al cajero con un error cuando la operacion si salio bien. El token distingue las dos cosas y en el reenvio responde exito, que es la verdad.
- Cambio:
  - `MovimientoCaja.token_alta` y `CuentaPorPagar.token_alta` (UUID, null, `editable=False`) + `UniqueConstraint` PARCIAL (`condition=Q(token_alta__isnull=False)`) en cada uno: `unique_movement_creation_token`, `unique_payable_creation_token`.
  - `AltaIdempotenteForm` (base nueva en `cashops/forms.py`): hidden `token_alta` con UUID nuevo por render; en form ligado el valor sale de los datos enviados, asi que sobrevive a un render con errores. Heredan `IngresoEfectivoForm`, `GastoRapidoForm`, `GastoComoDeudaForm`, `VentaGeneralForm`, `TransferenciaEntreCajasForm`. `form_card.html` ya renderiza `form.hidden_fields`: la plantilla no necesito campo nuevo.
  - Servicios (`register_expense`, `register_cash_income`, `register_card_sale`, `register_general_sale`, `transfer_between_boxes`, `register_box_expense_debt`): reciben `token_alta`, chequean primero si ya hay registro con ese token y en ese caso lo DEVUELVEN sin crear nada. El chequeo va antes del lock a proposito: asi un reintento sigue devolviendo el movimiento aunque la caja ya se haya cerrado en el medio.
  - Carrera real (dos POST simultaneos que pasan el chequeo): `_create_movement_once` crea dentro de un savepoint y ante `IntegrityError` devuelve el registro que quedo grabado. En `transfer_between_boxes` no se usa savepoint porque graba tres registros: se re-chequea el token con las cajas ya bloqueadas, antes de escribir nada, para no dejar mitades. El token vive en el movimiento de SALIDA, que representa el envio.
  - `hx-sync="this:drop"` (descarta el segundo envio en vuelo) + `hx-disabled-elt="find button"` en los DOS partials de formulario, `templates/cashops/partials/form_card.html` y `templates/treasury/partials/form_card.html` (son templates distintos, no comparten nada). Es defensa de UI, no la garantia.
- Alcance excluido y por que: cierre de caja (guard natural, queda CERRADA y el segundo intento falla), transferencia entre sucursales (deshabilitada en el servicio), correcciones/anulaciones (guard por estado), forms de configuracion (unique propios y no mueven plata).
- Datos: migraciones `cashops/0024_movimientocaja_token_alta_and_more`, `treasury/0028_cuentaporpagar_token_alta_and_more`. Solo `AddField` (nullable, sin default: en Postgres no reescribe la tabla) + `AddConstraint` (indice unico parcial). Filas historicas quedan con `token_alta = NULL` y el indice parcial las excluye, asi que no puede fallar por datos existentes ni necesita backfill. Rollback conceptual: `RemoveConstraint` + `RemoveField`, sin perdida (el token es metadata del envio, no dato de negocio).
- Sin token el comportamiento es exactamente el historico (cada POST crea): la proteccion de servidor exige el hidden, y hay un test que lo deja explicito.
- Archivos: `cashops/models.py`, `cashops/forms.py`, `cashops/services.py`, `cashops/views.py`, `treasury/models.py`, `templates/cashops/partials/form_card.html`, 2 migraciones nuevas, `cashops/tests.py`, `context.md`.
- Tests: clase nueva `AltaIdempotenteTests`, 15 casos — reenvio no duplica en egreso/ingreso/venta general/venta tarjeta/traspaso/deuda; token nuevo SI crea el segundo (no hay falso positivo); sin token se mantiene el historico; reenvio despues de cerrar la caja devuelve el existente; vista con doble POST identico deja 1 movimiento y redirige las dos veces; el form renderiza el hidden y el `hx-sync`; la base rechaza dos movimientos con el mismo token.
- Pendiente 1: las altas PROPIAS de tesoreria (pagos a proveedor, movimientos bancarios, acreditaciones, caja central) siguen sin token: un reenvio ahi todavia puede duplicar a nivel servidor. Solo quedaron cubiertas por el guard de htmx. Slice aparte: mismo patron (campo + unique parcial + chequeo en el servicio) sobre `PagoProveedor`/`MovimientoBancario`/etc., y sus forms heredando una base equivalente a `AltaIdempotenteForm`.
- Pendiente 2 — HECHO en parte, mismo dia. Barrido en produccion: **12 grupos de deudas repetidas sobre 404 activas** y **22 pares de movimientos sobre 2297**. Confirma el diagnostico: casi todos a 0-1 segundos con ids consecutivos (uno es TRIPLE: 3 movimientos de $195.000 en un segundo, caja 470). El patron delata el camino del timeout y no el dedo torpe: caja 470 junto 6 duplicados en 17 minutos y caja 534 junto 5 en 10 minutos, o sea una red donde cada envio se sentia colgado. Ninguna de las 12 deudas estaba PAGADA, asi que no habia salido plata.
  - Deudas: **11 registros anulados** con `annul_box_originated_debt` (mismo servicio que la UI, auditoria intacta), $5.473.468,80 de deuda inexistente dada de baja. Se conservo la primera carga de cada grupo. Ids anulados: 34, 183, 184, 259, 337, 397, 430, 432, 436, 476, 479.
  - Quedan 2 grupos a revisar a mano, $145.975: id 443 (OSSOLA, +1h y de OTRA caja — 516 vs 518; el barrido no incluye la caja en la clave de agrupacion, asi que puede no ser duplicado) e id 30 (LA CAÑADA, +80s con un id en el medio: no es doble click sino una recarga humana).
  - **Pendiente real: los 22 movimientos de caja NO se anularon.** Al anular un egreso cambia el saldo esperado de cajas ya CERRADAS y validadas (las cargadas son 470, 534, 508, 536), asi que hay que ir por la UI caja por caja mirando como queda el arqueo, no disparar 22 anulaciones a ciegas. Ojo tambien con el ingreso duplicado `PANIFICACION $420.000` de caja 223: ese va para el otro lado, esa caja cerro con faltante de 420k.
  - Las deudas con referencia de comprobante nunca pudieron duplicarse (las frenaba `unique_payable_reference_by_supplier`): todos los duplicados encontrados son cargas sin referencia. Hacer obligatoria la referencia en el form de deuda cerraria tambien el camino humano.

### FOR UPDATE sobre outer join: eliminar deuda y movimiento estaban rotos en Postgres 2026-07-27

- Sintoma: al anular una deuda originada en caja, `psycopg.errors.FeatureNotSupported: FOR UPDATE cannot be applied to the nullable side of an outer join`. Apareció corriendo la limpieza de duplicados en produccion, pero **el boton "Eliminar deuda" de la UI estaba roto igual** (mismo servicio). Idem "Eliminar movimiento" y "Editar movimiento de caja cerrada".
- Causa raiz: tres servicios combinan `select_for_update()` con `select_related()` sobre una FK NULLABLE. Django emite ese select_related como LEFT OUTER JOIN, y Postgres rechaza un `FOR UPDATE` que abarque el lado nullable de un outer join. Las FK culpables: `MovimientoCaja.rubro_operativo` y `CuentaPorPagar.caja_origen` (verificado por script sobre `_meta`; las demas FK de los otros 10 `select_for_update` del repo son NOT NULL y estan bien).
- **Por que no lo agarro ningun test: SQLite ignora `FOR UPDATE` por completo** (`has_select_for_update = False`), asi que el compilador ni siquiera entra al bloque que arma el lock. Toda la familia de bugs "el lock no es valido en Postgres" es INVISIBLE en la suite local, incluida la validacion de los paths de `of=`. Es la segunda vez que un comportamiento solo-Postgres pasa el verde local.
- Opcion: OPTIMA. Limitar el alcance del lock con `of=`, que ademas es la semantica correcta (queremos bloquear la fila que vamos a modificar, no las que traemos por prefetch). No se toco ningun select_related: la data que llega a la vista es la misma.
  - `_validate_closed_box_movement_for_correction` y `_validate_box_movement_for_deletion`: `select_for_update(of=("self", "caja"))` — mantiene el lock del movimiento Y de su caja (que es lo que se valida y lo que se recalcula despues), y deja afuera solo `rubro_operativo`.
  - `annul_box_originated_debt`: `select_for_update(of=("self",))` — la caja ahi solo se lee (permiso y estado); lo unico que se modifica es la deuda.
- Verificado compilando el SQL con el backend de Postgres (sin servidor: `DATABASE_URL` apuntando a un host falso + `get_autocommit` mockeado, el compilador no necesita conexion). Antes: `FOR UPDATE` pelado con LEFT OUTER JOIN presente. Despues: `FOR UPDATE OF "cashops_movimientocaja", "cashops_caja"` y `FOR UPDATE OF "treasury_cuentaporpagar"`. Compilar tambien valida los paths del `of=`, cosa que en SQLite no pasa.
- Archivos: `cashops/services.py`, `cashops/tests.py`, `context.md`.
- Tests: clase nueva `PostgresRowLockingTests` (3 casos: anular deuda, anular movimiento, corregir movimiento de caja cerrada), con `@skipUnless(connection.vendor == "postgresql")` siguiendo el patron de `TreasuryConcurrencyTests`. **SE SALTEAN en local**: no hay forma de reproducir esto con SQLite. Suite completa sigue verde.
- Pendiente / trampa para el proximo agente: cualquier `select_for_update()` nuevo que venga con `select_related()` sobre una FK nullable va a fallar en produccion y pasar en local. Si se agrega uno, usar `of=` y verificar compilando contra Postgres como se hizo aca. Correr la suite contra Postgres alguna vez cerraria el agujero de raiz.


### El rechazo de validacion devuelve la caja al cajero 2026-08-02

- Pedido del usuario (viene de la queja de la administracion): al rechazar una validacion de efectivo, que la caja vuelva "abierta" al cajero para que corrija y vuelva a cerrarla; el aviso por ahora es manual (WhatsApp) y el sistema queda preparado para el futuro modulo de avisos (punto de extension comentado en `reject_box_cash`).
- Idea central (del usuario): la caja "no cierra definitivamente hasta que este completa la validacion". Eso evito el costo grande: NO hay segundo `CierreCaja` (es OneToOne) ni migracion estructural. El re-cierre ACTUALIZA el cierre existente (`close_box` ahora hace select_for_update + update o create; `cerrado_en` se re-estampa a mano porque auto_now_add solo aplica en insert). El detalle de cada intento queda en `CajaValidacion` (efectivo declarado + motivo del rechazo), asi que no se pierde historia.
- `reject_box_cash`: tras registrar el RECHAZO, anula con auditoria el `AJUSTE_CIERRE` del cierre rechazado (helper nuevo `_annul_closing_adjustment`; sin esto el saldo esperado de la caja reabierta arrancaba distorsionado), resuelve la alerta DIFERENCIA_GRAVE de ese cierre (el upsert por cierre la reactiva si el re-cierre vuelve a dar grave), y deja la caja `ABIERTA` + `RECHAZADA` (sigue fuera de TODOS los totales: `VALIDACION_BLOQUEA_TOTALES` no mira `estado`), con `cerrada_en/por` en NULL. Guards nuevos con mensaje humano: mes de tesoreria cerrado y otra caja abierta del mismo cajero/turno/sucursal/fecha (chocaria con `unique_open_box_by_user_turn_branch_date`).
- Helper nuevo `_treasury_month_is_closed_for_empresa(fecha, empresa_id)`: desde treasury 0034 el cierre mensual es POR EMPRESA, pero `treasury_month_is_closed` quedo global y sobre-bloquea cruzado (una empresa cierra su mes y frena a las demas: `open_box`, `update_box_metadata`, anulacion de boveda). Los guards nuevos usan el helper por empresa; **el viejo sigue global en sus 3 llamadores previos: BUG conocido pendiente de otro slice**.
- UI: estado nuevo "Devuelta por rechazo" (badge-danger) en `describe_box_follow_up`; banner con el motivo textual del rechazo en el dashboard del cajero (card Caja activa) y en el detalle de caja; `box_reject_view` avisa y redirige si la caja ya no esta cerrada-pendiente; el texto del form de rechazo explica que la caja vuelve al cajero.
- Datos: SIN migraciones. Las cajas CERRADA+RECHAZADA viejas de produccion quedan como estan; re-rechazarlas desde la cola las devuelve al cajero (el guard acepta RECHAZADA). Semantica conocida: una transferencia hacia una caja devuelta mete plata en una caja que no contabiliza hasta validarse (igual que antes del cierre).
- Tests: `RechazoDevuelveCajaTests` (8) + `test_reject_requires_motivo_and_returns_box_to_cashier` reescrito al flujo nuevo (rechazar -> corregir -> re-cerrar -> validar; ya no se puede validar directo una rechazada).

### Revertir la validacion del efectivo (US-02) 2026-08-02

- El agujero sin salida: validar empuja `saldo_fisico` a la boveda y `VALIDADA` era terminal; una validacion equivocada solo se arreglaba eliminando la caja entera.
- `revert_box_cash_validation(caja, motivo, actor)`: anula (estado ANULADO + motivo/autor/fecha, nada se borra) los `MovimientoCajaCentral` del push (`caja_cierre=caja`, tipos INGRESO_CAJA/AJUSTE_NEGATIVO, filtrando REGISTRADO) y devuelve la caja a `PENDIENTE` con `validada_por/en` en NULL. Evento `REVERSION` nuevo en `CajaValidacion` (la validacion original queda al lado).
- NO se reuso `annul_central_cash_movement` de treasury: rechaza justamente INGRESO_CAJA y movimientos con `caja_cierre` ("se anulan desde su origen") y exige TREASURY_MOV_DELETE. Este ES el origen. Se siguio el precedente de `_release_central_cash_movement_from_annulled_payment` (anulacion de sistema) re-implementando a proposito los guards que si aplican: mes cerrado POR EMPRESA y doble anulacion (filtro REGISTRADO).
- Guard de saldo (no existia en NINGUNA anulacion de boveda): si el efectivo del cierre ya se uso y la boveda quedaria negativa, corta con mensaje claro antes de escribir.
- La matriz completa mueve la plata UNA vez por diseño previo que ya lo soportaba: el guard del push filtra REGISTRADO (revalidar tras revertir re-empuja el monto vigente) y `_reverse_central_cash_closure_for_box` tambien (eliminar tras revertir no descuenta de nuevo). Testeada entera contra `saldo_actual` real.
- Permiso nuevo `cashops_val_undo` ("Revertir validacion de efectivo"), separado de validar porque revertir SACA plata (mismo criterio que TREASURY_MOV_DELETE). Sembrado lazy denegado salvo roles admin. Toques obligatorios del patron: choices en `users/models.py` + `PERMISSION_MODULE_META` en `users/views.py` (sin la entrada, la ficha de permisos revienta con KeyError) + wrapper `can_revert_validacion_efectivo`.
- UI: boton "Revertir validacion" en seguimiento y detalle (solo cajas CERRADA+VALIDADA con permiso), form de confirmacion con motivo (patron disable_htmx).
- Datos: migraciones `users/0018` y `cashops/0025`, solo AlterField de choices (no reescriben tablas ni tocan filas).
- Tests: `ReversionValidacionTests` (8).

### Corregir el efectivo declarado del cierre (US-03) 2026-08-02

- El caso "declaro 904.485 y habia 804.485": los movimientos estan bien, el conteo declarado no. `saldo_fisico` se cargaba una unica vez en el form de cierre y no habia correccion posible.
- `update_declared_closing_cash(caja, saldo_fisico, justificacion, motivo, actor)`: solo cajas CERRADAS con validacion PENDIENTE/RECHAZADA. Sobre VALIDADA se bloquea apuntando a revertir primero (US-02): ese declarado ya esta en la boveda. Rehace la matematica del cierre igual que `close_box`: anula el ajuste viejo (reusa `_annul_closing_adjustment`), recalcula diferencia contra el esperado vigente, ajuste nuevo o justificacion obligatoria si supera 10.000, alerta grave upsert/resuelta.
- No mueve plata: el push llega recien al validar y lleva el monto corregido (test lo fija).
- Bitacora: evento `CORRECCION` en `CajaValidacion` con "de $X a $Y" + motivo + autor. Permiso: el de correccion de cajas cerradas existente. UI: boton "Corregir declarado" en la cola de validacion.
- Datos: migracion `cashops/0026`, solo AlterField de choices.
- Tests: `CorreccionDeclaradoTests` (6).

### Editar movimientos en caja abierta con permiso propio (US-01) 2026-08-02

- La queja original de la administracion: "Editar" solo tocaba la cabecera de la caja y editar por movimiento solo existia en cajas CERRADAS; en una caja en curso la unica salida era eliminar y recargar. Decision de la administradora: ver y editar SEPARADOS (Ver composicion sigue de solo lectura) y permiso configurable por usuario o rol.
- Permiso nuevo `cashops_open_fix` ("Correccion de movimientos en caja abierta"). Reglas en `can_correct_movement_in_box(user, box)`: caja CERRADA -> permiso de cerradas de siempre; caja ABIERTA -> el permiso nuevo O el de cerradas (quien corrige lo contabilizado puede corregir lo que todavia no lo esta). Mismo patron que el borrado (`can_delete_movement_in_box`).
- `update_closed_box_movement` se generalizo a `update_box_movement` (el nombre viejo queda como ALIAS para llamadores/tests). Validador nuevo `_validate_box_movement_for_correction` (reemplaza a `_validate_closed_box_movement_for_correction`, referida en la entrada FOR UPDATE del 2026-07-27: el `of=("self","caja")` se conserva tal cual): decide el permiso sobre el estado YA lockeado (si la caja se cierra mientras se espera el lock, rigen las reglas de cerrada), y bifurca el recalculo como ya hacia el annul: CERRADA con cierre -> `_recalculate_closed_box_after_correction`; ABIERTA -> `resync_operational_control_for_caja` (el saldo esperado es property y se corrige solo). `is_closed_box_movement_correctable` -> `is_box_movement_correctable` (caja no ANULADA en vez de CERRADA; mismos 6 tipos bloqueados: apertura, 4 patas de transferencia, ajuste de cierre).
- Cambio de comportamiento deliberado: alguien con permiso de cerradas ahora puede editar movimientos de cajas ABIERTAS (antes: ValidationError "Solo se pueden corregir movimientos de cajas cerradas"). Ningun test fijaba lo contrario.
- UI: el boton Editar por movimiento aparece en el detalle tambien para cajas abiertas (flag `movement.can_edit`, antes `can_fix_closed_box`); textos del form/vista neutralizados (ya no dicen "caja cerrada"). La URL conserva el name `closed_box_movement_edit` para no romper nada; renombrarla es cosmetico y queda para otro slice.
- Datos: migracion `users/0019`, solo AlterField de choices.
- Tests: `EdicionMovimientoCajaAbiertaTests` (8: separacion de permisos en ambos sentidos, tipos estructurales bloqueados, editar-y-cerrar deja la matematica bien, flujo de vista completo, composicion sigue sin acciones).

### Pendientes que dejo esta tanda 2026-08-02

- ~~`treasury_month_is_closed` global vs cierre mensual por empresa~~: RESUELTO el 2026-08-02 (commit 5ab8a89, ver entrada propia mas abajo).
- ~~`token_alta` para las altas de treasury~~: RESUELTO el 2026-08-02 (ver entrada propia mas abajo).
- Modulo de avisos (propuesta comercial ya enviada): el punto de insercion esta comentado en `reject_box_cash`.
- 22 movimientos de caja duplicados en produccion sin anular (se hacen por UI) y 2 deudas a revisar a mano (OSSOLA 443, LA CANADA 30).
- Carrera residual del cierre mensual: el mutex de Empresa cubre revert/annul_box vs close_treasury_month, pero validate_box_cash no toma ese lock (su ventana es benigna: el chequeo de cajas pendientes del cierre corta antes). Si algun dia se suma otro flujo que saque plata del mes, tiene que tomar el mismo lock de Empresa.

### Revision adversarial de la tanda: 7 hallazgos confirmados, 6 cerrados 2026-08-02

Antes de mandar la tanda a staging se corrio una revision multi-agente sobre el diff
(buscadores por dimension: plata, estados, permisos, Postgres; cada hallazgo verificado
por un segundo agente intentando refutarlo). Confirmados 7. Cerrados en el mismo dia 6:

1. **ALTA / plata — revertir una validacion anterior al 14/07/2026 no anulaba nada y
   revalidar DUPLICABA la plata en la boveda.** `caja_cierre` nacio en treasury/0025 sin
   backfill: los empujes viejos solo se identifican por concepto. `revert_box_cash_validation`
   ahora usa el matcher doble de `_reverse_central_cash_closure_for_box`
   (`Q(caja_cierre=caja) | Q(concepto__in=[...])`) y ademas CORTA con error si no encuentra
   empuje y el cierre declaro plata (revertir "en el aire" era lo que armaba el doble push).
   Test: `test_revert_matches_legacy_push_by_concept`, `test_revert_blocks_when_push_is_missing`.
2. **media / plata — el guard de mes cerrado del revert miraba `fecha_operativa`, pero el
   empuje puede vivir en OTRO mes** (el push re-fecha si el mes figura cerrado, y su chequeo
   es global). Ahora el guard sigue el mes donde vive CADA empuje (por empresa); sin empujes,
   rige la fecha operativa. Test: `test_month_guard_follows_the_push_month_not_the_operative_date`.
3. **media / Postgres — el guard "la boveda no queda en negativo" era check-then-act sin
   lock**: dos reversiones concurrentes contra la misma boveda pasaban las dos (write skew,
   invisible en SQLite). Ahora el saldo se chequea con la fila de `CajaCentral` bloqueada
   (`select_for_update(of=("self",))`), delta agrupado por boveda.
4. **media / estados — la reapertura por rechazo corria una carrera con `open_box`** que
   terminaba en IntegrityError crudo (500) para el validador. `reject_box_cash` ahora toma
   el mismo lock de `Turno` que `open_box` antes del chequeo de slot, y `box_reject_view`
   ademas captura IntegrityError con el mismo texto humano del guard.
5. **media / permisos — `box_validation_undo_view` y `box_declared_cash_edit_view` eran las
   unicas vistas del archivo sin `@login_required` / `@require_http_methods`.** Sesion vencida
   daba 403 con mensaje falso en vez de redirigir al login. Decoradores agregados; test de
   acceso anonimo (`test_new_money_views_redirect_anonymous_to_login`).
6. **media / permisos — corregir el declarado no aislaba por duenio**: un no-admin con
   `cashops_closed_fix` podia corregir el declarado de cajas AJENAS por URL directa (los
   flujos hermanos del mismo permiso lo niegan). Ahora pasa por `_get_box_for_request` como
   todos. Test: `test_declared_edit_isolated_to_own_boxes_for_non_admin`.

**Pendiente (confirmado, NO arreglado en esta tanda):**

7. **media / plata — carrera entre revertir una validacion y `close_treasury_month`**: ninguno
   toma un lock que el otro respete; en Postgres READ COMMITTED el mes puede congelar plata
   que la reversion concurrente acaba de anular (y la revalidacion posterior la cuenta dos
   veces). El fix correcto es lockear el cierre mensual por empresa en treasury y que los
   guards nuevos lean ese lock: slice propio de treasury, junto con la migracion de los
   llamadores globales de `treasury_month_is_closed` ya anotada en pendientes. Mientras
   tanto el riesgo real es bajo (dos acciones de administracion en el mismo segundo).

Trampa que dejo la revision para el proximo agente: el re-fechado del push
(`_push_box_closure_to_central_cash`) sigue decidiendo con el chequeo GLOBAL de mes
cerrado — si otra empresa cerro el mes, el efectivo se atribuye al mes siguiente de la
cadena PROPIA aunque este abierta. Es parte del mismo slice pendiente por empresa.

### El mes cerrado es por empresa de verdad + mutex del cierre mensual 2026-08-02

- Sintoma: el cierre mensual ES por empresa desde treasury/0034, pero los guards
  seguian consultando global. Cuando UNA empresa cerraba su mes: la otra no podia
  abrir cajas de ese mes (open_box), ni moverles la fecha (update_box_metadata),
  la anulacion de boveda se le bloqueaba, y el push del cierre re-fechaba su
  efectivo al mes siguiente de su PROPIA cadena aunque estuviera abierta (foto
  mensual corta, mes siguiente inflado). Ademas close_treasury_month no tomaba
  ningun lock: en Postgres podia congelar el snapshot mientras una reversion de
  validacion le sacaba plata al mes en paralelo (hallazgo 7 de la revision
  adversarial, READ COMMITTED write skew; SQLite lo esconde).
- Opcion: OPTIMA. Todos los guards pasan por el chequeo por empresa
  (`_treasury_month_is_closed_for_empresa` en cashops; `_mes_de_tesoreria_cerrado`
  gana `empresa_id` en treasury). Fila de cierre legacy sin empresa bloquea a
  todos (no se sabe de quien es la foto). Se elimino el helper global
  `treasury_month_is_closed` (sin llamadores). Mutex nuevo: close_treasury_month,
  revert_box_cash_validation y annul_box toman `select_for_update` sobre la fila
  de Empresa; orden de locks Caja -> Empresa, nadie lockea al reves.
- Sin migraciones ni impacto de datos: los cierres mensuales guardados valen igual.
- Tests: `MesCerradoPorEmpresaTests` (cashops), test por empresa en
  `AnulacionBovedaTests` (treasury), y el test del guard de reversion
  reconstruido sobre el camino legacy real. El mutex en si NO es testeable en
  SQLite (lock ignorado): queda anotado para la clase guardian de Postgres.
- Commit: 5ab8a89.

### Token de alta para las altas de plata de treasury 2026-08-02

- Cierra la deuda anotada al crear el token en cashops: las altas de treasury
  seguian sin proteccion contra el doble submit (doble click, reintento tras
  timeout, volver atras y reenviar), y con las features nuevas habia MAS flujos
  que cuando se anoto.
- Mismo contrato de 3 capas que cashops: short-circuit en el servicio ANTES de
  cualquier lock, savepoint sobre la carrera, constraint parcial unica en la
  base con mensaje humano (`violation_error_message`).
- Modelos con `token_alta` + constraint (migracion treasury/0035, AddField
  nullable + constraint parcial: sin reescritura de tabla, los registros
  historicos quedan con NULL y fuera de la constraint):
  - `PagoTesoreria` -> "Este pago ya fue registrado."
  - `MovimientoBancario` -> "Este movimiento bancario ya fue registrado."
  - `MovimientoCajaCentral` -> "Este movimiento de caja fuerte ya fue registrado."
- Servicios cubiertos: register_payment (punto unico de TODOS los pagos:
  transferencia, cheque, echeq, efectivo), el lote de proveedor (el token viaja
  en el PRIMER pago; el reenvio devuelve ese pago y no paga de nuevo),
  create_bank_movement, register_central_cash_movement, carga inicial y
  register_egreso_tesoreria (el reenvio se busca en boveda Y banco porque el
  egreso puede terminar en cualquiera de los dos).
- Forms: `AltaIdempotenteMixin` en treasury/forms.py (campo dinamico, sirve en
  Form y ModelForm; el partial de treasury ya renderizaba hidden_fields, asi que
  no hubo que tocar templates). Rebasados los 7 forms de plata.
  `update_bank_movement` acepta y IGNORA token_alta porque la vista de edicion
  comparte el form de alta y pasa **cleaned_data.
- Flujos que NO llevan token, a proposito:
  - "Pagar desde extracto" (paso final): ya es idempotente por el vinculo
    OneToOne movimiento->pago ("Este movimiento ya esta vinculado a un pago").
  - Acreditaciones de tarjeta: ya tienen dedupe semantico propio
    (`_existing_accreditation_duplicate_qs` corta el duplicado equivalente).
- Tests: `treasury/tests_token_alta.py` (9 casos: reenvio por cada flujo, token
  nuevo crea el segundo, lote no repaga, constraint en la base con mensaje
  humano, el form renderiza el token oculto).
- Trampa conocida: el save de PagoTesoreria pasa por full_clean, asi que el
  duplicado en carrera sale como ValidationError (validate_unique) ademas de
  IntegrityError: los savepoints atrapan las DOS.

### Agrupacion de rubros en la lectura economica (US-11.11) 2026-08-03

- Pedido de la administradora (audios): el listado de abajo del dashboard
  economico tiene demasiadas filas. Quiere juntar todo lo que es mercaderia en un
  solo item `MATERIA PRIMA` (cerveza, alcohol, coca cola, fiambres, almacen, pan,
  cafe, verdura, carne, pollo, pescado, cadeteria, limpieza, descartable) y que
  al abrirlo aparezca el desglose. El resto de los rubros quedan como estan,
  porque quiere seguir entrando a ver que se imputa.
- Aclaracion de alcance: la lista esta SOLO en "Situacion economica y
  rentabilidad". "Situacion financiera por periodo" no tiene lista de rubros.
- Opcion: OPTIMA con modelo nuevo `cashops.GrupoRubro` (nombre, activo) + FK
  nullable `RubroOperativo.grupo`. NO se uso rubro padre (self FK) a proposito:
  un rubro padre apareceria en todos los selectores de rubro y se le podria
  imputar un gasto directo, y ahi el total del grupo dejaria de ser la suma de
  sus hijos. Con modelo aparte es imposible por construccion: el grupo es solo
  nivel de lectura, la plata siempre cae en un rubro.
- `build_economic_period_snapshot` ahora devuelve DOS listas: `rubro_items` (la
  plana por rubro, sin cambios) e `items` (lo que se muestra, con los agrupados
  colapsados por `_collapse_economic_items_by_group`). Todos los totales de la
  cabecera y `objective_items_count` se calculan sobre la plana, asi agrupar no
  puede mover ningun numero de arriba. Test que lo fija comparando 9 claves antes
  y despues de agrupar.
- Desvio del grupo: se mide SOLO sobre los rubros con objetivo vigente (igual que
  `objective_scope_real_total` de la cabecera) y la fila lleva
  `objective_children_count` / `children_count` / `objective_covers_all_children`
  para avisar en pantalla cuando el objetivo no cubre todo el grupo. Sin ese
  aviso, un verde sobre 3 de 14 rubros se leeria como si cubriera el grupo.
- Bug propio evitado: `economic_rubro_detail` buscaba su cabecera en `items`; un
  rubro agrupado ya no tiene fila propia ahi y la cabecera quedaba vacia. Pasa a
  buscar en `rubro_items`. Ademas el "volver" del rubro agrupado vuelve al
  desglose del grupo, no al dashboard.
- Grupo desactivado no agrupa: sus rubros vuelven al listado sueltos sin perder
  importes (`RubroOperativo.grupo_de_lectura`). El form de rubro deja elegible un
  grupo desactivado si ya estaba asignado, para no sacarlo sin querer al guardar.
- Config nueva en cashops: `rubro_group_list/create/update/toggle`
  (`/cajas/grupos-de-rubros/`), link en el menu Config y en la pantalla de
  rubros; el form de rubro gano el selector "Grupo de lectura". Alta y baja de
  grupos las hace ella sola, sin dev.
- Migracion `cashops/0027`: CreateModel + AddField nullable + AddIndex. Sin
  backfill y sin reescritura de tabla. Mientras no exista ningun grupo, la
  pantalla se ve identica a antes (test que lo fija).
- Tests: `treasury/tests_grupos_rubros.py` (12 casos: baseline sin grupos, suma
  exacta, cabecera intacta, grupo desactivado, objetivo parcial y completo,
  reconciliacion fila vs desglose, y las 4 de vistas incluida la composicion del
  rubro agrupado). Suite completa 540 OK, 4 skipped.
- PENDIENTE que bloquea el objetivo por grupo (lo pidio: "un general para toda la
  materia prima, y tambien uno por cada rubro"): el objetivo se mide contra las
  ventas imputadas AL MISMO RUBRO (`sales_by_rubro_month`), no contra las ventas
  del periodo. Un rubro de gasto puro no recibe ventas, asi que su porcentaje
  nunca compara y queda "Sin objetivo" para siempre. Fijado en el test
  `test_un_objetivo_sobre_un_rubro_sin_ventas_propias_no_compara_nada`. Antes de
  agregar objetivo por grupo hay que decidir la base: ventas totales del periodo
  (lo que ella entiende por "35% de las ventas") vs ventas del propio rubro (lo
  que hace hoy el codigo).

### Una transferencia repartida entre varias facturas (US-4.10) 2026-08-03

- Pedido de la administradora: el pago semanal de cuenta corriente sale como UNA
  transferencia que cubre 6 facturas de proveedores distintos. Pidio ademas que en
  el desplegable figuren TODOS los proveedores (no solo los que la transferencia
  alcanzaba a pagar enteros) y un filtro por proveedor.
- Diagnostico: los tres pedidos eran UNO. El desplegable filtraba por
  `saldo_pendiente >= movimiento.monto` justamente porque una transferencia pagaba
  una sola factura por su importe exacto. Sacar el filtro sin resolver el reparto
  habria sido peor: elegir una factura de $200.000 y pagarle $1.209.905,08.
- Vuelta de la relacion: `MovimientoBancario.pago_tesoreria` (OneToOne) pasa a
  `PagoTesoreria.movimiento_bancario` (FK, related_name="pagos"). El vinculo tiene
  que vivir del lado del pago porque son N pagos por movimiento. Detalle que
  abarato todo: el accessor inverso YA se llamaba `movimiento_bancario`, asi que
  las 8 lecturas `pago.movimiento_bancario` no se tocaron; solo cambiaron los 16
  puntos que escribian o preguntaban desde el lado del movimiento.
- Migracion `treasury/0036` escrita A MANO para fijar el orden: AlterField (libera
  el nombre del accessor) -> AddField -> RunPython (copia los vinculos) ->
  RemoveField. Asi ningun estado intermedio tiene dos cosas llamadas
  `movimiento_bancario`. Reversible: la vuelta atras conserva UN pago por
  movimiento (era OneToOne) tomando el mas viejo.
- Impacto de datos: cada vinculo existente se copia uno a uno. Verificado con
  `treasury/tests_migracion_vinculo_pago.py`, que vuelve a 0035 con
  MigrationExecutor, crea filas vinculadas con los modelos de ESE estado y migra
  hacia adelante (3 casos: el vinculo llega, un debito sin pago no se inventa uno,
  y la vuelta atras lo devuelve). En Postgres AddField nullable y DropColumn son
  metadata: sin reescritura de tabla.
- Reglas que se movieron de sitio: las validaciones de `MovimientoBancario.clean()`
  que comparaban contra EL pago (clase segun medio de pago, misma cuenta, pago
  REGISTRADO, proveedor y categoria de la deuda) pasaron a
  `link_payment_to_bank_movement`, que es el unico lugar que crea el vinculo. Ya no
  podian vivir en clean(): los pagos se asocian DESPUES de guardar el movimiento.
  Se perdio como invariante de modelo "origen PAGO_TESORERIA implica pago
  vinculado"; queda garantizado por el servicio.
- Con proveedores distintos el movimiento NO se queda con el proveedor de la
  primera factura (seria mentir): `proveedor` y `categoria` quedan en NULL y los
  proveedores se leen de los pagos. Para eso se relajo el clean(): la clase
  TRANSFERENCIA_TERCEROS con origen PAGO_TESORERIA no exige proveedor. Cheque y
  ECHEQ lo siguen exigiendo, porque tienen un solo beneficiario. El
  rubro/sucursal/periodo heredados son los de la PRIMERA factura (clean los exige)
  y no afectan ningun total: los debitos con origen PAGO_TESORERIA quedan fuera del
  gasto economico, porque el costo ya lo conto la deuda.
- Concurrencia: `link_payment_to_bank_movement` y `pay_debts_from_bank_movement`
  bloquean la fila del movimiento (`select_for_update(of=("self",))`) ANTES de
  sumar lo ya asignado. Sin el lock, dos vinculaciones simultaneas leen el mismo
  "queda por asignar", las dos pasan y juntas se pasan del importe (write skew bajo
  READ COMMITTED). Compilado contra el backend de Postgres sin servidor: FOR UPDATE
  OF valido, sin LEFT OUTER JOIN. SQLite ignora el lock, asi que ese caso no se
  puede testear local.
- El reparto es todo o nada (`@transaction.atomic` + validacion de la suma antes de
  crear): media transferencia repartida es peor que ninguna, porque despues no se
  sabe que parte falto.
- Anular UNO de los pagos libera solo su importe: el movimiento sigue REGISTRADO y
  con origen PAGO_TESORERIA mientras le queden otros pagos vivos. Solo al anular el
  ultimo vuelve a MANUAL (y se anula si lo habia generado el sistema, como antes).
- Pantalla nueva `treasury/pay_debts_split.html`: todas las facturas impagas con
  checkbox e importe por fila, filtro por proveedor, y en la cabecera importe de la
  transferencia / ya asignado / queda por asignar. Lo que sobra queda sin asignar y
  se puede usar despues; el detalle del movimiento lo muestra y ofrece "Asignar el
  resto a otra deuda".
- De paso: el formato de plata estaba duplicado en `views._money` y en el filtro
  `money`. Ahora hay UNA implementacion (`services.formato_money`) y las otras dos
  delegan, porque los servicios tambien arman mensajes con importes.
- Tests: 20 nuevos (12 de servicio en `tests_pago_desde_banco.py` + 3 de vista + 3
  de migracion + 2 reescritos). Se REESCRIBIO
  `test_el_paso_uno_ofrece_solo_proveedores_con_facturas_alcanzables`: fijaba
  justamente el comportamiento que ella pidio cambiar. Suite completa 564 OK, 4
  skipped.

## Modo claro / modo oscuro (preferencia por computadora)

- Antes habia UN solo tema (oscuro) definido TRES veces: el `:root` de
  `static/css/gerayse.css` (lo cargan landing, login, primer ingreso y cambio de
  password obligatorio) y sendos `:root` inline, byte-identicos entre si, en
  `templates/cashops/layout.html` y `templates/treasury/layout.html` (22
  templates cada uno). Ahora la paleta vive SOLO en
  `templates/partials/theme_tokens.html`, que incluyen las tres shells. Los
  nombres historicos de gerayse.css (`--paper`, `--ink`, `--surface`,
  `--danger-bg`, `--accent-strong`, `--ok`, `--warning`) quedaron como alias
  `var()` de los canonicos, asi que esa hoja no se toco componente por
  componente.
- La preferencia se guarda en `localStorage` (clave `gerayse-theme`): queda atada
  al NAVEGADOR de esa computadora, no al usuario. Es lo pedido: la misma persona
  puede querer claro en el mostrador y oscuro en administracion. No hay campo en
  el modelo ni migracion. Si nunca eligio, manda `prefers-color-scheme` del
  sistema operativo, y se lo sigue en vivo hasta el primer click.
- `templates/partials/theme_boot.html` es un `<script>` inline y BLOQUEANTE al
  principio del `<head>`, antes de cualquier estilo. Tiene que quedar ahi: si el
  atributo `data-theme` se pusiera mas tarde, el navegador ya pinto el primer
  frame con el tema equivocado y se ve un flash blanco al entrar. Hay un test que
  fija ese orden.
- Tokens NUEVOS, todos con valor en los dos temas: `--on-accent` (tinta sobre el
  relleno de marca), `--on-danger`, `--field-bg`, `--selection-bg`,
  `--selection-ink`, `--button-shadow`, `--shell-glow`, `--text-soft`.
  `--on-accent` SE INVIERTE entre temas y no es un error: en oscuro el relleno es
  verde vivo y la tinta casi negra; en claro el relleno es verde ingles profundo
  y la tinta es papel. `--nb-light` es su alias historico y el nombre miente
  (nunca significo "claro", significaba "tinta sobre verde").
- Los tres colores de estado del tema CLARO estan ordenados por luminancia a
  proposito (ok 0.136 > warn 0.101 > danger 0.082). Un primer armado los tenia en
  luminancia casi identica (ok #146c43 vs danger #b3261e, 1.01:1 entre si): con
  daltonismo rojo-verde se pierde el tono y exito y peligro quedan indistinguibles,
  que en pantallas que pintan importes por signo es riesgo operativo, no estetica.
  Quedaron #15764a / #7d5006 / #9e1c15, con ok-vs-danger en 1.41:1.
- El tema OSCURO no se toco: los alias resuelven exactamente a los mismos hex de
  antes (verificado en el navegador). Lo unico que cambio en oscuro es que
  `-webkit-font-smoothing: antialiased` quedo acotado a
  `:root[data-theme="dark"] body` (adelgaza el trazo y deja anemica la tinta
  oscura sobre papel).
- Colores literales que SI rompian en claro y se tokenizaron: `.input/.select/
  .textarea { background: #0e1315 }` en los dos layouts y en gerayse.css (dejaba
  todos los campos negros con tinta oscura encima, era el peor), `color: #08130d`
  y `color: #fff` de los botones, `::selection`, el `style="color:#dd9598"` inline
  de "Reiniciar datos" (paso a `.nav-menu__item--danger`), y los `rgba(184,90,96,X)`
  de las zonas de peligro de `list_page.html` y `reset_confirm.html`.
- Bugs que YA estaban rotos en oscuro y se arreglaron de paso porque eran del
  mismo renglon: `disponibilidades_report.html` tenia una `.card` con
  `background: rgba(255,255,255,0.7)` inline (tarjeta casi blanca con texto
  off-white encima, sobre el shell grafito) y un `color: rgba(255,255,255,0.85)
  !important`; la barra de distribucion usaba dos verdes a un paso
  (`--accent` y `--forest-deep`), ahora efectivo vs banco se separan por tono
  (`--info`). Ademas `treasury/layout.html` nunca tuvo la regla de geometria de
  `.nav-divider` aunque usa la clase dos veces: sus separadores eran spans
  invisibles y ahora se ven.
- Botones destructivos: `style="background:var(--danger)"` sobre `.button` no
  alcanza, porque `.button` fija la tinta para el verde. Se paso a
  `class="button button-danger"` en `reset_confirm.html` (x2), `list_page.html` y
  `disponibilidades_report.html`.
- PENDIENTE CONOCIDO (no se toco, es preexistente): `.button-danger` en OSCURO es
  blanco sobre `#e2635c` = 3.40:1, por debajo del 4.5:1 de AA. Se arregla con un
  solo token (`--on-danger` oscuro a una tinta oscura tipo `#2a1615` da 6.9:1),
  pero cambia el aspecto del tema oscuro actual, asi que va en su propio slice. En
  el tema claro ese mismo boton da 7.98:1. Misma familia: en oscuro `--success` y
  `--warn` tienen luminancia casi identica (1.01:1).
- PENDIENTE CONOCIDO: los dos layouts siguen con ~300 lineas de CSS de componentes
  duplicadas. NO se unificaron en este slice porque no son duplicados exactos: hay
  37 diferencias reales y varias son decisiones de diseño, no valores (`.card h2`
  22.5px en cashops vs 16.8px en treasury; `label` normal vs chico-mayusculas;
  `.messages` toast flotante que se autodestruye a los 5s en cashops vs banner
  inline en treasury; `.action-grid` minmax 220px vs 140px). Ojo al unificar: en
  los dos archivos el `@media (max-width:640px)` esta ANTES del bloque
  "Prolijidad", que lo pisa, asi que HOY el padding mobile de `.shell` y `.card`
  no se aplica; ordenar "prolijo" lo activaria y cambiaria el mobile de las dos
  apps sin que nadie lo pida.
- PENDIENTE CONOCIDO: `treasury/reconciliation_page.html` esta escrita con
  utilidades de Bootstrap (`btn btn-primary`, `progress`, `bg-dark text-white`,
  `text-success`, `card-header bg-white`) y Bootstrap no se carga en ningun lado;
  `disponibilidades_report.html` tiene restos de Bulma (`has-text-success`).
  Hoy son no-ops, o sea que esa pantalla ya se ve plana. No entra en este slice
  porque arreglarlo es reescribir markup, no tematizar.
- Archivos: `templates/partials/theme_tokens.html`, `theme_boot.html`,
  `theme_toggle.html` y `static/js/theme.js` (nuevos); `templates/base.html`,
  `cashops/layout.html`, `treasury/layout.html`, `cashops/list_page.html`,
  `cashops/reset_confirm.html`, `treasury/disponibilidades_report.html`,
  `static/css/gerayse.css` (modificados).
- Tests: `core/tests_theme.py`, 7 nuevos. Fijan que las tres shells traigan el
  tema, que el arranque vaya antes de los estilos, que los dos temas definan
  EXACTAMENTE el mismo set de tokens (agregar un color a uno solo no rompe nada
  visible en el momento y aparece meses despues), que `--on-accent` sea distinto
  en cada tema, y que no vuelvan colores literales a las tres hojas. Suite
  completa 577 OK, 4 skipped. `makemigrations --check`: sin cambios (no toca
  modelos).

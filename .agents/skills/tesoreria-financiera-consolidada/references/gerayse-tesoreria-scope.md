# Gerayse Tesoreria Scope

Usar esta referencia cuando el pedido toque tesoreria, banco o disponibilidades.

## 1. Fuentes primarias

- `docs/epics/EP-03-tesoreria-central.md`
- `docs/epics/EP-04-bancos-y-conciliacion.md`
- `docs/epics/EP-05-flujo-de-disponibilidades.md`
- `docs/epics/EP-10-situacion-financiera-y-alertas-consolidadas.md`
- `treasury/models.py`
- `treasury/services.py`
- `treasury/views.py`
- `treasury/tests.py`

## 2. Fuentes de verdad del producto

- estado de deuda: `CuentaPorPagar`
- pago administrativo: `PagoTesoreria`
- reflejo bancario: `MovimientoBancario`
- acreditacion tarjeta: `AcreditacionTarjeta`
- descuentos de acreditacion: `DescuentoAcreditacion`
- efectivo central: `MovimientoCajaCentral`

## 3. Lecturas que no hay que mezclar

- venta digital
- lote POS
- acreditacion bancaria
- movimiento bancario manual
- pago de tesoreria
- disponibilidad consolidada

## 4. Matriz de origen rapido

- deuda pendiente o cancelada: reconstruir desde `CuentaPorPagar` y pagos validos
- movimiento de caja fuerte: `MovimientoCajaCentral`
- saldo bancario registrado: `MovimientoBancario`
- pendiente de acreditacion: relacion entre ventas digitales y acreditaciones registradas
- total consolidado: suma explicable de efectivo central y banco

## 5. Formulas sensibles

- pendiente de acreditacion:
  - ventas digitales del periodo menos acreditaciones registradas del periodo
  - aclarar si la lectura es neta o bruta
- disponibilidad consolidada:
  - efectivo central + banco
  - aclarar fecha de referencia
- vencimientos:
  - separar vencido, vence hoy y proximos dias

## 6. Estado actual de EP-10

- hecho:
  - dashboard financiero por periodo y sucursal
  - disponibilidades totales visibles
  - buckets de vencimiento
  - lectura de acreditaciones pendientes
- pendiente:
  - taxonomia dura de movimientos bancarios
  - carga agrupada de acreditaciones por periodo

## 7. Preguntas obligatorias

- el numero es saldo o movimiento
- el alcance es por cuenta, por sucursal o global
- la fecha relevante es operativa, de acreditacion o de impacto bancario
- el importe es bruto, neto o neto de descuentos
- el concepto debe impactar deuda, disponibilidad o ambos

## 8. Riesgos tipicos

- doble conteo entre venta y acreditacion
- dashboards que mezclan saldo acumulado y flujo del periodo
- estados bancarios inconsistentes con enums reales
- filtros que usan propiedades no ORM
- pagos en efectivo que exigen cuenta bancaria

## 9. Stop-ship funcional

- deuda recalculada desde fuente equivocada
- acreditacion cargada sin forma de vincularla o auditarla
- taxonomia bancaria que no diferencia ingreso, egreso y ajuste
- dashboard consolidado sin aclarar fecha de corte

## 10. Reglas de negocio confirmadas por cliente (2026-07-03)

Empresas y cuentas bancarias:

- Hay dos empresas: `MAPOGO SRL` y `ARMADI SRL`. No comparten cuentas bancarias.
- `MAPOGO SRL` tiene una sola sucursal: `Vivre`. Su cuenta de banco es de esa empresa.
- `ARMADI SRL` tiene varias sucursales; TODAS estan sincronizadas a una unica cuenta bancaria.
  Por lo tanto la cuenta de ARMADI es a nivel EMPRESA (comun a todos sus locales), no de una
  sucursal puntual.
- Consecuencia de modelado: la cuenta bancaria debe poder pertenecer a una EMPRESA, no solo a
  una sucursal. Scopear cuentas y movimientos por empresa duena de la cuenta.
- Solo se usan esos dos bancos; no se esperan cuentas compartidas entre empresas.

Imputacion de gastos bancarios:

- Regla del negocio: TODO gasto (impuestos AFIP/IVA, comisiones bancarias, transferencias a
  terceros/proveedores) debe imputarse POR SUCURSAL. "Todo afecta la rentabilidad del negocio."
- El cliente pondera los impuestos por volumen de venta y los carga ya desglosados por sucursal.
  Las transferencias se cargan por sucursal + rubro + proveedor.
- Por lo tanto un `MovimientoBancario` DEBITO (egreso) sin `sucursal_gasto` es un HUECO de carga,
  no un "gasto comun". No existe el gasto bancario global por diseno.

Efecto de un egreso sin `sucursal_gasto` (confirmado en `build_economic_period_snapshot`):

- Los egresos de tesoreria (banco y caja central) SOLO entran a `treasury_expense_total` y a
  `treasury_expense_by_rubro` (renglon "Gasto tesoreria" de Situacion economica) si tienen
  `rubro_operativo` + `sucursal_gasto` + `periodo_pago`.
- Si falta cualquiera de los tres, caen en `treasury_unmapped_expenses_total/_count`
  (renglon "Gasto sin imputar"), y NO impactan la rentabilidad economica por rubro/sucursal
  hasta completarse. Si impactan el saldo bancario/disponibilidad (la plata igual salio).

Creditos/otros: `MovimientoBancario` CREDITO y `MovimientoCajaCentral` de ingreso/aporte/ajuste
sin sucursal son plata comun por diseno y no requieren imputacion.

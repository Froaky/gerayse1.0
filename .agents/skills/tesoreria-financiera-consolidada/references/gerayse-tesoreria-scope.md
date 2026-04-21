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

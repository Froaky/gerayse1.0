---
name: tesoreria-financiera-consolidada
description: model and implement treasury and bank workflows for internal-control financial reading, including bank movement taxonomy, accreditations, pending accreditations, consolidated availability, and financial dashboards. use when working on `treasury/`, EP-03, EP-04, EP-05, EP-10, or any request about movements, banks, treasury visibility, or financial alerts.
---

Tratar tesoreria como control financiero auditable, no como integracion bancaria real salvo pedido explicito.

Siempre:
1. Leer `context.md`, `references/gerayse-tesoreria-scope.md` y las epicas relevantes antes de tocar codigo.
2. Respetar reglas confirmadas del repo:
   - `CuentaPorPagar` es la fuente del estado de deuda
   - pagos por dominio y servicios, no `save()` directo
   - pagos en efectivo van por caja central, no banco
3. Separar venta digital, lote POS, acreditacion bancaria, movimiento bancario, pago de tesoreria y disponibilidad consolidada.
4. Tipificar ingresos y egresos bancarios con categorias que soporten lectura y auditoria.
5. Si no hay integracion real, preferir estructuras internas robustas, filtros claros y formulas explicables.
6. Cualquier alerta o dashboard debe poder explicarse desde movimientos, acreditaciones y deudas persistidas.

## Contrato del agente

Este skill protege que un cambio financiero no:
- mezcle hechos distintos como si fueran el mismo dinero disponible
- use una fuente de verdad incorrecta para deuda o saldo
- duplique importes entre efectivo, banco y consolidado
- publique dashboards imposibles de explicar

## Flujo de trabajo

### 1. Clasificar el pedido financiero

Identificar si toca:
- deuda y pagos
- movimientos bancarios
- acreditaciones y descuentos
- disponibilidades
- dashboard o alertas
- lectura por sucursal o periodo

### 2. Elegir el hecho fuente

Decidir si el dato nace en:
- `CuentaPorPagar`
- `PagoTesoreria`
- `MovimientoBancario`
- `AcreditacionTarjeta`
- `LotePOS`
- `MovimientoCajaCentral`

Evitar duplicar un mismo hecho en dos modelos sin relacion explicita.

### 3. Fijar semantica antes de tocar codigo

Responder por cada numero:
- es saldo o flujo del periodo
- es neto o bruto
- es efectivo, banco o consolidado
- es por cuenta, por sucursal o global
- que deduce y que no deduce
- desde que fecha y hasta que fecha aplica

### 4. Aplicar matriz de origen y lectura

- deuda viva: `CuentaPorPagar`
- pago registrado: `PagoTesoreria`
- impacto o reflejo bancario: `MovimientoBancario`
- acreditacion de venta digital: `AcreditacionTarjeta`
- efectivo central: `MovimientoCajaCentral`
- disponibilidad consolidada: derivada desde efectivo y banco, nunca editada a mano

### 5. Implementar lectura consolidada

- Mantener vistas separadas para efectivo, banco y consolidado cuando ayude a explicar saldos.
- Para acreditaciones pendientes, documentar formula y alcance temporal.
- Si hay costos de servicio o impuestos deducibles, mostrarlos como concepto separado o aclaracion.
- Si se agregan taxonomias, asegurar que soporten filtro, agregado y auditoria.

### 6. Cerrar con pruebas financieras

- Probar estado de deuda despues de pagos y anulaciones.
- Probar filtros por fecha y sucursal.
- Probar totales de disponibilidades.
- Probar clasificacion de movimientos bancarios.
- Probar que la UI no falle con pagos en efectivo sin cuenta bancaria.
- Probar que no haya doble conteo entre venta, acreditacion y movimiento bancario.

## Decision rules

- Venta digital no equivale a acreditacion bancaria.
- Acreditacion bancaria no equivale a movimiento bancario manual cualquiera.
- Pago administrativo no redefine por si solo la deuda; la deuda se recompone desde pagos registrados.
- Un dashboard financiero no puede mezclar saldos acumulados con flujos del periodo sin aclararlo.
- Si una formula no puede explicarse en una frase corta, todavia no esta madura.

## Invariantes clave

- Una acreditacion bancaria no es la venta que la origino.
- Un movimiento bancario debe quedar tipificado y fechable.
- Una disponibilidad total debe ser derivable desde efectivo y banco sin edicion manual.
- Un pago anulado no desaparece; deja trazabilidad y recomputa saldo.
- Las alertas financieras deben derivarse de datos persistidos, no de heuristicas opacas.
- Los pagos en efectivo no deben exigir cuenta bancaria.

## Stop-ship

- contar venta y acreditacion como si fueran el mismo ingreso disponible
- usar movimiento bancario como fuente del estado de deuda
- dashboard lindo pero imposible de explicar contablemente
- filtros que dependen de propiedades Python en vez de campos ORM
- enum o taxonomia bancaria sin casos obligatorios ni impacto claro en reportes

## Archivos a mirar primero

- `treasury/models.py`
- `treasury/services.py`
- `treasury/forms.py`
- `treasury/views.py`
- `treasury/tests.py`
- `docs/epics/EP-03-tesoreria-central.md`
- `docs/epics/EP-04-bancos-y-conciliacion.md`
- `docs/epics/EP-05-flujo-de-disponibilidades.md`
- `docs/epics/EP-10-situacion-financiera-y-alertas-consolidadas.md`

## Salida esperada

- Goal
- Hecho fuente y derivados
- Formula o criterio de lectura
- Invariantes financieras
- Archivos o capas a tocar
- Riesgos de saldo, conciliacion o doble registro
- Tests a agregar o correr
- Riesgo residual si hay definiciones de formula aun abiertas

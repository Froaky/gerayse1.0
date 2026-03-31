# EP-05 Flujo de Disponibilidades

## Objetivo

Replicar el libro mensual de `efectivo` y `banco` que hoy se administra en `FLUJO DE DISPONIBILIDADES MAP.xlsx`.

## Incluye

- libro de efectivo central
- libro bancario consolidado
- saldo inicial y final del periodo
- arrastre mensual
- retiros, aportes y unificaciones
- arqueo de disponibilidades

## Reglas de negocio

- caja operativa y tesoreria central siguen separadas
- el flujo central toma datos de caja y banco, no se carga duplicado
- el saldo inicial del mes debe derivar del cierre del mes anterior
- toda diferencia manual debe quedar auditada

## User Stories

### US-5.1 Libro de efectivo central [DONE]

Como administracion
Quiero ver un libro de efectivo consolidado
Para saber cuanto efectivo real hay disponible fuera de la caja operativa puntual

Criterios:
- [x] saldo inicial
- [x] ingresos desde cajas
- [x] egresos administrativos en efectivo
- [x] saldo final

### US-5.2 Libro bancario central [DONE]

Como administracion
Quiero ver un libro bancario consolidado
Para seguir disponibilidades reales en cuenta

Criterios:
- [x] saldo inicial
- [x] ingresos y egresos por cuenta
- [x] saldo final

### US-5.3 Arrastre mensual [DONE]

Como administracion
Quiero que el sistema arrastre el saldo inicial del mes
Para no recalcular manualmente cada hoja

Criterios:
- [x] cierre de mes previo
- [x] arranque automatico del nuevo mes
- [x] posibilidad de ajuste auditado

### US-5.4 Consolidado efectivo + banco [DONE]

Como administracion
Quiero ver la disponibilidad total
Para tomar decisiones de pago y liquidez

Criterios:
- [x] total efectivo
- [x] total banco
- [x] total consolidado
- [x] filtros por fecha

### US-5.5 Retiros, aportes y unificacion de cajas [DONE]

Como administracion
Quiero registrar movimientos de retiro/aporte/unificacion
Para reflejar la operatoria real mencionada en el relevo

Criterios:
- [x] origen y destino
- [x] fecha
- [x] importe
- [x] motivo
- [x] aprobacion si aplica

### US-5.6 Arqueo de disponibilidades [DONE]

Como administracion
Quiero comparar saldo teorico vs saldo contado
Para detectar diferencias fuera de caja operativa

Criterios:
- [x] saldo sistema
- [x] saldo contado
- [x] diferencia
- [x] justificacion

## Dependencias

- EP-03 y EP-04 cerradas

## Criterio de cierre

- la hoja mensual de flujo debe salir del sistema
- el saldo inicial del mes siguiente ya no se toma manualmente del Excel anterior

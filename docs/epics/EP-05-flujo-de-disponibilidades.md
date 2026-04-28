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
- carga inicial visible de caja fuerte central
- egresos administrativos de tesoreria separados de caja operativa

## No incluye todavia

- proyeccion de caja futura
- conciliacion bancaria avanzada
- cierre contable formal
- cierre mensual separado por sucursal para tesoreria
- egresos operativos de sucursal, cubiertos por `EP-08`

## Reglas de negocio

- caja operativa y tesoreria central siguen separadas
- el flujo central toma datos de caja y banco, no se carga duplicado
- el saldo inicial del mes debe derivar del cierre del mes anterior
- toda diferencia manual debe quedar auditada
- el saldo inicial de caja fuerte central no se carga desde la apertura de caja de una sucursal
- un egreso administrativo de tesoreria no debe impactar una caja operativa abierta
- si se permite una carga inicial manual por puesta en marcha, debe quedar auditada con fecha, usuario y motivo

## User Stories

### [x] US-5.1 Libro de efectivo central

Como administracion
Quiero ver un libro de efectivo consolidado
Para saber cuanto efectivo real hay disponible fuera de la caja operativa puntual

Criterios:
- [x] saldo inicial
- [x] ingresos desde cajas
- [x] egresos administrativos en efectivo
- [x] saldo final

### [x] US-5.2 Libro bancario central

Como administracion
Quiero ver un libro bancario consolidado
Para seguir disponibilidades reales en cuenta

Criterios:
- [x] saldo inicial
- [x] ingresos y egresos por cuenta
- [x] saldo final

### [x] US-5.3 Arrastre mensual

Como administracion
Quiero que el sistema arrastre el saldo inicial del mes
Para no recalcular manualmente cada hoja

Criterios:
- [x] cierre de mes previo
- [x] arranque automatico del nuevo mes
- [x] posibilidad de ajuste auditado

### [x] US-5.4 Consolidado efectivo + banco

Como administracion
Quiero ver la disponibilidad total
Para tomar decisiones de pago y liquidez

Criterios:
- [x] total efectivo
- [x] total banco
- [x] total consolidado
- [x] filtros por fecha

### [x] US-5.5 Retiros, aportes y unificacion de cajas

Como administracion
Quiero registrar movimientos de retiro/aporte/unificacion
Para reflejar la operatoria real mencionada en el relevo

Criterios:
- [x] origen y destino
- [x] fecha
- [x] importe
- [x] motivo
- [x] aprobacion si aplica

### [x] US-5.6 Arqueo de disponibilidades

Como administracion
Quiero comparar saldo teorico vs saldo contado
Para detectar diferencias fuera de caja operativa

Criterios:
- [x] saldo sistema
- [x] saldo contado
- [x] diferencia
- [x] justificacion

### [x] US-5.7 Carga inicial de caja fuerte central

Como administracion
Quiero registrar el saldo inicial de caja fuerte central desde tesoreria
Para arrancar el control de disponibilidades sin mezclarlo con la apertura de caja de una sucursal

Criterios:
- existe un flujo visible para cargar o ajustar el saldo inicial de caja fuerte central
- la carga inicial exige fecha, importe, usuario y motivo
- la carga inicial o ajuste queda auditado y visible en el libro de efectivo central
- despues de inicializado el saldo, el saldo final se deriva de movimientos reales y cierres previos
- la carga no requiere una caja operativa abierta
- la carga no impacta ventas, egresos ni saldo de ninguna sucursal

### [x] US-5.8 Egresos administrativos desde tesoreria

Como administracion
Quiero registrar egresos que salen netamente de tesoreria
Para no cargarlos como gastos de una caja operativa de sucursal

Criterios:
- existe un flujo separado de caja operativa para egresos de tesoreria
- el egreso exige fecha, importe, concepto, usuario y clasificacion minima
- si el egreso sale de caja fuerte central, reduce el libro de efectivo central
- si el egreso sale de banco, impacta el libro bancario correspondiente
- el egreso queda visible en disponibilidades y no en movimientos operativos de una caja de sucursal
- la UI diferencia con claridad `egreso operativo de caja` y `egreso de tesoreria`

## Dependencias

- EP-03 y EP-04 cerradas
- EP-08 para caja operativa, ventas por canal y egresos por rubro de sucursal

## Orden tecnico sugerido

1. consolidar libro de efectivo central
2. consolidar libro bancario central
3. resolver arrastre mensual de saldos
4. mostrar total efectivo, banco y consolidado
5. registrar retiros, aportes y unificaciones auditadas
6. cerrar arqueo y diferencias de disponibilidades
7. reforzar carga inicial visible de caja fuerte central
8. separar egresos administrativos de tesoreria respecto de caja operativa

## Criterio de cierre

- la hoja mensual de flujo debe salir del sistema
- el saldo inicial del mes siguiente ya no se toma manualmente del Excel anterior
- administracion sabe donde cargar o ajustar el saldo inicial de caja fuerte central
- los egresos de tesoreria no se confunden con egresos operativos de sucursal

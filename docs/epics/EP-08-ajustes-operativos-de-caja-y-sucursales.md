# EP-08 Ajustes Operativos de Caja y Sucursales

## Objetivo

Simplificar la operatoria diaria de cajas para que registren solo lo que realmente corresponde al frente operativo, y formalizar la estructura de sucursales con codigos y razon social.

## Incluye

- caja operativa enfocada en ingresos
- egresos por rubro con trazabilidad
- simplificacion del detalle visible en movimientos
- maestro de sucursales con codigo y razon social
- totales por sucursal y por periodo
- traspasos entre cajas
- escenarios controlados de unificacion o arrastre entre turnos y dias

## No incluye todavia

- rediseno contable general
- conciliacion bancaria avanzada
- reglas de rentabilidad economica

## Reglas de negocio

- la caja operativa no debe mezclar captura de ingresos con egresos administrativos sin clasificacion
- todo egreso operativo debe quedar asociado a un rubro
- toda sucursal debe tener un codigo unico y una razon social asociada
- no debe existir transferencia entre sucursales si la operatoria real ya no la usa
- un arrastre o unificacion entre turnos o dias no puede duplicar ni perder saldo

## User Stories

### [ ] US-8.1 Caja operativa solo para ingresos

Como encargado
Quiero que la caja diaria registre solo ingresos operativos
Para evitar mezclar ventas con otros movimientos que distorsionan el control

Criterios:
- se registran ingresos en efectivo y otros ingresos operativos definidos
- los egresos no aparecen como carga rapida dentro del flujo principal de caja
- el saldo diario sigue siendo explicable desde movimientos auditados
- la UI deja claro cuando un movimiento no pertenece a caja operativa

### [x] US-8.2 Egreso por rubro en reemplazo de gasto rapido

Como administracion
Quiero registrar egresos por rubro
Para reemplazar cargas libres y mantener clasificacion consistente

Criterios:
- rubro obligatorio
- fecha, monto, sucursal y usuario obligatorios
- observacion opcional pero visible
- no existe un flujo de "gasto rapido" sin clasificacion

### [ ] US-8.3 Simplificacion del detalle de movimientos

Como operador
Quiero ver y cargar solo el detalle minimo necesario
Para reducir ruido en la operacion diaria

Criterios:
- se elimina el detalle de producto cuando no aporta al control de caja
- se elimina el campo de detalle redundante o libre si no se usa para una regla de negocio
- la informacion minima restante permite auditar el movimiento

### [x] US-8.4 Maestro de sucursales con codigo y razon social

Como administracion
Quiero mantener sucursales con codigo y razon social
Para identificar correctamente cada operacion y su pertenencia societaria

Criterios:
- codigo unico por sucursal
- nombre visible de sucursal
- razon social asociada obligatoria
- activacion y desactivacion controlada
- filtros por sucursal y codigo

### [x] US-8.5 Totales por sucursal y por periodo

Como administracion
Quiero ver totales de cajas por sucursal y periodo
Para comparar operacion entre locales sin reconstruir planillas

Criterios:
- filtro por rango de fechas
- filtro por sucursal
- total de ingresos visible
- total de egresos por rubro visible
- saldo neto visible

### [x] US-8.6 Traspasos solo entre cajas

Como administracion
Quiero mantener traspasos entre cajas y quitar los traspasos entre sucursales
Para reflejar la operatoria real y evitar opciones que inducen error

Criterios:
- el flujo entre cajas sigue disponible
- el flujo entre sucursales deja de estar disponible en UI y validaciones
- la historia existente queda auditada
- no se pierde trazabilidad de traspasos ya registrados

### [ ] US-8.7 Unificacion o arrastre entre turnos y dias

Como administracion
Quiero registrar unificaciones controladas entre cajas de distintos turnos o dias
Para cubrir escenarios reales como TT dia 1 hacia TM dia 2 sin ajustes manuales

Criterios:
- se informa origen y destino
- se informa fecha operativa y turno de ambos lados
- el movimiento queda auditado como una sola operacion explicable
- los saldos de origen y destino quedan consistentes
- el flujo permite ampliar a otros escenarios similares sin duplicar reglas

## Dependencias

- EP-01 caja operativa base
- EP-02 alertas operativas

## Orden tecnico sugerido

1. simplificar reglas y UI de caja
2. reemplazar gasto rapido por egreso por rubro
3. formalizar maestro de sucursales con codigo y razon social
4. quitar traspaso entre sucursales
5. agregar totales por sucursal y periodo
6. cerrar escenario de unificacion o arrastre entre turnos y dias

## Criterio de cierre

- la caja diaria deja de usarse para conceptos que no corresponden
- cada sucursal queda identificada por codigo y razon social
- ya no hay necesidad de corregir por fuera del sistema traspasos o arrastres especiales
- el total por sucursal y periodo sale del sistema sin planilla auxiliar

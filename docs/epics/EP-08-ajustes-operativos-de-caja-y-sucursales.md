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
- turnos operativos recurrentes sin alta manual diaria
- apertura de caja con importes claros y persistencia verificable
- carga diaria por canal de venta y egresos operativos desde la caja
- dashboard de caja con saldo efectivo y ventas a acreditar discriminadas

## No incluye todavia

- rediseno contable general
- conciliacion bancaria avanzada
- reglas de rentabilidad economica
- egresos administrativos de tesoreria o caja fuerte central, cubiertos por `EP-05`
- cambio de empresa activa o aislamiento entre empresas, cubierto por `EP-12`

## Reglas de negocio

- la caja operativa no debe mezclar captura de ingresos con egresos administrativos sin clasificacion
- todo egreso operativo debe quedar asociado a un rubro
- toda sucursal debe tener un codigo unico y una razon social asociada
- no debe existir transferencia entre sucursales si la operatoria real ya no la usa
- un arrastre o unificacion entre turnos o dias solo aplica dentro de la misma sucursal salvo nueva regla explicita
- un arrastre o unificacion entre turnos o dias no puede duplicar ni perder saldo
- el operador no debe depender de crear un turno diario antes de abrir caja
- los turnos `Turno Manana` y `Turno Tarde` deben estar disponibles como nombres operativos recurrentes
- la fecha operativa sigue siendo obligatoria para reportes, pero debe resolverse en apertura de caja sin bloquear por falta de alta previa de turno
- cada importe de apertura debe indicar si impacta efectivo fisico, ventas a acreditar o solo lectura operativa
- una apertura que no se guarda debe mostrar validaciones visibles y no perder la carga del usuario
- una caja no puede abrirse con un turno o sucursal que no correspondan al contexto elegido por el operador
- el saldo neto operativo puede mostrar resultado total, pero no debe ocultar cuanto queda en efectivo fisico
- las ventas por tarjeta, debito, credito, QR o billetera deben verse separadas del efectivo disponible en caja

## User Stories

### [x] US-8.1 Caja operativa solo para ingresos

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

### [x] US-8.3 Simplificacion del detalle de movimientos

Como operador
Quiero ver y cargar solo el detalle minimo necesario
Para reducir ruido en la operacion diaria

Criterios:
- se elimina el detalle de producto cuando no aporta al control de caja
- se elimina el campo de detalle redundante o libre si no se usa para una regla de negocio
- el operador no necesita cargar el mismo concepto en dos campos distintos
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

### [x] US-8.7 Unificacion o arrastre entre turnos y dias

Como administracion
Quiero registrar unificaciones controladas entre cajas de distintos turnos o dias
Para cubrir escenarios reales como TT dia 1 hacia TM dia 2 sin ajustes manuales

Criterios:
- se informa origen y destino
- se informa fecha operativa y turno de ambos lados
- la unificacion o arrastre no cruza sucursales
- motivo u observacion obligatoria
- el movimiento queda auditado como una sola operacion explicable
- los saldos de origen y destino quedan consistentes
- el flujo permite ampliar a otros escenarios similares sin duplicar reglas

### [x] US-8.8 Turnos operativos recurrentes

Como operador de sucursal
Quiero elegir `Turno Manana` o `Turno Tarde` sin tener que crear un turno por fecha
Para poder abrir caja en cualquier momento sin depender de una configuracion previa

Criterios:
- existen turnos operativos activos con nombre y descripcion opcional
- `Turno Manana` y `Turno Tarde` estan disponibles para cualquier fecha operativa
- el alta o mantenimiento de turnos no exige cargar una fecha diaria para que el turno exista
- la fecha operativa se sigue guardando en la apertura de caja para reportes, cierres y auditoria
- el flujo no obliga al operador a salir de apertura de caja para crear el turno del dia
- turnos historicos con fecha siguen visibles y no pierden trazabilidad

### [x] US-8.9 Apertura de caja persistente y con errores visibles

Como operador de sucursal
Quiero abrir caja y confirmar que quedo guardada
Para empezar a cargar ventas y movimientos sin repetir la operacion

Criterios:
- al guardar una apertura valida se crea una caja abierta asociada a usuario, sucursal, turno y fecha operativa
- despues de guardar, el sistema lleva al operador a la caja activa o muestra confirmacion visible
- si la apertura falla, se muestran errores concretos por campo o por regla de negocio
- si la apertura falla, el formulario conserva los datos ya cargados
- no se permite abrir una caja duplicada para el mismo usuario, sucursal, turno y fecha operativa
- la prueba funcional debe demostrar que una apertura valida persiste y queda disponible en el dashboard

### [x] US-8.10 Apertura con importes discriminados y etiquetas claras

Como operador de sucursal
Quiero distinguir efectivo inicial, ventas por tarjeta y otros canales al abrir o iniciar la carga diaria
Para saber que importe afecta el efectivo fisico y que importe queda como venta a acreditar

Criterios:
- el campo generico `monto inicial` se reemplaza o acompana con etiquetas claras de negocio
- el efectivo inicial queda identificado como dinero fisico disponible en la caja
- las ventas o importes por debito, credito, QR, MercadoPago, Vivre u otros canales no se suman al efectivo fisico
- si se permite cargar ventas ya realizadas al iniciar el turno, deben quedar como movimientos por canal y no como saldo inicial ambiguo
- el total diario puede reconstruirse por canal sin depender de observaciones libres
- la UI debe dejar claro que conceptos impactan saldo de caja y cuales quedan solo como venta registrada o pendiente de acreditacion

### [x] US-8.11 Carga diaria de ventas y egresos operativos desde la caja

Como operador de sucursal
Quiero que al abrir caja tenga a mano la carga de ventas por canal y egresos por rubro
Para completar la planilla diaria operativa sin buscar pantallas separadas

Criterios:
- desde una caja abierta se puede cargar venta total discriminada por efectivo, debito, credito, QR, apps u otros canales configurados
- desde una caja abierta se puede cargar egreso operativo por rubro
- cada venta o egreso queda como movimiento auditado con usuario, caja, sucursal, turno y fecha operativa
- los egresos por rubro impactan el saldo de caja solo cuando salen de esa caja fisica
- los egresos administrativos de tesoreria no se cargan como egresos operativos de caja
- la pantalla de caja muestra movimientos recientes para confirmar que la carga quedo registrada

### [x] US-8.12 Coherencia entre sucursal, turno y caja al abrir

Como operador de sucursal
Quiero ver solo turnos compatibles con mi sucursal
Para no abrir caja por error en otra terminal o local

Criterios:
- la sucursal se selecciona o queda predefinida antes de elegir turno
- la lista de turnos no muestra opciones de otra sucursal o terminal
- si el usuario es fijo de una sucursal, la apertura usa esa sucursal por defecto y no permite mezclar otra
- la etiqueta del turno muestra nombre operativo, fecha operativa y sucursal de forma entendible
- la validacion rechaza cualquier combinacion inconsistente entre usuario, sucursal, turno y caja
- el operador no debe ver `Terminal` u otra sucursal cuando esta cargando una caja de `Vivre`, salvo que esa sea realmente la sucursal seleccionada

### [x] US-8.13 Dashboard de caja con saldo efectivo y ventas por canal

Como operador o administrador de sucursal
Quiero ver separado el saldo efectivo de caja y las ventas por tarjeta, QR, debito, credito o billetera
Para no confundir el neto operativo con el dinero fisico disponible

Criterios:
- el dashboard de caja muestra `saldo efectivo en caja` como lectura principal del dinero fisico disponible
- las ventas en efectivo impactan el saldo efectivo de caja
- las ventas por tarjeta, QR, debito, credito, billetera o app quedan visibles como ventas por canal, pero no aumentan el efectivo fisico
- el dashboard puede mostrar un `total de ventas` o `neto operativo`, pero debe aclarar que incluye canales no efectivos
- la lectura por caja, sucursal y periodo mantiene la misma separacion entre efectivo y ventas a acreditar
- los movimientos recientes permiten reconocer de que canal viene cada venta
- los tests deben cubrir que una venta en efectivo y una venta por tarjeta no se mezclan como saldo efectivo

## Dependencias

- EP-01 caja operativa base
- EP-02 alertas operativas
- EP-05 para caja fuerte central y egresos administrativos de tesoreria
- EP-12 para filtrar vistas por empresa cuando una sucursal pertenezca a una empresa

## Orden tecnico sugerido

1. simplificar reglas y UI de caja
2. reemplazar gasto rapido por egreso por rubro
3. formalizar maestro de sucursales con codigo y razon social
4. quitar traspaso entre sucursales
5. agregar totales por sucursal y periodo
6. cerrar escenario de unificacion o arrastre entre turnos y dias
7. simplificar turnos operativos recurrentes
8. corregir persistencia y validaciones de apertura de caja
9. discriminar importes de apertura y carga diaria por canal
10. reforzar coherencia sucursal-turno-caja
11. separar visualmente saldo efectivo, ventas por canal y neto operativo en dashboard

## Criterio de cierre

- la caja diaria deja de usarse para conceptos que no corresponden
- cada sucursal queda identificada por codigo y razon social
- ya no hay necesidad de corregir por fuera del sistema traspasos o arrastres especiales
- el total por sucursal y periodo sale del sistema sin planilla auxiliar
- el operador puede abrir caja para turno manana o tarde sin crear turnos diarios manualmente
- la apertura de caja guarda de forma verificable o muestra errores accionables
- la carga diaria permite explicar ventas por canal, efectivo fisico y egresos operativos sin ambiguedad
- el dashboard de caja no hace pasar ventas a acreditar como efectivo disponible

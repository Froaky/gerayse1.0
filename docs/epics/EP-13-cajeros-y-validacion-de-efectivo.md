# EP-13 Cajeros, Validacion de Efectivo y Gastos como Deuda

Aprobada por el usuario el 2026-07-10 sobre la base de `PROPUESTA-EP-13-cajeros-y-validacion-efectivo.md`.
Desarrollo en rama `staging`; no se integra a `main` sin OK explicito del usuario.

## Objetivo

Permitir que empleados cajero carguen su caja por sucursal con permisos acotados, que el efectivo cargado no cuente en ningun saldo ni reporte hasta ser validado por un usuario con permiso especifico, y que los gastos cargados desde caja queden como deuda pendiente que tesoreria paga despues.

## Decisiones de negocio (2026-07-10)

- la validacion de efectivo es un permiso configurable en la matriz de permisos de cada usuario, no un rol fijo
- la validacion aplica a todas las cajas con efectivo, sin importar quien las cargo
- la validacion es por caja completa: la responsable coteja el efectivo fisico que le entregan contra el esperado y aprieta validar
- mientras una caja esta pendiente de validacion no contabiliza nada de nada en el sistema
- una caja sin efectivo contabiliza normal, sin paso de validacion
- el gasto cargado desde caja crea una deuda pendiente y el efectivo no sale de la caja; el pago real lo hace tesoreria despues
- la deuda impacta la situacion economica al cargarse y la financiera solo al pagarse (regla ya vigente, se blinda con tests)
- (2026-07-14) mientras la caja esta abierta contabiliza normal en los tableros del dia; el estado pendiente de validacion nace al CIERRE de la caja y desde ahi no contabiliza nada hasta validarse

## Incluye

- permisos por accion configurables por rol y por usuario, ademas de lectura/escritura por modulo
- alcance de escritura por sucursal para operar caja
- rol operativo cajero acotado a la carga de caja de su sucursal
- estado de validacion por caja y exclusion de todos los totales hasta validar
- vista de pendientes de validacion con validar y rechazar auditados
- gasto desde caja como deuda pendiente sin salida de efectivo
- tests de regresion de deuda en economica vs financiera

## No incluye todavia

- validacion por movimiento individual o por lote (la validacion es por caja completa)
- conciliacion automatica del efectivo validado contra depositos bancarios
- permisos por accion para modulos que no lo necesiten aqui (la base queda extensible)
- reemplazo del egreso de efectivo tradicional para administracion (traspasos y retiros reales siguen existiendo)

## Reglas de negocio

- la validacion de efectivo es un permiso por accion asignable por rol (default) o por usuario (override)
- toda caja que al cierre involucro efectivo (movimientos, monto inicial o saldo fisico declarado) requiere validacion antes de seguir contabilizando
- una caja abierta contabiliza normal; la exclusion de totales rige desde el cierre hasta la validacion
- una caja pendiente de validacion no aporta a ningun saldo, dashboard, snapshot, reporte, total ni alerta del sistema
- una caja sin movimientos de efectivo contabiliza normal sin validacion
- validar y rechazar exigen usuario responsable y quedan auditados; el rechazo exige motivo
- una caja rechazada sigue sin contabilizar hasta corregirse (correccion auditada de `EP-08`) y validarse
- las cajas anteriores a esta funcionalidad siguen contando como hasta ahora; el cambio no reinterpreta historia
- el gasto cargado desde una caja crea una `CuentaPorPagar` pendiente y no descuenta efectivo de esa caja
- un gasto como deuda no puede contarse dos veces: entra a la economica como deuda del periodo y a la financiera solo cuando tesoreria lo paga
- un cajero solo puede operar cajas de su sucursal asignada; la restriccion valida la escritura en backend, no solo la pantalla

## User Stories

### [x] US-13.1 Regresion de deuda en economica vs financiera

Como administracion
Quiero evidencia automatizada de que una deuda impacta la economica al cargarse y la financiera al pagarse
Para blindar la regla vigente antes de sumar gastos de caja como deuda

Criterios:
- un test demuestra que una deuda del periodo suma a la lectura economica por su importe total al cargarse
- un test demuestra que esa deuda sin pago no impacta la lectura financiera
- un test demuestra que el pago real impacta la financiera en el periodo del pago
- una deuda anulada no impacta la economica
- los tests corren con la suite estandar del repo

### [x] US-13.2 Permisos por accion configurables

Como administracion
Quiero asignar permisos por accion especifica, por ejemplo validar efectivo, desde la ficha de usuario y de rol
Para separar quien carga, quien valida y quien administra sin crear roles duplicados

Criterios:
- existe el concepto de permiso por accion ademas de lectura/escritura por modulo
- la accion de validar efectivo se puede activar por rol (default) y por usuario (override), consistente con la matriz actual
- el backend rechaza la accion a quien no tiene el permiso aunque conozca la URL
- usuarios y roles existentes conservan su comportamiento hasta recibir acciones explicitas
- la ficha de usuario muestra las acciones efectivas igual que muestra lectura/escritura

### [x] US-13.3 Rol cajero con alcance por sucursal

Como administracion
Quiero usuarios cajero que solo carguen la caja de su sucursal
Para dar acceso operativo sin exponer tesoreria, configuracion ni otros locales

Criterios:
- un cajero puede abrir, cargar y cerrar cajas solo de su(s) sucursal(es) asignada(s)
- el alcance por sucursal valida operaciones de escritura en backend, no solo filtra la pantalla
- un cajero no tiene escritura en tesoreria, configuracion ni usuarios
- el alcance conviene con `usuario fijo` y `sucursal_base` sin duplicar reglas
- usuarios legacy sin alcance explicito mantienen compatibilidad
- cubre para caja la parte necesaria de `EP-09` `US-9.11`; generalizar a otros modulos queda en `EP-09`

### [x] US-13.4 Caja pendiente de validacion excluida de todos los totales

Como administracion
Quiero que una caja con efectivo no contabilice nada hasta ser validada
Para que ningun saldo ni reporte cuente efectivo que todavia no fue confirmado fisicamente

Criterios:
- al cerrarse, una caja que involucro efectivo queda en estado pendiente de validacion
- mientras esta pendiente, la caja no aporta a dashboards, totales por sucursal/periodo, seguimiento, semaforos, disponibilidades, situacion financiera ni economica
- una caja sin movimientos de efectivo no requiere validacion y contabiliza normal
- al validarse, la caja contabiliza normalmente en todos los puntos anteriores sin recalculo manual
- las cajas previas a esta funcionalidad quedan como validadas o no requeridas y no cambian ningun numero historico
- los tests cubren cada punto de agregacion con una caja pendiente y una validada

### [x] US-13.5 Vista de pendientes de validacion

Como responsable con permiso de validar efectivo
Quiero ver las cajas pendientes, cotejar el efectivo que me entregan y validar o rechazar
Para confirmar el efectivo fisico antes de que el sistema lo cuente

Criterios:
- existe una vista de pendientes de validacion dentro de cajas, visible para quien tiene el permiso
- cada caja pendiente muestra sucursal, fecha operativa, turno, responsable y efectivo esperado por sistema
- el boton validar exige el permiso por accion y deja registro de quien y cuando valido
- el rechazo exige motivo, deja registro auditado y la caja sigue sin contabilizar
- una caja rechazada puede corregirse y volver a validarse
- el estado de validacion es visible en los listados y detalle de caja existentes

### [x] US-13.6 Gasto desde caja como deuda pendiente

Como empleado de sucursal
Quiero registrar un gasto desde mi caja sin sacar efectivo
Para que quede como deuda pendiente que administracion paga y controla despues

Criterios:
- desde una caja se puede registrar un gasto con rubro, importe y datos minimos de trazabilidad
- el gasto crea una `CuentaPorPagar` pendiente asociada a sucursal, rubro y periodo
- el gasto no descuenta efectivo ni altera el saldo de la caja
- la deuda creada impacta la situacion economica del periodo al cargarse
- la financiera solo se afecta cuando tesoreria registra el pago real de esa deuda
- el gasto no se cuenta dos veces en ningun reporte
- el egreso de efectivo tradicional sigue disponible solo para quien tenga el permiso correspondiente

## Dependencias

- `EP-08` caja operativa, movimientos, cierres y correcciones auditadas
- `EP-09` `US-9.11`: esta epica implementa el alcance por sucursal necesario para caja; la generalizacion queda en `EP-09`
- `EP-03`, `EP-05`, `EP-10`, `EP-11` para deuda, pagos y snapshots financieros/economicos

## Orden tecnico sugerido

1. US-13.1 tests de regresion de deuda
2. US-13.2 permisos por accion
3. US-13.3 rol cajero y alcance por sucursal
4. US-13.4 exclusion de totales por estado de validacion
5. US-13.5 vista de pendientes y acciones de validar/rechazar
6. US-13.6 gasto desde caja como deuda pendiente

## Criterio de cierre

- un cajero puede cargar su caja sin ver ni tocar tesoreria, configuracion ni usuarios
- el efectivo cargado no impacta ningun saldo ni reporte hasta ser validado por un usuario con el permiso especifico
- quien valida puede ver todas las cajas pendientes, cotejar el efectivo y validar o rechazar con auditoria
- una caja sin efectivo opera sin friccion adicional
- un gasto cargado desde caja queda como deuda pendiente sin mover efectivo y sin duplicarse en ningun reporte
- existe evidencia automatizada de que la deuda impacta economica al cargarse y financiera al pagarse

# PROPUESTA EP-13 Cajeros por Sucursal, Validacion de Efectivo y Permisos Finos

> **ESTADO: PROPUESTA PARA PRESUPUESTAR. NO IMPLEMENTAR SIN OK EXPLICITO DEL USUARIO.**
> Este documento existe para que el usuario pueda dimensionar y presupuestar el trabajo.
> No se asigna a ningun agente para ejecucion, no se marca en el `README.md` como backlog
> activo y no se toca codigo ni migraciones a partir de este documento hasta que el usuario
> lo apruebe explicitamente, epica por epica o historia por historia.

## Origen del pedido (2026-07-08)

1. Usuarios "cajero" que carguen la caja por sucursal; el egreso de caja y los gastos se
   cargan como deuda.
2. "Presupuesto de la validacion de efectivo": si un cajero carga un ingreso de efectivo, ese
   efectivo no debe contar hasta que un usuario con permiso de validacion lo confirme. Pedido
   explicito de pulir el modulo de permisos para que esto se pueda armar prolijo.
3. Las deudas deberian impactar la situacion economica al cargarse (resta apenas se carga) y
   la situacion financiera al pagarse.

## Diagnostico tecnico de partida (que ya existe, que falta)

Esto cambia el tamano real de cada slice al presupuestar, porque parte del pedido ya esta
resuelto o parcialmente resuelto:

- **Caja por sucursal**: ya existe (`Caja`, `MovimientoCaja`, `CierreCaja`, `EP-08`). Lo que
  no existe es un rol "cajero" acotado: hoy el actor es un `User` generico con permisos por
  modulo completo (`cashops`, `treasury`, `config`, `users`), sin restriccion de "solo puede
  cargar su sucursal" ni de "solo puede cargar, no confirmar".
- **Egreso de caja como deuda**: hoy un egreso de caja operativa (`MovimientoCaja` de tipo
  egreso) descuenta directamente el efectivo de la caja; no genera una `CuentaPorPagar`. Crear
  deuda automaticamente a partir de un egreso de caja es un cambio de flujo de datos, no una
  configuracion.
- **Validacion de efectivo**: no existe ningun estado "pendiente de validar" para un ingreso
  de caja. Existe `ArqueoDisponibilidades` (compara efectivo de sistema contra efectivo
  contado), pero se registra directo, sin flujo de aprobacion. Este es el gap mas grande de
  los tres pedidos.
- **Permisos**: hoy son por modulo completo (`PermissionModule` + `Role` + `RolePermission` +
  `UserPermission`, lectura/escritura), sin alcance por sucursal ni por accion puntual dentro
  de un modulo (ej.: "puede cargar caja" vs "puede validar efectivo" dentro de `cashops`). Esto
  ya esta reconocido como pendiente en `EP-09` `US-9.11` (permisos por sucursal/empresa/lugar).
  La validacion de efectivo necesita, ademas, un permiso por **accion** (validar) que hoy no
  existe como concepto, no solo un permiso por **alcance** (sucursal).
- **Deudas en economica vs financiera**: revisado el codigo actual, esto ya esta mayormente
  resuelto:
  - situacion economica: `build_economic_period_snapshot()` ya suma `CuentaPorPagar.importe_total`
    (el total, no solo lo pagado) de las deudas no anuladas del periodo -> ya resta "apenas se
    carga", como pide el usuario.
  - situacion financiera: se arma desde movimientos reales de caja/banco (`PagoTesoreria` y su
    reflejo bancario), no desde deuda devengada -> una deuda sin pago real no impacta hoy la
    financiera, lo cual ya es consistente con "al ser pagado".
  - Este punto probablemente no requiera desarrollo nuevo, sino **confirmarlo con casos reales
    del cliente y agregar tests de regresion** que dejen la regla blindada. Se incluye igual en
    la propuesta para que el presupuesto lo contemple, pero es la pieza mas chica de las tres.

## Por que esto es una propuesta aparte y no una epica activa

- Toca permisos y flujo de dinero en efectivo: cualquier error aqui es alto riesgo operativo
  (efectivo que no cuenta cuando deberia, o que cuenta antes de tiempo).
- Requiere decisiones de negocio que hoy no estan definidas (ver "Preguntas abiertas").
- El usuario pidio explicitamente dejarlo en plan/presupuesto, no en ejecucion.

## Slices propuestos (para dimensionar, no para ejecutar)

### Slice 1: Rol operativo "cajero" con alcance por sucursal

- Definir un rol o permiso que limite a un usuario a cargar caja solo de su(s) sucursal(es)
  asignada(s), sin acceso de escritura a tesoreria, configuracion ni usuarios.
- Depende de resolver primero granularidad de permisos por sucursal (`EP-09` `US-9.11`), porque
  hoy el alcance por sucursal para escritura no esta implementado a nivel backend en ninguna
  vista protegida.
- Esfuerzo estimado: medio. Es fundacional para el resto de los slices (sin esto, "permiso de
  validacion de efectivo" no tiene donde apoyarse de forma prolija).

### Slice 2: Permisos por accion dentro de un modulo (no solo lectura/escritura)

- Extender el modelo de permisos actual para soportar acciones especificas ademas de
  lectura/escritura genericos: por ejemplo `cargar_caja`, `validar_efectivo`, `cerrar_caja`.
- Es un cambio de modelo (`PermissionModule`, `RolePermission`, `UserPermission`) con migracion,
  no solo de UI.
- Esfuerzo estimado: medio-alto, por la superficie de views/forms que hoy solo chequean
  lectura/escritura por modulo y deberian empezar a chequear accion.

### Slice 3: Estado "pendiente de validar" para ingresos de efectivo

- Un ingreso de efectivo cargado por un cajero queda en un estado "pendiente" y no suma al
  efectivo disponible de caja/dashboard/disponibilidades hasta que un usuario con el permiso
  del Slice 2 lo valide.
- Requiere: nuevo estado en `MovimientoCaja` (o modelo satelite de validacion), pantalla de
  cola de pendientes por validar, accion de validar/rechazar con motivo y trazabilidad, y que
  todos los calculos de saldo (caja, dashboard, disponibilidades, situacion financiera) excluyan
  lo no validado hasta que se confirme.
- Riesgo alto: toca formulas de saldo ya usadas en produccion (`Caja.saldo_esperado`,
  `build_disponibilidades_snapshot`, `build_financial_period_snapshot`); un descuido aqui
  puede duplicar o esconder efectivo real.
- Esfuerzo estimado: alto. Es el slice mas grande de la propuesta.

### Slice 4: Egreso de caja generando deuda automatica

- Cuando se carga un egreso de caja, ademas de descontar el efectivo fisico, se crea una
  `CuentaPorPagar` asociada (proveedor, rubro, sucursal, periodo, importe).
- Riesgo de doble conteo: hoy un egreso de caja ya resta efectivo directamente; si ademas se
  registra como deuda y esa deuda se paga despues por tesoreria, hay que evitar que el gasto se
  cuente dos veces (una vez como salida de caja, otra vez como pago de la deuda).
- Requiere definir con el cliente si el egreso de caja "es" la deuda ya pagada (estado `PAGADA`
  desde el alta, solo para trazabilidad) o si genera una deuda real `PENDIENTE` que despues se
  paga por otro medio (lo cual no tendria sentido si el efectivo ya salio de la caja).
- Esfuerzo estimado: medio, pero con alto riesgo de reglas de negocio ambiguas (ver preguntas
  abiertas).

### Slice 5: Regresion de deudas en economica vs financiera

- Agregar tests explicitos que fijen el comportamiento ya vigente: deuda entra a economica al
  cargarse (por `importe_total`, no por pago), y entra a financiera solo cuando hay un pago real
  registrado.
- Esfuerzo estimado: bajo. Es mayormente verificacion, no construccion.

## Orden sugerido si se aprueba

1. Slice 5 (bajo costo, cierra una duda y dejar blindada la regla actual)
2. Slice 1 y Slice 2 en conjunto (son la base de permisos que todo lo demas necesita)
3. Slice 3 (el mas grande y riesgoso; requiere el permiso de validacion ya resuelto)
4. Slice 4 (depende de que el cliente resuelva la pregunta de doble conteo antes de tocar codigo)

## Preguntas abiertas que cambian el presupuesto

Estas preguntas deberian resolverse con el cliente antes de cotizar un numero cerrado:

- Si un ingreso de efectivo esta "pendiente de validar", ¿el cajero puede seguir operando la
  caja con ese efectivo (por ejemplo dar vuelto) o queda bloqueado hasta la validacion?
- ¿La validacion es por movimiento individual, por caja completa al cierre, o por lote diario?
- ¿Quien valida: un rol fijo (ej. "Administracion") o cualquier usuario con el permiso
  puntual, sin importar el rol?
- Egreso de caja como deuda: ¿la deuda nace ya pagada (solo trazabilidad) o pendiente de pago
  real por otro medio? Si nace pendiente, ¿de donde sale el efectivo para pagarla si ya salio
  de la caja al momento del egreso?
- ¿Los cajeros deben ver situacion financiera/economica de su sucursal, o solo la pantalla de
  carga de caja?

## Dependencias

- `EP-08` caja operativa y sucursales (base de `Caja`/`MovimientoCaja`)
- `EP-09` `US-9.11` permisos por sucursal/empresa/lugar (prerequisito real de Slice 1 y 3)
- `EP-05`/`EP-11` para el impacto de deuda en disponibilidad y situacion economica

## Criterio de cierre (si se aprueba y se ejecuta a futuro)

- un cajero puede cargar su caja sin ver ni tocar tesoreria, configuracion ni usuarios
- el efectivo cargado por un cajero no impacta ningun saldo ni reporte hasta ser validado por
  un usuario con el permiso especifico
- un egreso de caja queda trazable como deuda sin duplicar el gasto en ningun reporte
- existe evidencia automatizada (tests) de que deuda impacta economica al cargarse y financiera
  al pagarse

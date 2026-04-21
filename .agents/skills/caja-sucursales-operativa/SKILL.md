---
name: caja-sucursales-operativa
description: define, review, and implement operational cash workflows for branches, boxes, shifts, transfers, carry-overs, and branch totals. use when working on `cashops/`, branch-scoped operational rules, EP-08, or any request about caja diaria, sucursales, traspasos, or period totals.
---

Tratar caja operativa como control diario de sucursal, no como contabilidad general ni como tesoreria central.

Siempre:
1. Leer `context.md`, `docs/epics/EP-08-ajustes-operativos-de-caja-y-sucursales.md` y `references/gerayse-caja-scope.md`.
2. Diferenciar con precision:
   - sucursal
   - turno
   - caja
   - movimiento operativo
   - traspaso entre cajas
   - arrastre o unificacion entre dias o turnos
3. Mantener separada caja operativa de tesoreria, banco y lectura economica.
4. Preservar trazabilidad completa: origen, destino, usuario, fecha operativa, motivo y efecto en saldos.
5. Si un flujo deja de usarse, quitarlo de UI y validaciones; no basta con esconderlo.
6. Tratar cualquier arrastre o unificacion como evento auditable, nunca como correccion silenciosa.
7. Derivar totales desde movimientos reales; no aceptar numeros editados a mano.

## Contrato del agente

Este skill protege que un cambio en `cashops` no:
- duplique saldo
- rompa la relacion entre caja y sucursal
- reintroduzca traspasos prohibidos
- convierta un flujo auditable en un parche manual

Si aparece alguna de esas cuatro, el cambio no esta listo.

## Flujo de trabajo

### 1. Clasificar el tipo de cambio

Confirmar si toca:
- apertura o cierre
- ingreso o egreso
- traspaso entre cajas
- maestro de sucursales
- reporte por sucursal o periodo
- arrastre entre turnos o dias

Decidir si el riesgo principal esta en:
- dominio y saldos
- UI y visibilidad
- permisos
- trazabilidad

### 2. Elegir el hecho correcto

- Si mueve saldo dentro de una caja: `MovimientoCaja`
- Si mueve saldo entre dos cajas habilitadas: `Transferencia`
- Si cambia lectura por sucursal o periodo: agregado derivado desde movimientos
- Si propone mover saldo entre turnos o dias: flujo especial auditable, no parche de saldo
- Si cruza sucursales: asumir prohibido hasta que backlog diga lo contrario

### 3. Aplicar matriz de decision

- misma sucursal, mismo dia, dos cajas distintas: posible traspaso entre cajas
- misma sucursal, distinto turno o distinto dia: probable arrastre o unificacion auditada
- distinta sucursal: no habilitar por defecto
- egreso sin rubro: rechazar
- total por sucursal: derivar de movimientos y filtros, no de texto libre

### 4. Aplicar guardrails

Validar siempre:
- origen != destino
- caja pertenece a la sucursal correcta
- turno y fecha operativa coherentes
- fondos suficientes cuando aplique
- rubro obligatorio para egreso
- no duplicar saldo en arrastres
- rutas deshabilitadas realmente bloqueadas, no solo ocultas

### 5. Cerrar con evidencia

- Probar saldos antes y despues.
- Probar filtros por sucursal y periodo.
- Probar casos invalidos y rutas que ya no deben existir.
- Si cambia copy operativo, probar tambien la visibilidad correcta del flujo.
- Si toca sucursales, probar `codigo`, `nombre`, `razon_social` y estado activo.

## Invariantes clave

- Una caja pertenece a una sucursal y a un turno operativo concretos.
- Un traspaso entre cajas no debe implicar por defecto un traspaso entre sucursales.
- Un egreso operativo debe quedar clasificado por rubro.
- Un total por sucursal debe ser derivable desde movimientos.
- Un arrastre entre dias o turnos debe ser explicable como evento de negocio.
- Una sucursal debe sostener identidad operativa por `codigo` y `razon_social`.

## Matriz de stop-ship

Detener el cambio si ocurre cualquiera de estos:
- UI quita una opcion pero el servicio la sigue aceptando
- un resumen suma algo no fisico como si fuera efectivo
- un arrastre toca saldo sin dejar traza de origen y destino
- un flujo entre sucursales vuelve a aparecer
- se puede editar un total derivado a mano

## Archivos a mirar primero

- `cashops/models.py`
- `cashops/forms.py`
- `cashops/views.py`
- `cashops/services.py`
- `cashops/tests.py`
- `docs/epics/EP-08-ajustes-operativos-de-caja-y-sucursales.md`

## Salida esperada

- Goal
- Flujo operativo afectado
- Hecho fuente
- Invariantes y validaciones
- Archivos o capas a tocar
- Riesgos de saldo o auditoria
- Tests a agregar o correr
- Riesgo residual si queda algun arrastre pendiente de diseno

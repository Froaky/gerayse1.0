# Gerayse Caja Scope

Usar esta referencia cuando el pedido toque caja operativa o sucursales.

## 1. Fuentes primarias

- `docs/epics/EP-08-ajustes-operativos-de-caja-y-sucursales.md`
- `cashops/models.py`
- `cashops/forms.py`
- `cashops/views.py`
- `cashops/services.py`
- `cashops/tests.py`

## 2. Mapa de objetos

- `Sucursal`: identidad operativa de local
- `Turno`: fecha operativa + T.M. / T.T.
- `Caja`: contexto operativo por usuario, turno y sucursal
- `MovimientoCaja`: hecho base de saldo y lectura diaria
- `Transferencia`: traza entre cajas o, historicamente, entre sucursales

## 3. Hechos y derivados

- saldo de caja: derivado de `MovimientoCaja`
- traza de movimiento entre cajas: `Transferencia` mas sus efectos
- total por sucursal: derivado por filtro sobre movimientos
- lectura por rango: agregacion, no entidad nueva

## 4. Decisiones funcionales actuales

- `gasto rapido` debe desaparecer como concepto visible
- `egreso por rubro` es la salida operativa valida
- traspaso entre sucursales queda deshabilitado
- maestro de sucursales requiere `codigo` y `razon_social`
- hay lectura por rango y sucursal para control operativo

## 5. Estado funcional de EP-08

- hecho:
  - `US-8.2`
  - `US-8.4`
  - `US-8.5`
  - `US-8.6`
- pendiente:
  - `US-8.3`
  - `US-8.7`

## 6. Matriz de decision rapida

- ingreso fisico a una caja: `MovimientoCaja`
- egreso operativo clasificado: `MovimientoCaja` con rubro
- dinero de una caja a otra: `Transferencia` + movimientos asociados
- dinero entre dias o turnos: flujo especial auditable
- dinero entre sucursales: revisar backlog; hoy no habilitar

## 7. Preguntas obligatorias antes de tocar codigo

- el saldo cambia o solo cambia la lectura
- el hecho fuente es movimiento, transferencia o agregado
- el flujo debe seguir visible o quedar explicitamente bloqueado
- la fecha relevante es `fecha_operativa`, turno o rango
- el dato es por caja, por sucursal o consolidado

## 8. Riesgos tipicos

- duplicar saldo al arrastrar entre dias
- mezclar sucursal, caja y tesoreria en una misma pantalla
- quitar una opcion solo en UI y dejar camino por servicio
- calcular totales desde texto libre o cierres en vez de movimientos

## 9. Stop-ship funcional

- movimiento que afecta caja equivocada
- traspaso permitido con origen y destino iguales
- egreso sin rubro
- total de sucursal editable a mano
- flujo entre sucursales reabierto sin decision de backlog

## 10. Que probar casi siempre

- saldo antes y despues
- permiso de operador vs admin
- filtro por sucursal
- filtro por fecha o rango
- caso invalido de origen/destino
- ruta que debe desaparecer o bloquearse

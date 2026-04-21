# Gerayse Control Scope

Usar esta referencia cuando el pedido toque control de gestion, rentabilidad o vistas economicas.

## 1. Fuentes primarias

- `docs/epics/EP-06-control-de-gestion-y-alertas.md`
- `docs/epics/EP-07-impuestos-planes-y-autorizaciones.md`
- `docs/epics/EP-11-rentabilidad-y-situacion-economica.md`
- `cashops/services.py`
- `treasury/models.py`
- `treasury/services.py`

## 2. Cortes funcionales

- situacion financiera:
  - efectivo
  - banco
  - pendientes
- situacion economica:
  - ingresos base del periodo
  - gastos y deuda imputables al periodo
- control por rubro:
  - objetivos porcentuales
  - desvio real
- rentabilidad:
  - por sucursal o consolidada
  - siempre por periodo comparable

## 3. Preguntas obligatorias antes de modelar

- cual es el periodo exacto
- que se toma como base de ingresos
- que deuda entra en el calculo
- que rubros son obligatorios
- que formula valida negocio

## 4. Matriz minima de definicion de KPI

Para cada indicador definir:
- nombre
- formula
- periodo
- alcance: sucursal o consolidado
- base: ventas, deuda, gasto, objetivo
- exclusiones

## 5. Anti-patrones

- usar ventas de un periodo contra gastos de otro
- mezclar deuda total con deuda del periodo
- mantener `categoria` en unos flujos y `rubro` en otros sin criterio comun
- publicar rentabilidad sin formula explicable

## 6. Lo que probablemente venga en EP-11

- porcentajes por rubro
- desvio sobre ventas
- deuda por periodo
- rentabilidad por sucursal

## 7. Stop-ship funcional

- indicador sin periodo explicito
- deuda sin criterio de imputacion
- rubros inconsistentes entre fuentes
- comparacion de sucursales con bases distintas

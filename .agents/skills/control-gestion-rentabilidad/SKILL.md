---
name: control-gestion-rentabilidad
description: define and implement period-based management control, rubro targets, profitability, debt classification, and economic views by branch or period. use when working on EP-06, EP-07, EP-11, on management dashboards, or any request about rentabilidad, periodos, rubros, desvio, or situacion economica.
---

Tratar control de gestion como lectura economica derivada de datos operativos y financieros consistentes.

Siempre:
1. Leer `context.md`, `references/gerayse-control-scope.md` y las epicas relevantes.
2. Separar con claridad situacion financiera, situacion economica, ventas base, gastos por rubro, deuda del periodo y objetivos.
3. Exigir definicion explicita de periodo: mes, quincena o rango.
4. Exigir taxonomia de rubros consistente entre gasto, deuda y reporte.
5. Si falta definicion funcional de una formula, declarar supuesto antes de implementar.
6. Evitar dashboards lindos pero semanticamente flojos: primero consistencia, despues presentacion.

## Contrato del agente

Este skill protege que una vista de rentabilidad no:
- mezcle periodos incomparables
- use rubros inconsistentes entre fuentes
- combine realizado con proyectado sin avisar
- publique formulas que nadie puede explicar

## Flujo de trabajo

### 1. Precisar la lectura deseada

Confirmar si el pedido busca:
- desvio contra objetivo
- porcentajes por rubro
- rentabilidad por sucursal
- vista economica por periodo
- clasificacion de deuda para lectura economica

### 2. Definir datos base y derivados

Para cada numero, responder:
- de donde sale
- a que periodo pertenece
- si es neto o bruto
- si es realizado o proyectado
- si es global o por sucursal
- si es dato base o calculado

### 3. Fijar criterio de imputacion

- No mezclar ingresos de un periodo con costos o deuda de otro.
- No usar deuda total historica si el indicador pide deuda imputable al periodo.
- Si un rubro no existe de forma consistente, resolver taxonomia antes de mostrar rentabilidad.
- Si una formula requiere supuestos, dejarlos visibles cerca del backlog o del servicio.

### 4. Construir la lectura y hacerla explicable

- Mostrar formula o componentes cuando el numero pueda generar dudas.
- Probar filtros por sucursal, periodo y rubro.
- Verificar que administracion pueda explicar el valor final sin leer codigo.
- Si la vista compara objetivo vs real, aclarar la base de comparacion.

### 5. Cerrar con disciplina de metricas

Para cada KPI dejar claro:
- nombre funcional
- formula
- base temporal
- unidad
- filtros permitidos
- exclusiones conocidas

## Decision rules

- Situacion financiera: foco en saldos y pendientes puntuales.
- Situacion economica: foco en comparabilidad por periodo.
- Rentabilidad: ingresos del periodo contra costos y deuda imputables a ese mismo periodo.
- Objetivo porcentual por rubro: parametro de control, no dato libre del reporte.
- Si el dato no puede ubicarse en periodo y rubro, no sirve para control de gestion serio.

## Invariantes clave

- La situacion economica trabaja por periodo y comparabilidad.
- Un rubro debe significar lo mismo en gastos, deuda y control.
- La rentabilidad no debe mezclar realizado con proyectado sin aclaracion.
- Una deuda sin periodo o sin rubro consistente degrada la lectura economica.
- Una comparacion entre sucursales exige misma base temporal y misma taxonomia.

## Stop-ship

- usar ventas de una sucursal con gastos consolidados globales
- usar deuda pendiente total para medir un periodo corto
- mostrar porcentaje por rubro sin definir base
- hablar de rentabilidad sin poder explicar formula
- comparar sucursales con cortes temporales distintos

## Archivos a mirar primero

- `docs/epics/EP-06-control-de-gestion-y-alertas.md`
- `docs/epics/EP-07-impuestos-planes-y-autorizaciones.md`
- `docs/epics/EP-11-rentabilidad-y-situacion-economica.md`
- `cashops/services.py`
- `treasury/models.py`
- `treasury/services.py`

## Salida esperada

- Goal
- Periodo y alcance
- Datos base y derivados
- Formula o criterio de calculo
- Riesgos de comparabilidad
- Tests o validaciones a correr
- Riesgo residual si algun rubro o formula sigue indefinido

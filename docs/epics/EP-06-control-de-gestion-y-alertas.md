# EP-06 Control de Gestion y Alertas

## Objetivo

Replicar la matriz mensual de resultado operativo y transformar el control "a fin de mes" en alertas y lectura diaria.

## Incluye

- matriz diaria mensual
- porcentajes objetivo por rubro
- alertas de desvio
- dashboard gerencial
- seguimiento de diferencias y faltantes

## No incluye todavia

- lectura financiera consolidada de efectivo, banco y acreditaciones ya cubierta en `EP-10`
- rentabilidad economica con deuda periodificada y comparacion contra ventas ya cubierta en `EP-11`
- autorizaciones especiales, impuestos y planes de pago de `EP-07`

## Reglas de negocio

- los porcentajes de rubro son parametros de control, no texto de planilla
- los gastos deben imputarse al rubro correcto
- el sistema debe avisar antes del cierre mensual, no despues

## User Stories

### [x] US-6.1 Matriz diaria de ingresos y egresos

Como administracion
Quiero una vista mensual por dia
Para reemplazar la planilla de control diario

Criterios:
- ingresos por canal visibles por dia
- egresos por rubro visibles por dia
- total diario y saldo o resultado del dia
- filtro por periodo y por sucursal cuando aplique
- la matriz se deriva de movimientos persistidos, no de celdas editables

### [x] US-6.2 Parametros de porcentaje por rubro

Como administracion
Quiero definir porcentajes objetivo
Para medir desvio por categoria

Criterios:
- porcentaje objetivo por rubro
- alcance global o por sucursal
- vigencia por periodo cuando aplique
- activacion y desactivacion con historial minimo

### [x] US-6.3 Alertas por desvio de rubro

Como administracion
Quiero alertas automaticas
Para detectar cuando un rubro se va de rango

Criterios:
- umbral amarillo
- umbral rojo
- fecha, periodo y alcance visibles
- el desvio se calcula contra el objetivo vigente del mismo rubro y base temporal

### [x] US-6.4 Alertas por deuda y vencimientos

Como administracion
Quiero ver lo que vence hoy, en breve o ya vencio
Para priorizar pagos

Criterios:
- vence hoy
- vence en 3 dias
- vencida
- filtro general y por sucursal cuando el dato exista

### [x] US-6.5 Dashboard gerencial

Como administracion
Quiero un panel rapido
Para ver deuda, pagos, acreditaciones, diferencias y disponibilidades

Criterios:
- deuda pendiente
- vencido
- pagado en periodo
- acreditado o pendiente relevante
- efectivo disponible
- banco disponible
- lectura resumida por periodo y opcionalmente por sucursal

### [x] US-6.6 Exportacion y cierre mensual

Como administracion
Quiero exportar el consolidado mensual
Para auditoria interna o trabajo externo

Criterios:
- exportacion por periodo
- resumen por rubro y por canal
- detalle trazable que explique cada total

### [x] US-6.7 Seguimiento de diferencias y faltantes

Como administracion
Quiero registrar y seguir diferencias operativas o faltantes
Para no resolver desfasajes por fuera del sistema ni perder responsables

Criterios:
- diferencia visible con fecha, sucursal y responsable
- clasificacion minima entre faltante, sobrante o pendiente de aclaracion
- justificacion y estado de seguimiento
- el caso puede quedar resuelto sin borrar la diferencia original

## Dependencias

- EP-03, EP-04 y EP-05 con datos consistentes

## Orden tecnico sugerido

1. construir la matriz diaria mensual desde datos operativos consistentes
2. parametrizar objetivos por rubro y vigencia
3. calcular alertas por desvio de rubro
4. registrar seguimiento de diferencias y faltantes
5. consolidar dashboard gerencial
6. cerrar exportacion mensual trazable

## Criterio de cierre

- la hoja matricial mensual deja de mantenerse a mano
- el encargado y administracion pueden guiarse por alertas, como pidio el relevo

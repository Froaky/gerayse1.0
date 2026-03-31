# EP-06 Control de Gestion y Alertas

## Objetivo

Replicar la matriz mensual de resultado operativo y transformar el control "a fin de mes" en alertas y lectura diaria.

## Incluye

- matriz diaria mensual
- porcentajes objetivo por rubro
- alertas de desvio
- dashboard gerencial
- seguimiento de diferencias y faltantes

## Reglas de negocio

- los porcentajes de rubro son parametros de control, no texto de planilla
- los gastos deben imputarse al rubro correcto
- el sistema debe avisar antes del cierre mensual, no despues

## User Stories

### US-6.1 Matriz diaria de ingresos y egresos

Como administracion
Quiero una vista mensual por dia
Para reemplazar la planilla de control diario

Criterios:
- ingresos por canal
- egresos por rubro
- total diario
- saldo o resultado del dia

### US-6.2 Parametros de porcentaje por rubro

Como administracion
Quiero definir porcentajes objetivo
Para medir desvio por categoria

Criterios:
- porcentaje maximo por rubro
- alcance global o por sucursal si hace falta
- activacion/desactivacion

### US-6.3 Alertas por desvio de rubro

Como administracion
Quiero alertas automaticas
Para detectar cuando un rubro se va de rango

Criterios:
- umbral amarillo
- umbral rojo
- fecha y alcance

### US-6.4 Alertas por deuda y vencimientos

Como administracion
Quiero ver lo que vence hoy, en breve o ya vencio
Para priorizar pagos

Criterios:
- vence hoy
- vence en 3 dias
- vencida

### US-6.5 Dashboard gerencial

Como administracion
Quiero un panel rapido
Para ver deuda, pagos, acreditaciones, diferencias y disponibilidades

Criterios:
- deuda pendiente
- vencido
- pagado en periodo
- acreditado
- efectivo disponible
- banco disponible

### US-6.6 Exportacion y cierre mensual

Como administracion
Quiero exportar el consolidado mensual
Para auditoria interna o trabajo externo

Criterios:
- export por periodo
- resumen por rubro y por canal
- detalle trazable

## Dependencias

- EP-03, EP-04 y EP-05 con datos consistentes

## Criterio de cierre

- la hoja matricial mensual deja de mantenerse a mano
- el encargado y administracion pueden guiarse por alertas, como pidio el relevo

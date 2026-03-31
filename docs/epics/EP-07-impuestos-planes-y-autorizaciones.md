# EP-07 Impuestos, Planes y Autorizaciones

## Objetivo

Cubrir las obligaciones especiales que en los Excel aparecen como impuestos, embargos, planes de pago, sueldos extraordinarios y adelantos autorizados.

## Incluye

- obligaciones impositivas recurrentes
- planes de pago y cuotas
- intereses financieros y resarcitorios
- requerimientos pendientes
- autorizaciones previas
- auditoria de aprobaciones

## Reglas de negocio

- un impuesto no puede quedar como texto libre si genera vencimiento o saldo
- un plan de pago debe poder mostrar capital, intereses y cuotas
- un adelanto o gasto excepcional requiere autorizacion si asi lo define el negocio
- no debe existir pago sin comprobante o sustento

## User Stories

### US-7.1 Obligaciones impositivas recurrentes

Como administracion
Quiero registrar obligaciones fiscales periodicas
Para controlar AFIP, rentas, 931, sindicato y similares

Criterios:
- tipo de impuesto
- periodo fiscal
- vencimiento
- importe
- estado

### US-7.2 Planes de pago

Como administracion
Quiero registrar planes con cuotas
Para controlar deuda refinanciada

Criterios:
- plan
- cuota
- capital
- interes financiero
- interes resarcitorio
- vencimiento

### US-7.3 Requerimientos pendientes

Como administracion
Quiero registrar compromisos no standard
Para no perder pendientes que hoy estan en hojas auxiliares

Criterios:
- fecha
- concepto
- monto estimado
- prioridad
- estado

### US-7.4 Adelantos con autorizacion

Como administracion
Quiero exigir autorizacion previa en adelantos
Para evitar salidas no controladas

Criterios:
- solicitante
- autorizador
- motivo
- fecha
- estado de aprobacion

### US-7.5 Auditoria de aprobaciones

Como administracion
Quiero ver quien autorizo y cuando
Para sostener control interno

Criterios:
- usuario
- timestamp
- motivo o comentario

### US-7.6 Bloqueos de control

Como negocio
Quiero que ciertas cosas no pasen nunca
Para respetar reglas explicitadas en el relevo

Criterios:
- no caja sin responsable
- no pago sin comprobante o referencia
- no diferencia sin justificar
- no deuda pagada sin trazabilidad

## Dependencias

- EP-03 base de deuda y pagos
- EP-04 banco y acreditaciones

## Criterio de cierre

- impuestos y planes dejan de vivir fuera del sistema
- autorizaciones especiales dejan de depender de mensajes o memoria

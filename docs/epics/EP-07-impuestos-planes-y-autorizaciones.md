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

## No incluye todavia

- liquidacion integral de sueldos
- conciliacion bancaria avanzada de pagos especiales
- contabilidad fiscal formal
- workflow documental externo con organismos

## Reglas de negocio

- un impuesto no puede quedar como texto libre si genera vencimiento o saldo
- un plan de pago debe poder mostrar capital, intereses y cuotas
- un adelanto o gasto excepcional requiere autorizacion si asi lo define el negocio
- no debe existir pago sin comprobante o sustento

## User Stories

### [ ] US-7.1 Obligaciones impositivas recurrentes

Como administracion
Quiero registrar obligaciones fiscales periodicas
Para controlar AFIP, rentas, 931, sindicato y similares

Criterios:
- tipo de impuesto u organismo
- periodo fiscal obligatorio
- vencimiento
- importe
- estado visible y filtrable

### [ ] US-7.2 Planes de pago

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
- estado por cuota y lectura del saldo pendiente del plan

### [ ] US-7.3 Requerimientos pendientes

Como administracion
Quiero registrar compromisos no standard
Para no perder pendientes que hoy estan en hojas auxiliares

Criterios:
- fecha
- concepto
- monto estimado
- prioridad
- estado
- responsable o sector cuando aplique

### [ ] US-7.4 Adelantos con autorizacion

Como administracion
Quiero exigir autorizacion previa en adelantos
Para evitar salidas no controladas

Criterios:
- solicitante
- autorizador
- motivo
- fecha
- estado de aprobacion
- el adelanto no puede ejecutarse sin aprobacion cuando la regla lo exige

### [ ] US-7.5 Auditoria de aprobaciones

Como administracion
Quiero ver quien autorizo y cuando
Para sostener control interno

Criterios:
- usuario
- timestamp
- motivo o comentario
- visibilidad desde el detalle del compromiso o aprobacion

### [ ] US-7.6 Bloqueos de control

Como negocio
Quiero que ciertas cosas no pasen nunca
Para respetar reglas explicitadas en el relevo

Criterios:
- no caja sin responsable
- no pago sin comprobante o referencia
- no diferencia sin justificar
- no deuda pagada sin trazabilidad

### [ ] US-7.7 Embargos y retenciones judiciales

Como administracion
Quiero registrar embargos o retenciones judiciales
Para no mezclarlos con impuestos generales ni perder vencimiento, expediente o estado

Criterios:
- tipo de medida u organismo
- expediente, referencia o sustento obligatorio
- monto fijo o porcentaje cuando corresponda
- fecha de vigencia o vencimiento
- estado visible y trazable

### [ ] US-7.8 Sueldos extraordinarios y pagos excepcionales

Como administracion
Quiero registrar sueldos extraordinarios u otros pagos excepcionales
Para tratarlos con aprobacion y trazabilidad sin forzarlos como deuda comun de proveedor

Criterios:
- tipo de compromiso extraordinario
- beneficiario o concepto obligatorio
- monto y fecha esperada
- aprobacion previa cuando corresponda
- lectura separada de la deuda operativa comun

## Dependencias

- EP-03 base de deuda y pagos
- EP-04 banco y acreditaciones

## Orden tecnico sugerido

1. formalizar obligaciones impositivas recurrentes
2. modelar planes de pago con cuotas e intereses
3. registrar requerimientos pendientes, embargos y otros compromisos especiales
4. exigir autorizacion previa en adelantos y pagos excepcionales
5. exponer auditoria de aprobaciones y bloqueos de control

## Criterio de cierre

- impuestos y planes dejan de vivir fuera del sistema
- autorizaciones especiales dejan de depender de mensajes o memoria

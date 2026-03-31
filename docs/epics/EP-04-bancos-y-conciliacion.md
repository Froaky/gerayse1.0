# EP-04 Bancos y Conciliacion

## Objetivo

Llevar al sistema lo que hoy se controla en el lado `MAPOGO - BANCO` y en los cierres de lote POS.

## Incluye

- movimientos bancarios administrativos
- acreditaciones por tarjeta
- lotes POS
- retenciones y descuentos
- relacion pago tesoreria vs banco
- conciliacion simple de ventas y acreditaciones

## Reglas de negocio

- una acreditacion bancaria no es lo mismo que una venta
- toda acreditacion debe poder vincularse al menos a un canal u operador
- los descuentos bancarios deben quedar separados del neto acreditado
- un pago de tesoreria con impacto bancario debe tener reflejo bancario trazable

## User Stories

### [x] US-4.1 Registro de movimiento bancario

- [x] Cuenta bancaria obligatoria
- [x] Tipo `debito` o `credito`
- [x] Fecha, monto y concepto
- [x] Referencia y observaciones opcionales

### [x] US-4.2 Registro de acreditacion por tarjeta

- [x] Cuenta bancaria destino
- [x] Fecha de acreditacion
- [x] Monto acreditado
- [x] Operador/canal
- [x] Referencia de lote o liquidacion

### [x] US-4.3 Registro de lote POS

- [x] Fecha de lote
- [x] Terminal u operador opcional
- [x] Total del lote
- [x] Observaciones

### [x] US-4.4 Retenciones y descuentos bancarios

- [x] Tipo de descuento
- [x] Monto
- [x] Acreditacion asociada
- [x] Descripcion

### [x] US-4.5 Relacion de pagos con banco

- [x] Transferencia vinculable a debito bancario
- [x] Cheque/ECHEQ con estado bancario
- [x] Trazabilidad bidireccional

### [x] US-4.6 Conciliacion simple tarjeta vs banco

- [x] Total vendido por tarjeta
- [x] Total lote POS
- [x] Total acreditado
- [x] Diferencia visible
- [x] Filtros por fecha y cuenta bancaria

### [x] US-4.7 Dashboard bancario

- [x] Acreditaciones del periodo
- [x] Debitos bancarios relevantes
- [x] Descuentos y retenciones
- [x] Diferencias de conciliacion

## Dependencias

- EP-03 cerrada
- ventas por tarjeta correctamente separadas de caja

## Criterio de cierre

- la parte bancaria del Excel de disponibilidades debe reconstruirse sin carga paralela
- una acreditacion y sus descuentos quedan explicados desde el sistema
- ya se puede detectar diferencia entre venta, lote y acreditacion

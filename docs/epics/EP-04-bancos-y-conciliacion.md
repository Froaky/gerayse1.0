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

## No incluye todavia

- integracion bancaria real en linea
- importacion masiva de extractos
- conciliacion automatica contra todos los operadores y marcas
- conciliacion bancaria automatica de cualquier tipo sin decision explicita posterior
- cierre contable formal

## Reglas de negocio

- una acreditacion bancaria no es lo mismo que una venta
- toda acreditacion debe poder vincularse al menos a un canal u operador
- los descuentos bancarios deben quedar separados del neto acreditado
- un pago de tesoreria con impacto bancario debe tener reflejo bancario trazable
- hasta nueva decision de negocio, la conciliacion bancaria se opera de forma manual asistida por el sistema y no por matching automatico

## User Stories

### [x] US-4.1 Registro de movimiento bancario

Como tesoreria
Quiero registrar movimientos bancarios con tipificacion minima
Para explicar ingresos y egresos reales por cuenta sin depender de textos ambiguos

- [x] Cuenta bancaria obligatoria
- [x] Tipo `debito` o `credito`
- [x] Fecha, monto y concepto
- [x] Referencia y observaciones opcionales
- [x] El movimiento queda disponible para lectura por cuenta y periodo

### [x] US-4.2 Registro de acreditacion por tarjeta

Como tesoreria
Quiero registrar acreditaciones de tarjeta separadas de la venta
Para explicar que ingreso al banco y que sigue pendiente de acreditar

- [x] Cuenta bancaria destino
- [x] Fecha de acreditacion
- [x] Monto acreditado
- [x] Operador/canal
- [x] Referencia de lote o liquidacion
- [x] La acreditacion puede leerse por periodo y por cuenta bancaria

### [x] US-4.3 Registro de lote POS

Como tesoreria
Quiero registrar lotes POS
Para relacionar ventas digitales con futuras acreditaciones y descuentos

- [x] Fecha de lote
- [x] Terminal u operador opcional
- [x] Total del lote
- [x] Observaciones
- [x] El lote puede vincularse a su acreditacion cuando el dato exista

### [x] US-4.4 Retenciones y descuentos bancarios

Como tesoreria
Quiero separar descuentos y retenciones del neto acreditado
Para no confundir comisiones o impuestos con dinero efectivamente disponible

- [x] Tipo de descuento
- [x] Monto
- [x] Acreditacion asociada
- [x] Descripcion
- [x] El descuento no se mezcla con el neto acreditado en la lectura financiera

### [x] US-4.5 Relacion de pagos con banco

Como administracion
Quiero vincular pagos administrativos con su reflejo bancario
Para auditar que un egreso de tesoreria realmente impacto en la cuenta correcta

- [x] Transferencia vinculable a debito bancario
- [x] Cheque/ECHEQ con estado bancario
- [x] Trazabilidad bidireccional
- [x] La vinculacion no redefine por si sola el estado de deuda

### [x] US-4.6 Conciliacion simple tarjeta vs banco

Como administracion
Quiero comparar venta digital, lote y acreditacion
Para detectar diferencias sin reconstruir la planilla bancaria a mano

- [x] Total vendido por tarjeta
- [x] Total lote POS
- [x] Total acreditado
- [x] Diferencia visible
- [x] Filtros por fecha y cuenta bancaria
- [x] La diferencia se explica sin mezclar ventas con dinero ya acreditado

### [x] US-4.7 Dashboard bancario

Como administracion
Quiero una vista bancaria resumida del periodo
Para controlar acreditaciones, debitos y diferencias desde una sola lectura

- [x] Acreditaciones del periodo
- [x] Debitos bancarios relevantes
- [x] Descuentos y retenciones
- [x] Diferencias de conciliacion

## Dependencias

- EP-03 cerrada
- ventas por tarjeta correctamente separadas de caja

## Orden tecnico sugerido

1. registrar movimientos bancarios y cuentas alcanzadas
2. registrar acreditaciones y lotes POS
3. separar descuentos y retenciones del neto acreditado
4. vincular pagos de tesoreria con debitos bancarios
5. resolver conciliacion simple de venta, lote y banco
6. cerrar dashboard bancario del periodo

## Criterio de cierre

- la parte bancaria del Excel de disponibilidades debe reconstruirse sin carga paralela
- una acreditacion y sus descuentos quedan explicados desde el sistema
- ya se puede detectar diferencia entre venta, lote y acreditacion

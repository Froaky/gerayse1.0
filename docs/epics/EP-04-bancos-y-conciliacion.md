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
- carga inicial auditada de saldo bancario por cuenta
- cuenta bancaria asociada a una empresa duena, no solo a una sucursal puntual

## No incluye todavia

- integracion bancaria real en linea
- importacion masiva de extractos
- conciliacion automatica contra todos los operadores y marcas
- conciliacion bancaria automatica de cualquier tipo sin decision explicita posterior
- cierre contable formal
- reparto o prorrateo de acreditaciones entre las sucursales de una misma empresa

## Reglas de negocio

- una acreditacion bancaria no es lo mismo que una venta
- toda acreditacion debe poder vincularse al menos a un canal u operador
- los descuentos bancarios deben quedar separados del neto acreditado
- un pago de tesoreria con impacto bancario debe tener reflejo bancario trazable
- el saldo inicial bancario se carga por cuenta bancaria y debe quedar auditado; no reemplaza movimientos reales posteriores
- hasta nueva decision de negocio, la conciliacion bancaria se opera de forma manual asistida por el sistema y no por matching automatico
- una cuenta bancaria pertenece a una empresa; puede ademas estar asociada a una sucursal puntual cuando la cuenta es exclusiva de un solo local, pero no lo exige
- cuando una empresa opera varias sucursales sobre una unica cuenta bancaria, el ingreso de esa cuenta (acreditaciones y creditos) se lee como fondo comun de la empresa, no como reparto por sucursal

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

### [x] US-4.8 Saldo inicial bancario por cuenta

Como tesoreria
Quiero cargar o ajustar el saldo inicial de una cuenta bancaria desde la parte de banco
Para empezar la lectura bancaria desde un saldo real sin inventar movimientos manuales

Criterios:
- [x] cada cuenta bancaria permite registrar un saldo inicial con fecha de referencia
- [x] la carga exige cuenta bancaria, fecha, importe, usuario y motivo
- [x] el saldo inicial queda visible en el libro o dashboard bancario como punto de partida del periodo
- [x] el saldo inicial no se mezcla con acreditaciones, transferencias ni egresos reales
- [x] si se corrige el saldo inicial, queda auditado valor anterior, valor nuevo, usuario, fecha y motivo
- [x] el sistema evita que existan saldos iniciales ambiguos para la misma cuenta y fecha de referencia
- [x] los totales bancarios posteriores se calculan desde saldo inicial mas movimientos reales del periodo

### [x] US-4.9 Cuenta bancaria con empresa propietaria

Como tesoreria
Quiero que una cuenta bancaria pueda asociarse a una empresa ademas de, opcionalmente, una sucursal
Para reflejar que la cuenta de `ARMADI SRL` es comun a todas sus sucursales y la de `MAPOGO SRL` es propia de `Vivre`, sin inventar un reparto por sucursal que no existe en la operacion real

Criterios:
- [x] `CuentaBancaria` tiene un campo `empresa` (FK), independiente del campo `sucursal` que ya existe
- [x] la carga de una cuenta bancaria exige empresa propietaria (si se elige sucursal, la empresa se deriva de esa sucursal)
- [x] migracion compatible con backfill de datos existentes: hereda la empresa de la sucursal cuando existe; si la cuenta no tiene sucursal, infiere la empresa solo cuando todos los egresos imputados de la cuenta apuntan a sucursales de una unica empresa; los casos ambiguos quedan sin empresa y se completan desde la edicion de la cuenta, sin tocar movimientos historicos
- [x] listados, formularios, filtros y totales de cuentas bancarias y movimientos bancarios respetan la empresa activa seleccionada, igual que el resto del sistema (las cuentas legacy sin empresa asignada siguen visibles hasta que administracion las complete)
- [x] una cuenta de una empresa con varias sucursales no exige elegir una sucursal puntual para poder operar
- [x] las acreditaciones y creditos de una cuenta bancaria de empresa siguen leyendose como ingreso consolidado de esa empresa, sin repartirse ni prorratearse entre sucursales (mantiene la regla ya vigente de `US-10.11`)
- [x] los egresos bancarios de esa cuenta pueden seguir imputandose a una sucursal puntual mediante `sucursal_gasto`, porque la regla de imputacion por sucursal aplica a gastos, no a la propiedad de la cuenta ni a los ingresos
- [x] los tests cubren que una cuenta bancaria y sus movimientos no aparecen si la empresa duena no esta en el contexto activo, que el acceso directo por URL devuelve 404, y que el ingreso consolidado no cambia al filtrar por una sucursal de esa empresa

Nota de alcance: los formularios de pagos, lotes POS y acreditaciones siguen ofreciendo todas las cuentas activas (comportamiento previo); el scoping por empresa activa se aplico a listados, totales, y a los formularios de cuenta bancaria. Scopear los selectores de cuenta del resto de los formularios queda como mejora incremental.

## Dependencias

- EP-03 cerrada
- ventas por tarjeta correctamente separadas de caja
- regla de aislamiento por empresa vigente (`EP-12`)

## Orden tecnico sugerido

1. registrar movimientos bancarios y cuentas alcanzadas
2. registrar acreditaciones y lotes POS
3. separar descuentos y retenciones del neto acreditado
4. vincular pagos de tesoreria con debitos bancarios
5. resolver conciliacion simple de venta, lote y banco
6. cerrar dashboard bancario del periodo
7. agregar carga inicial auditada de saldo bancario por cuenta
8. asociar cuenta bancaria a empresa propietaria con backfill auditado

## Criterio de cierre

- la parte bancaria del Excel de disponibilidades debe reconstruirse sin carga paralela
- una acreditacion y sus descuentos quedan explicados desde el sistema
- ya se puede detectar diferencia entre venta, lote y acreditacion
- tesoreria puede iniciar una cuenta bancaria con saldo real auditado sin cargar un movimiento ficticio
- una cuenta bancaria compartida por varias sucursales de una misma empresa queda modelada como cuenta de empresa, sin depender de una sucursal ficticia

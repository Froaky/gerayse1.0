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
- reparto de una transferencia entre varias facturas, incluso de proveedores distintos

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
- una transferencia puede pagar varias facturas, incluso de proveedores distintos: la suma de los pagos vinculados no puede superar el importe del movimiento bancario
- un movimiento bancario sigue siendo un solo hecho del extracto aunque pague varias deudas; pagar N facturas no genera N debitos
- el medio de pago del pago de tesoreria es la fuente de verdad del tipo financiero del debito vinculado: la clase del movimiento se deriva de el y no se puede cargar distinto de un lado y del otro
- equivocarse de instrumento (cargar transferencia cuando fue cheque) es un error de tipificacion, no de plata: se corrige sobre el egreso ya registrado, sin anular pagos ni volver a cargar la deuda

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

### [x] US-4.10 Una transferencia repartida entre varias facturas

Como administracion
Quiero aplicar una sola transferencia a varias facturas, incluso de proveedores distintos
Para registrar el pago semanal de cuenta corriente sin cargar un debito por cada factura

Contexto: el pago semanal de CTA CTE sale como una transferencia sola que cubre
6 facturas de proveedores distintos. Hoy una transferencia paga UNA factura por
su importe exacto, asi que ese caso obliga a cargar debitos que no existen en el
extracto.

Criterios:
- desde una transferencia ya cargada se pueden elegir varias facturas impagas y asignarle a cada una su importe
- se pueden elegir facturas de proveedores distintos en la misma operacion
- el listado para elegir muestra todas las facturas impagas del alcance, sin filtrar por el importe de la transferencia
- el listado se puede filtrar por proveedor, porque la lista completa es larga
- la suma asignada no puede superar el importe de la transferencia, y en pantalla se ve cuanto queda por asignar mientras se carga
- el importe asignado a una factura no puede superar su saldo pendiente
- si la suma asignada es menor al importe de la transferencia, queda visible que parte de la transferencia todavia no esta asignada a ninguna deuda
- cada factura recibe su propio pago por el importe asignado, y cada pago queda trazable a la misma transferencia
- la transferencia no genera un debito nuevo por cada factura: sigue siendo un solo movimiento del extracto
- desde la transferencia se ve que facturas pago y por cuanto; desde cada factura se llega a la transferencia
- anular uno de los pagos devuelve el saldo a esa factura y libera ese importe de la transferencia, sin tocar los otros pagos ni el movimiento bancario
- el reparto respeta empresa activa: no se paga una factura de una empresa con una transferencia de la cuenta de otra
- una transferencia ya repartida no acepta un pago nuevo que haga pasar su importe total
- una transferencia con referencia cargada se puede repartir igual: la referencia del instrumento se sufija por linea (`TRF-77 (1/2)`) porque un pago no puede repetir referencia para la misma cuenta y medio de pago. Corregido 2026-08-13; antes el reparto de una transferencia con referencia fallaba entero y solo funcionaba sin referencia

### [x] US-4.11 Corregir con que se pago una deuda ya registrada

Como administracion
Quiero corregir el tipo financiero de un egreso que ya pague
Para no tener que anular los pagos y cargar todo de nuevo cuando me equivoco de instrumento

Contexto: lo reporto la usuaria de tesoreria. Cargo el egreso como "egreso por
transferencia" y era "egreso por cheque". El boton Editar del detalle del
movimiento desaparece apenas queda vinculado a un pago -y esta bien que
desaparezca, porque editar de verdad cambia monto, fecha y cuenta-, asi que la
unica salida era borrar todo y empezar de cero.

Criterios:
- desde el detalle de un egreso bancario vigente que paga facturas hay una accion para corregir el tipo financiero
- las opciones son las tres que tienen reflejo bancario: cheque, ECHEQ y transferencia a terceros
- la correccion cambia a la vez el tipo financiero del movimiento y el medio de pago de todas las facturas que paga, para que no queden diciendo cosas distintas
- se puede corregir la referencia del instrumento (nro de cheque u operacion); cheque y ECHEQ la exigen
- si el egreso paga varias facturas, la misma referencia se sufija por linea (`CH-1001 (1/3)`), igual que en el pago por proveedor
- si la persona no toca la referencia, cada pago conserva la propia (puede tener una distinta si se cargo a mano y despues se vinculo)
- la correccion NO cambia importe, fecha, cuenta bancaria, deudas pagadas ni saldos: ninguna lectura financiera o economica se mueve
- una transferencia que paga facturas de proveedores distintos no se puede tipificar como cheque ni ECHEQ, porque esos instrumentos tienen un unico beneficiario
- volver de cheque a transferencia limpia la fecha diferida, que la transferencia no admite
- el movimiento deja registrado en observaciones que la tipificacion se corrigio, ademas de usuario y fecha de actualizacion
- respeta empresa activa: el acceso directo por URL a un egreso de otra empresa devuelve 404
- un egreso eliminado, un credito o un movimiento sin pagos no entran por esta pantalla

Nota tecnica: para que esto fuera posible se corrigio una invariante de
`PagoTesoreria.clean()`. Las guardas "la deuda esta anulada" y "la deuda ya esta
cancelada" aplicaban a cualquier guardado y no solo al alta, asi que un pago que
dejaba la deuda PAGADA -el caso normal- quedaba congelado para siempre. Ahora
aplican solo al alta de un pago nuevo; el control de sobrepago, que ya excluia el
propio pago del total, se mantiene siempre. Efecto colateral querido: "Vincular a
pago" tambien funciona sobre deudas ya canceladas (era una limitacion documentada
en `tests_bank_impact`).

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

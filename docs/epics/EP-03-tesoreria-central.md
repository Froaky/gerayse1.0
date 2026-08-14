# EP-03 Tesoreria Central

## Objetivo

Construir el nucleo de tesoreria para dejar de registrar deuda y pagos en planillas sueltas.

## Incluye

- proveedores
- categorias de deuda
- cuentas por pagar
- cuentas bancarias
- pagos por transferencia, cheque y ECHEQ
- pagos parciales
- anulacion controlada
- historial por proveedor
- auditoria minima de tesoreria

## No incluye todavia

- conciliacion avanzada
- importacion bancaria masiva
- contabilidad general
- forecast financiero

## Reglas de negocio

- una cuenta por pagar nunca puede quedar con saldo negativo
- una deuda puede tener multiples pagos
- un pago no se borra, se anula
- no se paga una deuda anulada o ya pagada
- no se usa una cuenta bancaria inactiva
- todo pago debe tener usuario, fecha y referencia minima

## User Stories

### [x] US-3.1 Alta de proveedores

Como administrador
Quiero registrar proveedores
Para centralizar a quien se le debe y a quien se le paga

Criterios:
- alta, edicion y baja logica
- razon social obligatoria
- identificador fiscal opcional pero unico si se informa
- contacto, telefono, email, alias y CBU opcionales
- busqueda por nombre o identificador

### [x] US-3.2 Categorias de deuda

Como administrador
Quiero categorizar obligaciones
Para separar proveedores, servicios, impuestos y otros compromisos

Criterios:
- alta y baja logica de categorias
- categorias activas e inactivas
- filtros por categoria

### [x] US-3.3 Registro de cuenta por pagar

Como administrador
Quiero registrar una obligacion pendiente
Para saber cuanto debo, a quien y cuando vence

Criterios:
- proveedor obligatorio
- concepto obligatorio
- importe total y saldo pendiente
- fecha de emision y vencimiento
- referencia de comprobante opcional
- estado inicial `pendiente`

### [x] US-3.4 Consulta de deuda pendiente

Como administrador
Quiero ver todas las obligaciones abiertas
Para priorizar pagos y detectar vencidos

Criterios:
- filtros por proveedor, categoria, fecha y estado
- orden por vencimiento
- deuda vencida destacada
- saldo pendiente visible

### [x] US-3.5 Alta de cuentas bancarias

Como administrador
Quiero registrar cuentas bancarias de la empresa
Para asociar pagos y movimientos a cuentas reales

Criterios:
- banco, tipo y numero de cuenta obligatorios
- alias/CBU opcionales
- activacion/desactivacion

### [x] US-3.6 Registro de pago por transferencia

Como tesoreria
Quiero registrar una transferencia
Para dejar trazabilidad del egreso administrativo

Criterios:
- seleccionar deuda
- seleccionar cuenta bancaria origen
- monto y fecha
- referencia/comprobante opcional
- recalc saldo pendiente

### [x] US-3.7 Registro de pago por cheque

Como tesoreria
Quiero registrar pagos por cheque
Para controlar instrumentos no inmediatos

Criterios:
- numero o referencia obligatoria
- fecha de pago
- fecha diferida opcional
- monto y cuenta bancaria

### [x] US-3.8 Registro de pago por ECHEQ

Como tesoreria
Quiero registrar pagos por ECHEQ
Para tener trazabilidad de pagos diferidos electronicos

Criterios:
- referencia obligatoria
- fecha y monto
- cuenta bancaria asociada

### [x] US-3.9 Pagos parciales

Como administrador
Quiero pagar una deuda en varias partes
Para reflejar la operatoria real

Criterios:
- multiples pagos por obligacion
- estado `pendiente`, `parcial`, `pagada`
- saldo pendiente recalculado automaticamente

### [x] US-3.10 Anulacion controlada de pago

Como administrador
Quiero anular un pago incorrecto
Para conservar historial sin borrar dinero

Criterios:
- motivo obligatorio
- usuario y fecha de anulacion
- recomputo de saldo de la deuda

### [x] US-3.11 Historial por proveedor

Como administrador
Quiero ver deuda y pagos por proveedor
Para auditar relacion comercial y deuda historica

Criterios:
- listado de obligaciones
- pagos realizados
- saldo pendiente e historico

### [x] US-3.12 Auditoria de tesoreria

Como administrador
Quiero ver quien hizo cada accion relevante
Para tener control interno

Criterios:
- usuario creador/modificador/anulador
- timestamps
- visibilidad desde detalle de deuda y pago

### [x] US-3.13 Sucursal y caja de origen visibles en la deuda

Como tesorera
Quiero ver de que sucursal y de que caja salio cada factura
Para elegir la correcta cuando el proveedor tiene varias por el mismo importe

Contexto real: en produccion hay 233 deudas abiertas donde el mismo proveedor
tiene varias facturas del mismo importe en sucursales distintas. El peor caso es
un proveedor con 33 facturas de $27.500 en 5 sucursales. Ninguna de las cuatro
pantallas de deuda mostraba la sucursal, asi que las lineas eran identicas entre
si. De ahi salieron 10 pagos dobles hechos dentro de un mismo lote.

Criterios:
- la sucursal se muestra en el listado de cuentas por pagar, en el detalle de la
  deuda, en "pagar por proveedor" y al repartir una transferencia
- la sucursal se muestra con codigo y nombre, porque tesoreria lleva la cuenta
  corriente semanal por codigo de sucursal en su planilla
- las deudas nacidas en una caja muestran la caja y su fecha operativa; dos
  facturas del mismo proveedor, sucursal e importe solo se separan por ahi
- las deudas legacy sin sucursal siguen funcionando y muestran "Sin sucursal"
- agregar el dato no puede costar una consulta por fila: el listado trae mas de
  mil deudas abiertas

Nota tecnica: las etiquetas viven en dos propiedades derivadas de
`CuentaPorPagar` (`sucursal_label` y `origen_label`), no en cada template, para
que las cuatro pantallas digan lo mismo. Es el mismo patron que ya usaban
`estado_visible` y `urgency_label`. El costo de lectura se cubre con
`select_related("sucursal", "caja_origen")` en los cuatro querysets y con un test
que compara la cantidad de consultas con 1 y con 7 deudas.

### [x] US-3.14 Filtrar la cuenta corriente por proveedor, sucursal y lapso

Como tesorera
Quiero filtrar las facturas impagas por proveedor, sucursal y rango de fechas y
ver el subtotal de lo filtrado
Para pagar la cuenta corriente de la semana y cuadrarla contra mi planilla

Contexto real: tesoreria no paga facturas sueltas. Recibe una planilla con una
fila por proveedor / sucursal / fecha y un total, y paga la cuenta corriente de
una semana. La pantalla de reparto tenia un unico filtro por proveedor y listaba
las 1.292 deudas abiertas en una sola pagina.

Criterios:
- filtros de proveedor, sucursal y lapso de fechas, combinables entre si
- el lapso corre sobre la fecha de factura, que es la que anota tesoreria
- se muestra el subtotal pendiente y la cantidad de lo filtrado
- los combos ofrecen el universo permitido, no el ya filtrado: elegir proveedor
  no puede hacer desaparecer sucursales del selector
- el POST conserva los filtros, para no tener que rearmarlos si hay un error
- un lapso invertido avisa y no filtra, en vez de devolver cero facturas
- una fecha invalida en la URL no rompe la pantalla

### [x] US-3.15 Aviso de posible duplicado al cargar la deuda

Como cajero
Quiero que el sistema me avise si esa factura ya parece estar cargada
Para no cargarla dos veces sin darme cuenta

Criterios:
- se compara proveedor + sucursal + fecha de factura + importe
- avisa y DEJA GUARDAR IGUAL: el segundo envio es "Guardar de todos modos".
  Dos facturas reales pueden coincidir en las cuatro cosas
- el aviso nombra la caja de origen de la que ya existe, que es lo unico que
  permite reconocerla
- si las dos tienen numero de comprobante cargado y es distinto, no avisa: ahi
  no hay duda, dentro de un proveedor el numero es unico por constraint
- mira la sucursal a la que se va a imputar la deuda, no la de la caja
- una deuda sin sucursal solo se compara contra otras sin sucursal

### [x] US-3.16 No pagar dos veces la misma factura en un lote

Como tesorera
Quiero que no me deje tildar dos lineas que son la misma factura
Para no pagarle dos veces al proveedor

Contexto real: los 10 pagos dobles de produccion ($2.013.126,48) salieron todos
dentro de un mismo lote, con referencias como "sistema (6/26)" y
"sistema (8/26)". La pantalla tenia la informacion para avisar y no avisaba.

Criterios:
- aplica en "pagar por proveedor" y al repartir una transferencia
- misma clave que el aviso de carga, y la misma excepcion por comprobante
- corta el envio y pide un tilde aparte ("Son facturas distintas"), no bloquea
  para siempre
- al volver con el aviso, las facturas ya tildadas siguen tildadas

### [x] US-3.17 Avisar el pago parcial y el doble conteo del egreso

Como tesorera
Quiero que me avise cuando la transferencia no alcanza para toda la factura, y
cuando un egreso administrativo va a contar el gasto dos veces
Para no dejar facturas pagadas a medias sin querer ni inflar la economica

Contexto real: en produccion aparecio una factura de $33.000 con el importe
precargado en $21.750, porque el sugerido se topea con lo que queda sin asignar
de la transferencia. Nada lo decia.

Criterios:
- la linea avisa cuanto quedaria debiendo si se paga con el sugerido
- no avisa cuando la transferencia alcanza para el saldo completo
- la pantalla de egreso administrativo aclara que no cancela ninguna deuda, y
  que si el gasto ya esta cargado como deuda queda contado dos veces
- el aviso del egreso linkea a "Pagar por proveedor", que es el camino correcto
- es un aviso, no un bloqueo: el egreso administrativo se usa de verdad para
  alquileres, sueldos e impuestos, que no se cargan como deuda

## Orden tecnico sugerido

1. proveedores
2. categorias
3. cuentas por pagar
4. cuentas bancarias
5. pagos
6. anulaciones
7. historial y auditoria visible

## Criterio de cierre

- la hoja `DEUDAS` del Excel debe poder reconstruirse desde base
- una deuda abierta ya no se mantiene por texto libre
- transferencias, cheques y ECHEQ quedan trazados
- saldo pendiente y estado cierran en todos los casos

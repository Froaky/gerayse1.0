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

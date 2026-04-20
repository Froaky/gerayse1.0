# Manual De Demo: Camino Feliz De Gerayse 1.0

## Objetivo

Este documento sirve para mostrar el sistema completo de manera simple, ordenada y entendible para una persona que nunca vio Gerayse.

La idea de la demo no es recorrer cada pantalla posible, sino mostrar un circuito coherente de trabajo:

1. operacion diaria en sucursal
2. control administrativo en tesoreria
3. control interno de efectivo y disponibilidades

## Que Es Gerayse

Gerayse es un sistema web para reemplazar planillas sueltas y dar trazabilidad a:

- sucursales
- turnos
- cajas operativas
- ventas e ingresos
- gastos operativos
- deudas con proveedores
- pagos administrativos
- efectivo central
- arqueos y cierres

Hoy, para esta demo, la tesoreria debe presentarse como un sistema de control interno.
No hay que prometer integracion bancaria real ni conciliacion automatica contra homebanking.

## Requisitos Previos Para La Demo

- Ingresar con un usuario administrador.
- Tener el sistema levantado y accesible.
- Si la base esta vacia, conviene cargar antes o durante la demo:
  - 1 sucursal
  - 1 turno abierto
  - 1 caja operativa
  - 1 proveedor
  - 1 categoria de deuda
  - 1 cuenta de control para tesoreria

## Mensaje General Para Explicar Antes De Empezar

Una forma simple de introducir el sistema:

"Gerayse separa la operacion diaria de sucursal de la tesoreria administrativa. Todo lo que pasa en caja queda trazado. Todo lo que se debe y se paga tambien queda trazado. Y despues administracion puede controlar el efectivo central, los pagos y la disponibilidad consolidada sin depender de Excel."

## Recorrido Recomendado De La Demo

## 1. Login

### Que Mostrar

- Pantalla de ingreso.
- Usuario y contrasena.

### Que Decir

- El sistema trabaja con autenticacion.
- El acceso define que puede ver cada usuario.
- Para la demo usamos un administrador para recorrer todo el circuito.

## 2. Modulo Operacion

Ruta sugerida: ` /operacion/ `

### Objetivo Del Bloque

Mostrar que la sucursal puede abrir caja, registrar movimientos y cerrar con control.

### Paso 2.1: Crear Sucursal

Ruta sugerida: ` /sucursales/ ` y ` /sucursales/nueva/ `

### Datos sugeridos

- Nombre: `Sucursal Centro`
- Codigo: `CENTRO`

### Que Decir

- Toda la operacion se organiza por sucursal.
- Esto permite separar cajas, turnos y reportes.

### Paso 2.2: Crear Turno

Ruta sugerida: ` /turnos/ ` y ` /turnos/nuevo/ `

### Datos sugeridos

- Sucursal: `Sucursal Centro`
- Fecha operativa: fecha de hoy
- Tipo: `T.M.`

### Que Decir

- El turno es el contexto operativo del dia.
- La caja no se abre en el aire: siempre queda vinculada a sucursal y turno.

### Paso 2.3: Abrir Caja

Ruta sugerida: ` /cajas/nueva/ `

### Datos sugeridos

- Usuario responsable: el usuario operador o administrador
- Sucursal: `Sucursal Centro`
- Turno: `T.M.`
- Monto inicial: `50000`

### Que Decir

- La caja se abre con fondo inicial.
- Desde este momento, cada movimiento impacta en el saldo esperado.

### Paso 2.4: Registrar Ingreso En Efectivo

Ruta sugerida: ` /cajas/{id}/ingreso/ `

### Datos sugeridos

- Monto: `10000`
- Categoria: `Aporte inicial adicional` o similar
- Observacion: `Ingreso para reforzar cambio`

### Que Decir

- No todo ingreso en caja es una venta.
- El sistema diferencia ingresos operativos de ventas y gastos.

### Paso 2.5: Registrar Venta

Ruta sugerida: ` /cajas/{id}/venta/ `

### Datos sugeridos

- Monto: `18500`
- Tipo de venta: `EFECTIVO` o el equivalente disponible
- Rubro: el que corresponda
- Observacion: `Venta mostrador`

### Que Decir

- La venta queda trazada y afecta la caja segun el medio.
- En una demo simple conviene usar venta que impacte claramente en el saldo.

### Paso 2.6: Registrar Gasto Operativo

Ruta sugerida: ` /cajas/{id}/gasto/ `

### Datos sugeridos

- Monto: `3500`
- Rubro operativo: `Limpieza` o `Mercaderia`
- Categoria: `Compra rapida`
- Observacion: `Compra de insumos`

### Que Decir

- El sistema no solo controla dinero, tambien clasifica el gasto.
- Eso permite despues ver alertas y desvio por rubro.

### Paso 2.7: Mostrar Dashboard Operativo

Ruta sugerida: ` /operacion/?scope=box&box={id} `

### Que Mostrar

- saldo esperado
- movimientos recientes
- contexto de caja activa
- alertas si existen

### Que Decir

- La pantalla central resume la operacion.
- El encargado no necesita buscar informacion en varias planillas.

### Paso 2.8: Cerrar Caja

Ruta sugerida: ` /cajas/{id}/cerrar/ `

### Datos sugeridos

Usar un cierre sin diferencia para el camino feliz:

- Saldo fisico: igual al saldo esperado que informa el sistema
- Justificacion: vacio

### Que Decir

- El cierre compara sistema contra dinero contado.
- Si la diferencia es menor al umbral, el cierre es simple.
- Si la diferencia es importante, el sistema exige justificacion y deja alerta.

## 3. Modulo Tesoreria

Ruta sugerida: ` /tesoreria/dashboard/ `

### Objetivo Del Bloque

Mostrar el circuito administrativo: proveedores, deuda, pago y control de caja central.

### Aclaracion Importante Para La Demo

Presentar tesoreria como control interno.

- Las cuentas se usan como cuentas de control o registro interno.
- No hace falta prometer integracion bancaria real.
- Para esta demo conviene no entrar en conciliacion ni acreditaciones.

### Paso 3.1: Crear Proveedor

Ruta sugerida: ` /tesoreria/proveedores/ ` y ` /tesoreria/proveedores/nuevo/ `

### Datos sugeridos

- Razon social: `Proveedor Demo SRL`
- Identificador fiscal: `30-12345678-9`
- Contacto: `Compras`
- Telefono: `11-5555-1111`
- Email: `compras@proveedordemo.com`

### Que Decir

- El proveedor es el maestro base para deuda y pagos.
- Desde aca se concentra el historial financiero del tercero.

### Paso 3.2: Crear Categoria De Deuda

Ruta sugerida: ` /tesoreria/categorias/ ` y ` /tesoreria/categorias/nueva/ `

### Datos sugeridos

- Nombre: `Servicios`

### Que Decir

- Las categorias permiten ordenar las obligaciones y filtrar rapidamente.

### Paso 3.3: Crear Cuenta De Control

Ruta sugerida: ` /tesoreria/cuentas-bancarias/ ` y ` /tesoreria/cuentas-bancarias/nueva/ `

### Datos sugeridos

- Nombre: `Cuenta Corriente Administracion`
- Banco: `Cuenta De Control Interno`
- Tipo: `Cuenta corriente`
- Numero: `ADM-001`

### Que Decir

- En esta demo no se usa como integracion bancaria real.
- Sirve como cuenta de referencia o registro del medio desde donde se pago.

### Paso 3.4: Registrar Cuenta Por Pagar

Ruta sugerida: ` /tesoreria/cuentas-por-pagar/ ` y ` /tesoreria/cuentas-por-pagar/nueva/ `

### Datos sugeridos

- Proveedor: `Proveedor Demo SRL`
- Categoria: `Servicios`
- Concepto: `Factura mantenimiento de equipos`
- Referencia comprobante: `FAC-0001`
- Fecha emision: fecha de hoy
- Fecha vencimiento: fecha de hoy o fecha cercana
- Importe total: `120000`
- Sucursal: opcional

### Que Decir

- La deuda nace con saldo pendiente completo.
- El estado cambia solo segun pagos reales registrados.

### Paso 3.5: Mostrar Dashboard De Tesoreria

Ruta sugerida: ` /tesoreria/dashboard/ `

### Que Mostrar

- deuda pendiente
- vencidos
- pagos del mes
- secciones principales

### Que Decir

- Tesoreria resume lo urgente: cuanto se debe, que esta vencido y que ya se pago.

### Paso 3.6: Registrar Pago En Efectivo

Ruta sugerida: ` /tesoreria/pagos/efectivo/nuevo/ `

### Datos sugeridos

- Cuenta por pagar: `Factura mantenimiento de equipos`
- Fecha de pago: fecha de hoy
- Monto: `20000`
- Observaciones: `Pago parcial por caja central`

### Que Decir

- El pago reduce la deuda automaticamente.
- Como es efectivo, tambien genera movimiento en caja central.
- Esto muestra la integracion entre tesoreria y control de efectivo.

### Paso 3.7: Registrar Pago Por Transferencia

Ruta sugerida: ` /tesoreria/pagos/transferencia/nuevo/ `

### Datos sugeridos

- Cuenta por pagar: la misma deuda
- Cuenta de registro: `Cuenta Corriente Administracion`
- Fecha de pago: fecha de hoy
- Monto: `30000`
- Referencia: `TRF-DEMO-001`

### Que Decir

- El sistema soporta pagos parciales por distintos medios.
- El saldo pendiente y el estado de la deuda se recalculan solos.

### Paso 3.8: Ver Detalle De La Deuda

Ruta sugerida: ` /tesoreria/cuentas-por-pagar/{id}/ `

### Que Mostrar

- proveedor
- concepto
- importe total
- total pagado
- saldo pendiente
- pagos registrados debajo

### Que Decir

- Esta pantalla reemplaza perfectamente una hoja de control de deuda.
- Se ve historia, estado y trazabilidad en un mismo lugar.

## 4. Control De Efectivo Central

### Objetivo Del Bloque

Mostrar que administracion puede controlar entradas, salidas y conteo de efectivo.

### Paso 4.1: Ver Libro De Efectivo Central

Ruta sugerida: ` /tesoreria/efectivo-central/ `

### Que Mostrar

- movimientos
- ingresos y egresos
- el pago en efectivo recien generado

### Que Decir

- Todo pago en efectivo de tesoreria deja una huella directa en este libro.
- Tambien se pueden registrar movimientos manuales cuando hace falta.

### Paso 4.2: Registrar Movimiento Manual De Efectivo

Ruta sugerida: ` /tesoreria/efectivo-central/nuevo/ `

### Datos sugeridos

- Tipo: `Aporte`
- Fecha: fecha de hoy
- Monto: `50000`
- Concepto: `Refuerzo de caja central para pagos`

### Que Decir

- Esto permite reflejar aportes, retiros o ajustes sin depender de planillas externas.

### Paso 4.3: Realizar Arqueo

Ruta sugerida: ` /tesoreria/arqueos/ ` y ` /tesoreria/arqueos/nuevo/ `

### Datos sugeridos

- Saldo contado efectivo: usar el valor sugerido por el sistema o uno levemente distinto
- Observaciones: `Control de cierre diario`

### Que Decir

- El arqueo compara lo que el sistema dice contra lo que realmente se conto.
- Esto es control interno puro.

## 5. Mostrar Disponibilidades

Ruta sugerida: ` /tesoreria/disponibilidades/ `

### Objetivo Del Bloque

Mostrar una vista consolidada de efectivo y cuentas de control.

### Que Mostrar

- saldo inicial
- ingresos y egresos del periodo
- total consolidado

### Que Decir

- Esta pantalla resume la posicion interna de disponibilidades por periodo.
- Sirve para cierre administrativo y seguimiento mensual.

## Pantallas Que Conviene Evitar En Esta Demo

Para que el mensaje quede consistente, evitar salvo que te las pidan puntualmente:

- ` /tesoreria/conciliacion/ `
- ` /tesoreria/acreditaciones/ `
- ` /tesoreria/lotes-pos/ `
- ` /tesoreria/bancos/{id}/vincular/ `

Motivo:

- hoy el valor principal del sistema esta en trazabilidad y control interno
- no en integracion bancaria real

## Guion Corto De Presentacion

Si necesitas una version resumida de 5 a 7 minutos:

1. login
2. operacion: sucursal, turno, caja
3. cargar una venta y un gasto
4. mostrar dashboard operativo
5. cerrar caja
6. ir a tesoreria
7. crear proveedor y deuda
8. pagar parte en efectivo
9. mostrar deuda actualizada y libro de efectivo central
10. cerrar con disponibilidades o arqueo

## Frases Utiles Para Explicar El Valor Del Sistema

- "Antes esto vivia en varias planillas; ahora queda todo trazado en una sola base."
- "La sucursal registra, administracion controla y la direccion ve el consolidado."
- "La deuda no se pierde en mensajes o Excel: queda viva hasta que realmente se paga."
- "El efectivo central ya no depende de memoria, sino de movimientos y arqueos."

## Cierre Recomendado

Una buena forma de cerrar la demo:

"Lo importante no es solo registrar movimientos, sino poder reconstruir que paso, quien lo hizo y como impacta en la caja, la deuda y la disponibilidad general. Ese es el valor de Gerayse."

# EP-10 Situacion Financiera y Alertas Consolidadas

## Objetivo

Unificar la lectura financiera diaria entre cajas, tesoreria y bancos para tener una vista consolidada de disponibilidades, vencimientos y acreditaciones pendientes.

## Incluye

- dashboard unificado de cajas y tesoreria
- limpieza de acciones o botones sin uso
- situacion financiera con movimientos reales de caja y banco
- movimientos bancarios tipificados
- movimientos bancarios clasificados por rubro operativo visible para el usuario
- limpieza de la palabra categoria en el flujo de movimientos bancarios cuando el dato esperado sea rubro
- carga de acreditaciones por dia o por periodo
- alerta de acreditaciones pendientes
- lectura consolidada de acreditaciones sin reparto por sucursal
- vista total de disponibilidades
- egresos de caja fuerte central imputados en vistas particulares por sucursal
- alertas de vencimientos
- rubro, sucursal y periodo obligatorios en todo movimiento bancario de egreso, sin importar el origen
- diferencia visible entre deuda pendiente del periodo y disponibilidad real en banco

## No incluye todavia

- integracion bancaria en tiempo real
- contabilidad general
- proyecciones financieras de largo plazo
- redisenar todo el maestro historico de categorias de deuda
- migracion masiva de datos legacy sin rubro operativo asociado

## Reglas de negocio

- una venta digital, una acreditacion bancaria y un gasto bancario no son el mismo hecho
- la situacion financiera debe separar efectivo, banco y pendientes
- una acreditacion pendiente se calcula por diferencia entre ventas digitales y acreditaciones registradas a la fecha del periodo
- las acreditaciones bancarias o de tarjeta no se discriminan por sucursal en la disponibilidad porque el dinero ingresado es un fondo comun
- la alerta de acreditacion pendiente debe incluir aclaracion sobre costos de servicio e impuestos cuando corresponda
- toda salida bancaria debe quedar clasificada por tipo y, si aplica, por rubro y proveedor
- los egresos financieros o administrativos deben poder imputarse a sucursal cuando el gasto corresponda a una unidad operativa
- un egreso de caja fuerte central imputado a una sucursal debe afectar la vista financiera particular de esa sucursal en el periodo correspondiente, aunque el dinero haya salido de tesoreria central
- los rubros cargados en el maestro operativo deben estar disponibles para clasificar movimientos bancarios cuando el usuario de tesoreria deba elegir un rubro
- en movimientos bancarios la UI debe hablar de rubro, no de categoria, salvo que se este administrando compatibilidad historica
- un movimiento bancario registrado correctamente debe aparecer luego en la lista o filtro correspondiente a la misma cuenta, sucursal, empresa activa o seleccion usada por el usuario
- todo movimiento bancario de egreso (debito) debe tener rubro, sucursal y periodo para poder contarse en la situacion economica por rubro/sucursal; sin los tres datos completos queda como gasto sin imputar, aunque igual afecte el saldo bancario real
- el campo periodo de un movimiento bancario (`periodo_pago`) ya existe en el modelo; lo que falta es exigirlo junto con rubro y sucursal en todo egreso bancario y no solo en los egresos administrativos de tesoreria
- el consolidado debe explicar, con un numero y no solo con listas separadas, si la deuda pendiente del periodo esta cubierta por la disponibilidad real en banco

## User Stories

### [x] US-10.1 Dashboard unificado de cajas y tesoreria

Como administracion
Quiero un dashboard consolidado de cajas y tesoreria
Para ver la situacion financiera sin entrar a pantallas separadas

Criterios:
- resumen de efectivo visible
- resumen de banco visible
- deuda y vencimientos visibles
- acreditaciones y pendientes visibles
- no quedan botones vacios o acciones sin destino

### [x] US-10.2 Situacion financiera por periodo

Como administracion
Quiero ver movimientos reales de caja y banco por periodo
Para entender la posicion financiera actual

Criterios:
- filtro por fecha o periodo
- vista consolidada general
- vista filtrable por sucursal
- ingresos y egresos diferenciados

### [x] US-10.3 Tipificacion de movimientos bancarios

Como tesoreria
Quiero registrar movimientos bancarios con tipos claros
Para explicar cada salida o entrada sin textos ambiguos

Criterios:
- ingresos por acreditacion
- egresos por cheque y echeq
- egresos por impuestos
- egresos por comisiones bancarias
- egresos por retiros
- egresos por transferencias a terceros
- rubro y proveedor obligatorios cuando aplique

### [x] US-10.4 Carga de acreditaciones por dia o por periodo

Como tesoreria
Quiero registrar acreditaciones por dia o por periodo
Para reflejar la informacion bancaria segun como llegue el dato

Criterios:
- carga diaria disponible
- carga agrupada por periodo disponible
- cuenta bancaria y fecha obligatorias
- el sistema evita duplicados evidentes

### [x] US-10.5 Alerta de acreditaciones pendientes

Como administracion
Quiero ver alertas de acreditaciones pendientes
Para detectar rapido cuando lo vendido digitalmente no llego al banco

Criterios:
- calculo por periodo
- total general de todas las sucursales
- no discrimina acreditaciones por sucursal en la disponibilidad, porque el ingreso bancario se lee como fondo comun
- si el origen de la venta digital tuvo sucursal, ese dato puede quedar como referencia operativa, pero no divide el dinero acreditado por sucursal
- mensaje aclaratorio sobre deducir costos de servicio e impuestos de Payway u operador equivalente

### [x] US-10.6 Disponibilidades totales

Como administracion
Quiero ver el total de caja fuerte general y el total existente en banco
Para conocer la disponibilidad total en modo vista

Criterios:
- total caja fuerte general
- total banco
- total consolidado
- fecha de referencia visible

### [x] US-10.7 Alertas de vencimientos

Como administracion
Quiero recibir alertas sobre vencimientos y proximos compromisos
Para priorizar pagos antes de quedar fuera de termino

Criterios:
- vencido
- vence hoy
- vence en los proximos dias
- vista general y por sucursal cuando aplique

### [x] US-10.8 Rubros operativos disponibles en movimientos bancarios

Como tesoreria
Quiero elegir en movimientos bancarios los mismos rubros activos que ya estan cargados en el maestro de rubros
Para no duplicar categorias ni depender de textos manuales para clasificar egresos bancarios

Criterios:
- el campo visible se llama Rubro
- no aparece el texto Rubro / categoria ni Categoria en el formulario de alta de movimiento bancario
- el selector muestra rubros operativos activos y no de sistema
- las clases bancarias que hoy exigen clasificacion siguen exigiendo un rubro
- el movimiento queda persistido con el rubro elegido y ese rubro se ve en lista y detalle
- los movimientos existentes con categoria legacy siguen visibles sin bloquear la consulta

### [x] US-10.9 Visibilidad del movimiento bancario despues del alta

Como tesoreria
Quiero que una transferencia registrada aparezca en la lista correspondiente al mismo contexto seleccionado
Para confirmar la carga sin buscarla en otra pantalla o quitar filtros a mano

Criterios:
- despues de registrar un movimiento bancario se puede encontrar por concepto, referencia o monto
- si el usuario filtra por cuenta bancaria, aparece cuando la cuenta coincide
- si el usuario filtra por sucursal o empresa activa, el criterio de filtro usa una regla clara y consistente para la cuenta bancaria y la sucursal de gasto
- una transferencia a terceros no queda oculta por no tener categoria legacy si tiene rubro operativo
- cuando no hay resultados, la pantalla muestra filtros aplicados y permite limpiar la seleccion
- decision de implementacion: la visibilidad usa cuenta bancaria, sucursal de la cuenta, sucursal de gasto y empresa activa cuando esos datos existen

### [x] US-10.10 Accion primaria visible en formulario de movimiento bancario

Como tesoreria
Quiero que el boton final del formulario indique claramente la accion que ejecuta
Para registrar el movimiento sin dudas sobre si el formulario esta completo o si falta una accion oculta

Criterios:
- el boton de envio del alta de movimiento bancario muestra un texto visible como Guardar movimiento
- el texto del boton no queda vacio aunque el formulario use una plantilla reusable
- la accion secundaria de volver o cancelar queda diferenciada de la accion principal
- la evidencia visual cubre pantallas desktop y mobile para evitar botones sin etiqueta

### [x] US-10.11 Acreditaciones consolidadas sin reparto por sucursal

Como administracion
Quiero que las acreditaciones bancarias se lean como ingreso consolidado
Para no dividir por sucursal dinero que entra a una disponibilidad comun

Criterios:
- [x] las acreditaciones registradas aparecen en disponibilidad/banco como ingreso consolidado de la empresa o cuenta correspondiente
- [x] un filtro por sucursal no reparte ni prorratea el importe acreditado entre locales
- [x] si una venta digital de origen tenia sucursal, ese dato queda disponible como referencia operativa pero no cambia la lectura financiera del dinero acreditado
- [x] las alertas de acreditaciones pendientes siguen mostrando el pendiente general del periodo
- [x] la pantalla diferencia esta regla de los egresos, que si pueden imputarse y consultarse por sucursal cuando el gasto corresponde a una unidad operativa
- [x] los tests deben cubrir que una acreditacion no desaparece ni cambia de monto por seleccionar una sucursal

### [x] US-10.12 Egresos de caja fuerte central en vista financiera por sucursal

Como administracion
Quiero que la situacion financiera particular de una sucursal tome los egresos de caja fuerte central imputados a esa sucursal
Para leer el resultado financiero real del local, no solo el consolidado general

Criterios:
- [x] un egreso administrativo pagado desde caja fuerte central con sucursal y periodo aparece en la vista financiera de esa sucursal
- [x] el mismo egreso sigue reduciendo la caja fuerte central general, sin duplicarse como egreso de caja operativa
- [x] el consolidado general y la vista particular por sucursal muestran importes consistentes para el mismo periodo
- [x] el detalle del egreso permite ver origen `caja fuerte central`, sucursal imputada, rubro, concepto, importe y fecha
- [x] si el egreso no tiene sucursal o periodo, queda visible como pendiente de imputacion y no entra silenciosamente en una sucursal
- [x] el filtro por empresa activa no mezcla egresos de sucursales fuera del contexto seleccionado
- [x] los tests deben cubrir que un egreso central imputado a una sucursal afecta el total particular de esa sucursal

### [x] US-10.13 Rubro, sucursal y periodo obligatorios en todo movimiento bancario de egreso

Como administracion
Quiero que todo movimiento bancario de egreso exija rubro, sucursal y periodo sin importar su origen
Para que un debito cargado manualmente o vinculado a un pago quede clasificado igual que un egreso administrativo de tesoreria, y no se repitan huecos de imputacion como los detectados en el diagnostico `reporte_sin_sucursal`

Criterios:
- [x] a partir de esta historia, toda alta o edicion de `MovimientoBancario` de tipo debito exige `rubro_operativo`, `sucursal_gasto` y `periodo_pago`, sin importar si el origen es manual, pago de tesoreria o egreso de tesoreria (al vincular un pago, el debito hereda sucursal y periodo de la deuda pagada cuando le faltan; si sigue incompleto, la vinculacion se bloquea)
- [x] los creditos bancarios (acreditaciones y otros ingresos) no exigen estos tres campos, porque son plata comun sin imputacion por sucursal
- [x] los retiros de banco (`clase=RETIRO`, fondeo de caja fuerte) tampoco exigen imputacion ni cuentan como gasto tesoreria: mueven fondos entre banco y caja fuerte, no son un gasto; contarlos duplicaria el gasto contra los egresos de caja fuerte imputados
- [x] los movimientos historicos que ya existan sin estos tres datos completos no se bloquean ni se ocultan; el listado de banco tiene filtro `Imputacion: pendientes/imputados`, un resumen con total y cantidad de egresos pendientes, y una accion `Completar imputacion` que funciona para cualquier origen (incluidos debitos vinculados a pagos, que la edicion manual bloquea); la anulacion de un historico incompleto sigue funcionando
- [x] al completar los tres datos en un movimiento historico, ese egreso pasa a sumar en `Gasto tesoreria` por rubro/sucursal en la situacion economica en lugar de quedar en `Gasto sin imputar` (los debitos manuales incompletos ahora tambien cuentan como `Gasto sin imputar`; antes eran invisibles a esa alerta)
- [x] el mensaje de error identifica exactamente que dato falta (rubro, sucursal o periodo) en vez de un error generico
- [x] los tests cubren: alta bloqueada de un debito manual sin los tres datos, alta bloqueada de un debito vinculado a un pago de tesoreria sin los tres datos, y edicion de un historico incompleto que pasa a completo

### [x] US-10.14 Diferencia entre deuda pendiente y disponibilidad en banco

Como administracion
Quiero ver en el consolidado la diferencia entre la deuda pendiente del periodo y la disponibilidad real en banco
Para saber si el banco alcanza para cubrir los compromisos pendientes antes de que se generen vencimientos sin fondos

Criterios:
- [x] el consolidado muestra la deuda pendiente del periodo segun `CuentaPorPagar` no anulada en estado pendiente o parcial (usa el mismo numero que la tarjeta `Deuda pendiente` ya existente: toda la deuda viva a la fecha de corte, sin filtrar por periodo de referencia)
- [x] el consolidado muestra la disponibilidad real en banco a la fecha de corte, calculada como saldo inicial mas movimientos reales (misma base que `US-4.8`/`US-10.6`)
- [x] el consolidado muestra la diferencia entre ambos numeros con signo, indicando si el banco cubre o no cubre la deuda pendiente (tarjeta `Banco menos deuda pendiente` en el dashboard de tesoreria; solo en la vista consolidada, porque en la vista por sucursal el banco por sucursal no incluye las cuentas de empresa y el numero enganaria)
- [x] bajo contexto de empresa, la deuda pendiente incluye las deudas legacy sin sucursal (igual que la lectura economica), para no sobreestimar la cobertura
- [x] el calculo no mezcla caja fuerte central dentro del numero de banco, ni mezcla acreditacion pendiente de cobrar dentro de la deuda pendiente
- [x] la fecha de corte usada para el calculo queda visible junto al numero
- [x] los tests cubren el calculo con deuda mayor a la disponibilidad bancaria y con disponibilidad bancaria mayor a la deuda pendiente

Nota de alcance a confirmar con el cliente: esta historia interpreta "pendiente" como deuda pendiente de pago (`CuentaPorPagar`), distinta de la acreditacion pendiente de cobrar que ya cubren `US-10.5`/`US-10.11`. Si el pedido original se referia a la acreditacion pendiente, la fuente de datos cambia pero el criterio de "mostrar una diferencia explicita en el consolidado" se mantiene igual. Segunda decision tomada: "deuda pendiente del periodo" se implemento como la deuda viva total a la fecha de corte (igual que la tarjeta `Deuda pendiente`), no como deuda del mes de referencia; si el cliente esperaba solo la deuda del mes, es un ajuste chico de filtro.

## Dependencias

- EP-03 tesoreria central base
- EP-04 bancos y conciliacion
- EP-05 flujo de disponibilidades
- EP-06 control de gestion y alertas

## Orden tecnico sugerido

1. ordenar taxonomia de movimientos bancarios
2. resolver carga de acreditaciones por dia o periodo
3. consolidar disponibilidades y situacion financiera
4. unificar dashboard y limpiar acciones vacias
5. activar alertas de acreditaciones pendientes y vencimientos
6. alinear movimientos bancarios con rubros operativos y retirar el texto categoria del flujo de alta
7. corregir filtros o contexto para que una transferencia recien registrada sea visible en la seleccion esperada
8. validar que el boton principal del formulario siempre tenga etiqueta visible
9. ajustar lectura de acreditaciones para que sea consolidada y no se reparta por sucursal
10. incorporar egresos de caja fuerte central imputados a vistas financieras particulares por sucursal
11. exigir rubro, sucursal y periodo en todo egreso bancario y habilitar worklist de historicos incompletos
12. agregar diferencia explicita entre deuda pendiente y disponibilidad en banco al consolidado

## Criterio de cierre

- la administracion puede leer efectivo, banco, deuda y pendientes desde una sola vista
- las acreditaciones pendientes dejan de calcularse a mano
- la situacion financiera consolidada sale del sistema con filtros por periodo y sucursal
- tesoreria puede cargar una transferencia bancaria con rubro operativo, verla inmediatamente en el contexto correcto y entender la accion principal del formulario sin botones vacios
- las acreditaciones se leen como ingreso comun y los egresos mantienen imputacion por sucursal cuando corresponda
- el estado financiero particular de una sucursal incluye egresos de caja fuerte central imputados a esa sucursal y periodo
- ningun egreso bancario nuevo puede cargarse sin rubro, sucursal y periodo, y los historicos incompletos tienen un camino claro para completarse
- el consolidado explica con un numero si la deuda pendiente del periodo esta cubierta por lo disponible en banco

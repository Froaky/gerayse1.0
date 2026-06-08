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
- alertas de vencimientos

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
- los rubros cargados en el maestro operativo deben estar disponibles para clasificar movimientos bancarios cuando el usuario de tesoreria deba elegir un rubro
- en movimientos bancarios la UI debe hablar de rubro, no de categoria, salvo que se este administrando compatibilidad historica
- un movimiento bancario registrado correctamente debe aparecer luego en la lista o filtro correspondiente a la misma cuenta, sucursal, empresa activa o seleccion usada por el usuario

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

### [ ] US-10.11 Acreditaciones consolidadas sin reparto por sucursal

Como administracion
Quiero que las acreditaciones bancarias se lean como ingreso consolidado
Para no dividir por sucursal dinero que entra a una disponibilidad comun

Criterios:
- las acreditaciones registradas aparecen en disponibilidad/banco como ingreso consolidado de la empresa o cuenta correspondiente
- un filtro por sucursal no reparte ni prorratea el importe acreditado entre locales
- si una venta digital de origen tenia sucursal, ese dato queda disponible como referencia operativa pero no cambia la lectura financiera del dinero acreditado
- las alertas de acreditaciones pendientes siguen mostrando el pendiente general del periodo
- la pantalla diferencia esta regla de los egresos, que si pueden imputarse y consultarse por sucursal cuando el gasto corresponde a una unidad operativa
- los tests deben cubrir que una acreditacion no desaparece ni cambia de monto por seleccionar una sucursal

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

## Criterio de cierre

- la administracion puede leer efectivo, banco, deuda y pendientes desde una sola vista
- las acreditaciones pendientes dejan de calcularse a mano
- la situacion financiera consolidada sale del sistema con filtros por periodo y sucursal
- tesoreria puede cargar una transferencia bancaria con rubro operativo, verla inmediatamente en el contexto correcto y entender la accion principal del formulario sin botones vacios
- las acreditaciones se leen como ingreso comun y los egresos mantienen imputacion por sucursal cuando corresponda

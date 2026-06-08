# EP-11 Rentabilidad y Situacion Economica

## Objetivo

Construir una vista economica del negocio por periodo y sucursal que relacione ventas, gastos por rubro y deuda del periodo para estimar rentabilidad real.

## Incluye

- porcentajes de gastos por rubro sobre ventas
- calculo de desvio por sucursal y periodo
- rentabilidad por periodo
- vista economica quincenal o mensual
- deudas asignadas a rubro y periodo
- reemplazo funcional de categoria por rubro donde haga falta comparabilidad economica
- egresos pagados desde tesoreria imputados a sucursal, rubro y periodo
- reimputacion auditada de gastos historicos cuando se crea o corrige una sucursal
- exclusion controlada de ventas que no deben integrar la base economica general
- desglose trazable de cada total por rubro hasta los movimientos o documentos que lo componen

## No incluye todavia

- balance contable formal
- amortizaciones complejas
- centros de costo avanzados
- facturacion contable o fiscal de panificacion fuera de la lectura gerencial definida

## Reglas de negocio

- la situacion economica trabaja por periodo, no solo por movimiento de caja del dia
- todo gasto y toda deuda relevante deben quedar asociados a un rubro comparable
- la rentabilidad debe calcularse contra ventas o ingresos del mismo periodo
- una deuda sin periodo no debe entrar a la lectura economica consolidada
- el sistema debe permitir vista quincenal, mensual o por periodos mayores
- los egresos administrativos pagados desde tesoreria deben impactar la situacion economica si tienen rubro, sucursal y periodo de imputacion
- el desglose por sucursal debe incluir gastos pagados por tesoreria cuando correspondan a esa sucursal, aunque no hayan salido de una caja operativa
- la discriminacion por sucursal aplica a egresos, gastos y deuda imputable; no debe inferirse desde acreditaciones bancarias comunes
- una reimputacion historica de gasto debe conservar valor anterior, valor nuevo, usuario, fecha y motivo
- las ventas excluidas de la base economica general deben quedar marcadas por regla visible y no por borrado manual
- todo total economico por rubro debe poder explicarse desde sus componentes cuando el usuario necesite auditar de donde sale el valor
- el desglose de un rubro debe reconciliar contra el total mostrado para el mismo periodo, sucursal y empresa activa

## User Stories

### [x] US-11.1 Parametros de porcentaje por rubro sobre ventas

Como administracion
Quiero registrar porcentajes objetivo de gasto por rubro sobre ventas
Para medir si cada sucursal se mantiene dentro del rango esperado

Criterios:
- porcentaje objetivo por rubro
- alcance global o por sucursal
- vigencia por periodo cuando aplique
- activacion y desactivacion sin perder historial de objetivos

### [x] US-11.2 Calculo de gasto real contra ventas

Como administracion
Quiero comparar el gasto real por rubro contra las ventas
Para detectar desvio economico por sucursal y periodo

Criterios:
- ventas del periodo como base de calculo
- gastos y deuda imputable del periodo por rubro
- porcentaje real calculado
- desvio contra objetivo visible
- filtros por sucursal, periodo y rubro

### [x] US-11.3 Rentabilidad por sucursal y periodo

Como direccion
Quiero ver la rentabilidad por sucursal y periodo
Para evaluar el resultado economico del negocio

Criterios:
- ingreso del periodo
- egreso del periodo
- resultado economico visible
- filtro por sucursal
- filtro por quincena, mes o rango mayor

### [x] US-11.4 Vista de situacion economica

Como direccion
Quiero una vista economica consolidada
Para analizar el costo real del negocio en periodos grandes

Criterios:
- vista quincenal o mensual obligatoria
- posibilidad de ampliar a periodos mayores
- rubros principales visibles
- deudas del periodo incorporadas a la lectura

### [x] US-11.5 Deudas asociadas a rubro y periodo

Como administracion
Quiero asignar tipo de deuda, rubro y periodo
Para incluir impuestos, proveedores y servicios en la lectura economica correcta

Criterios:
- tipo de deuda visible
- rubro obligatorio
- fecha o periodo obligatorio
- filtros por rubro, periodo y estado
- una deuda sin rubro o periodo no entra a la lectura economica consolidada

### [x] US-11.6 Reemplazo funcional de categoria por rubro

Como administracion
Quiero usar rubro donde hoy la categoria no alcanza
Para tener comparabilidad real entre gastos y deuda

Criterios:
- rubro reemplaza o complementa categoria segun el flujo
- la UI usa el termino coherente para el usuario
- la migracion funcional no rompe consultas existentes
- la compatibilidad legacy queda explicita mientras existan categorias historicas sin migrar

### [ ] US-11.7 Egresos de tesoreria en situacion economica por sucursal

Como direccion
Quiero que la situacion economica tome los egresos administrativos pagados desde tesoreria
Para ver el gasto real por sucursal, rubro y periodo aunque el dinero haya salido de caja fuerte central

Criterios:
- los egresos de tesoreria con rubro, sucursal y periodo se incorporan al gasto real de la situacion economica
- el desglose de cada sucursal muestra gastos por rubro pagados desde tesoreria cuando corresponden al periodo filtrado
- los egresos operativos de caja y los egresos de tesoreria no se duplican si representan hechos distintos
- los egresos sin sucursal, rubro o periodo quedan visibles como pendientes de imputacion y no se ocultan silenciosamente
- el dashboard muestra una explicacion o detalle que permita distinguir gastos de caja operativa, deuda del periodo y egresos de tesoreria
- el calculo respeta empresa activa, sucursal filtrada y rango de periodo seleccionado

### [ ] US-11.8 Reimputacion auditada de gastos historicos por sucursal y rubro

Como administracion
Quiero modificar la sucursal, rubro o periodo de imputacion de gastos historicos
Para mover gastos cargados antes de crear una nueva sucursal sin perder trazabilidad

Criterios:
- se puede reimputar un gasto historico desde una sucursal anterior, por ejemplo `EB1`, hacia una nueva sucursal como panaderia/pasteleria
- la reimputacion permite ajustar sucursal, rubro y periodo cuando el gasto quedo mal clasificado
- el sistema exige motivo obligatorio y usuario responsable
- queda guardado el valor anterior y el valor nuevo de cada campo modificado
- la reimputacion actualiza la situacion economica y los desgloses por sucursal/rubro/periodo
- el movimiento financiero original no se borra ni se duplica; solo cambia la imputacion economica o administrativa permitida
- si el gasto esta asociado a un pago cerrado o movimiento sensible, la UI muestra restriccion o requiere permiso administrativo

### [ ] US-11.9 Ventas excluidas de la base economica general

Como direccion
Quiero excluir ventas internas o especiales de la base de ventas general
Para que facturacion de panificacion u otros flujos no distorsionen la rentabilidad total del negocio

Criterios:
- una venta, canal, rubro o sucursal puede marcarse como excluida de la base economica general segun regla de negocio visible
- las ventas excluidas siguen registradas y auditables, pero no se suman a `ventas base` de la situacion economica general
- la vista muestra el total excluido por separado para que la diferencia sea explicable
- el filtro por sucursal puede mostrar esas ventas cuando se consulta la unidad correspondiente
- la regla de exclusion no afecta el efectivo fisico, las acreditaciones ni la trazabilidad de caja
- la configuracion evita excluir ventas por error sin permiso administrativo

### [ ] US-11.10 Desglose trazable de totales por rubro

Como administracion
Quiero abrir el detalle de un rubro de la situacion economica
Para ver por que movimientos, deudas o egresos esta compuesto el total mostrado

Criterios:
- desde un total por rubro, por ejemplo `Almacen $100.000`, se puede ver una lista de componentes que suman exactamente ese total para el periodo y sucursal filtrados
- cada componente muestra origen del dato, fecha, sucursal, rubro, concepto o proveedor, importe y referencia disponible
- el origen distingue al menos deuda del periodo, egreso operativo de caja y egreso administrativo de tesoreria cuando esos datos participen del total
- si un componente viene de un dato corregido o reimputado, el detalle permite reconocer que tuvo una correccion o ver su trazabilidad
- si existen componentes legacy sin informacion completa, quedan separados como `pendientes de trazabilidad` y no se mezclan sin explicacion con el total auditado
- el desglose respeta empresa activa, sucursal, rubro y periodo seleccionados
- el total del encabezado del desglose coincide con la suma de los componentes visibles o explica cualquier ajuste pendiente de imputacion

## Dependencias

- EP-03 tesoreria central base
- EP-06 control de gestion y alertas
- EP-07 impuestos, planes y autorizaciones
- datos consistentes de ventas, egresos y deuda por sucursal
- EP-05 para egresos administrativos de tesoreria con sucursal y periodo
- EP-08 para cargas y correcciones de caja por canal

## Orden tecnico sugerido

1. cerrar criterio comun de rubros para gastos y deuda
2. asignar periodo a deuda y obligaciones
3. calcular porcentajes y desvio contra ventas
4. construir vista de rentabilidad por sucursal y periodo
5. consolidar situacion economica
6. incorporar egresos administrativos de tesoreria por sucursal/rubro/periodo
7. habilitar reimputacion auditada de gastos historicos
8. configurar exclusiones de ventas para base economica general
9. agregar desglose auditable de componentes por rubro

## Criterio de cierre

- la administracion puede leer costo y rentabilidad por sucursal y periodo sin planillas paralelas
- deuda, gastos y ventas del periodo quedan comparables bajo el mismo esquema de rubros
- la situacion economica deja de depender de calculos manuales quincenales o mensuales
- los gastos pagados desde tesoreria aparecen en la situacion economica de la sucursal correspondiente
- los gastos historicos mal imputados pueden corregirse con auditoria
- las ventas internas o especiales quedan explicadas sin inflar la base general
- cada total por rubro puede abrirse y reconciliarse contra sus componentes de origen

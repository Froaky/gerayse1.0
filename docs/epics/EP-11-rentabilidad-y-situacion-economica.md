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

## No incluye todavia

- balance contable formal
- amortizaciones complejas
- centros de costo avanzados

## Reglas de negocio

- la situacion economica trabaja por periodo, no solo por movimiento de caja del dia
- todo gasto y toda deuda relevante deben quedar asociados a un rubro comparable
- la rentabilidad debe calcularse contra ventas o ingresos del mismo periodo
- una deuda sin periodo no debe entrar a la lectura economica consolidada
- el sistema debe permitir vista quincenal, mensual o por periodos mayores

## User Stories

### [ ] US-11.1 Parametros de porcentaje por rubro sobre ventas

Como administracion
Quiero registrar porcentajes objetivo de gasto por rubro sobre ventas
Para medir si cada sucursal se mantiene dentro del rango esperado

Criterios:
- porcentaje por rubro
- alcance por sucursal
- alcance por periodo cuando aplique
- activacion y desactivacion

### [ ] US-11.2 Calculo de gasto real contra ventas

Como administracion
Quiero comparar el gasto real por rubro contra las ventas
Para detectar desvio economico por sucursal y periodo

Criterios:
- ventas del periodo como base de calculo
- gastos del periodo por rubro
- porcentaje real calculado
- desvio contra objetivo visible

### [ ] US-11.3 Rentabilidad por sucursal y periodo

Como direccion
Quiero ver la rentabilidad por sucursal y periodo
Para evaluar el resultado economico del negocio

Criterios:
- ingreso del periodo
- egreso del periodo
- resultado economico visible
- filtro por sucursal
- filtro por quincena, mes o rango mayor

### [ ] US-11.4 Vista de situacion economica

Como direccion
Quiero una vista economica consolidada
Para analizar el costo real del negocio en periodos grandes

Criterios:
- vista quincenal o mensual obligatoria
- posibilidad de ampliar a periodos mayores
- rubros principales visibles
- deudas del periodo incorporadas a la lectura

### [ ] US-11.5 Deudas asociadas a rubro y periodo

Como administracion
Quiero asignar tipo de deuda, rubro y periodo
Para incluir impuestos, proveedores y servicios en la lectura economica correcta

Criterios:
- tipo de deuda visible
- rubro obligatorio
- fecha o periodo obligatorio
- filtros por rubro, periodo y estado

### [ ] US-11.6 Reemplazo funcional de categoria por rubro

Como administracion
Quiero usar rubro donde hoy la categoria no alcanza
Para tener comparabilidad real entre gastos y deuda

Criterios:
- rubro reemplaza o complementa categoria segun el flujo
- la UI usa el termino coherente para el usuario
- la migracion funcional no rompe consultas existentes

## Dependencias

- EP-03 tesoreria central base
- EP-06 control de gestion y alertas
- EP-07 impuestos, planes y autorizaciones
- datos consistentes de ventas, egresos y deuda por sucursal

## Orden tecnico sugerido

1. cerrar criterio comun de rubros para gastos y deuda
2. asignar periodo a deuda y obligaciones
3. calcular porcentajes y desvio contra ventas
4. construir vista de rentabilidad por sucursal y periodo
5. consolidar situacion economica

## Criterio de cierre

- la administracion puede leer costo y rentabilidad por sucursal y periodo sin planillas paralelas
- deuda, gastos y ventas del periodo quedan comparables bajo el mismo esquema de rubros
- la situacion economica deja de depender de calculos manuales quincenales o mensuales

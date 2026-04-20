# EP-10 Situacion Financiera y Alertas Consolidadas

## Objetivo

Unificar la lectura financiera diaria entre cajas, tesoreria y bancos para tener una vista consolidada de disponibilidades, vencimientos y acreditaciones pendientes.

## Incluye

- dashboard unificado de cajas y tesoreria
- limpieza de acciones o botones sin uso
- situacion financiera con movimientos reales de caja y banco
- movimientos bancarios tipificados
- carga de acreditaciones por dia o por periodo
- alerta de acreditaciones pendientes
- vista total de disponibilidades
- alertas de vencimientos

## No incluye todavia

- integracion bancaria en tiempo real
- contabilidad general
- proyecciones financieras de largo plazo

## Reglas de negocio

- una venta digital, una acreditacion bancaria y un gasto bancario no son el mismo hecho
- la situacion financiera debe separar efectivo, banco y pendientes
- una acreditacion pendiente se calcula por diferencia entre ventas digitales y acreditaciones registradas a la fecha del periodo
- la alerta de acreditacion pendiente debe incluir aclaracion sobre costos de servicio e impuestos cuando corresponda
- toda salida bancaria debe quedar clasificada por tipo y, si aplica, por rubro y proveedor

## User Stories

### [ ] US-10.1 Dashboard unificado de cajas y tesoreria

Como administracion
Quiero un dashboard consolidado de cajas y tesoreria
Para ver la situacion financiera sin entrar a pantallas separadas

Criterios:
- resumen de efectivo visible
- resumen de banco visible
- deuda y vencimientos visibles
- acreditaciones y pendientes visibles
- no quedan botones vacios o acciones sin destino

### [ ] US-10.2 Situacion financiera por periodo

Como administracion
Quiero ver movimientos reales de caja y banco por periodo
Para entender la posicion financiera actual

Criterios:
- filtro por fecha o periodo
- vista consolidada general
- vista filtrable por sucursal
- ingresos y egresos diferenciados

### [ ] US-10.3 Tipificacion de movimientos bancarios

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

### [ ] US-10.4 Carga de acreditaciones por dia o por periodo

Como tesoreria
Quiero registrar acreditaciones por dia o por periodo
Para reflejar la informacion bancaria segun como llegue el dato

Criterios:
- carga diaria disponible
- carga agrupada por periodo disponible
- cuenta bancaria y fecha obligatorias
- el sistema evita duplicados evidentes

### [ ] US-10.5 Alerta de acreditaciones pendientes

Como administracion
Quiero ver alertas de acreditaciones pendientes
Para detectar rapido cuando lo vendido digitalmente no llego al banco

Criterios:
- calculo por periodo
- total general de todas las sucursales
- posibilidad de vista por sucursal
- mensaje aclaratorio sobre deducir costos de servicio e impuestos de Payway u operador equivalente

### [ ] US-10.6 Disponibilidades totales

Como administracion
Quiero ver el total de caja fuerte general y el total existente en banco
Para conocer la disponibilidad total en modo vista

Criterios:
- total caja fuerte general
- total banco
- total consolidado
- fecha de referencia visible

### [ ] US-10.7 Alertas de vencimientos

Como administracion
Quiero recibir alertas sobre vencimientos y proximos compromisos
Para priorizar pagos antes de quedar fuera de termino

Criterios:
- vencido
- vence hoy
- vence en los proximos dias
- vista general y por sucursal cuando aplique

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

## Criterio de cierre

- la administracion puede leer efectivo, banco, deuda y pendientes desde una sola vista
- las acreditaciones pendientes dejan de calcularse a mano
- la situacion financiera consolidada sale del sistema con filtros por periodo y sucursal

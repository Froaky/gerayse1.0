# Matriz Sistema vs Relevo

Esta matriz traduce Excel y relevo operativo a capacidades concretas del sistema.

| Proceso | Evidencia | Fuente | Capacidad del sistema | Entidades/servicios | Prioridad |
|---|---|---|---|---|---|
| Cierre por turno | "Se cierra cada turno por separado" | Relevo encargado | Mantener cierre por turno y responsable | `Turno`, `Caja`, `CierreCaja` | Alta |
| Caja individual por cajero | "Cada cajero tiene su caja individual" | Relevo encargado | Una caja abierta por usuario/turno/sucursal | `Caja`, permisos | Alta |
| Diferencias menores a 10.000 | "Si es menos de 10.000 cierran..." | Relevo encargado | Ajuste o cierre permitido con justificacion segun umbral | `CierreCaja`, `Justificacion` | Alta |
| Diferencias mayores a 10.000 | "informan a la administracion" | Relevo encargado | Escalado con alerta obligatoria | alertas operativas y tesoreria | Alta |
| Ventas por efectivo/tarjeta/PedidosYa | `Ing ef`, `Ing t/c`, `Ing P/ya` | Excel matriz diaria | Ventas por canal separadas | servicios de caja + conciliacion futura | Alta |
| Egresos por rubro real | `CERVEZA`, `VINO`, `ALMACEN`, `PAYWAY`, `SUELDOS`, etc. | Excel matriz diaria | Catalogo de rubros financieros y tablero por rubro | rubros, dashboards | Alta |
| Deuda pendiente | hoja `DEUDAS` por `IMPOSITIVAS`, `SERVICIOS`, `PROVEEDORES` | Excel transferencias/deudas | Cuentas por pagar con categorias y estados | `CuentaPorPagar`, categorias | Alta |
| Pagos por transferencia | hojas mensuales `TRANSFERENCIAS ...` | Excel transferencias/deudas | Registro de pagos administrativos | `PagoTesoreria` | Alta |
| Cheques y ECHEQ | seccion `CHEQUES`, conceptos como `ECHEQ BRUNETTI 15D` | Excel transferencias/deudas | Instrumentos de pago con estado y referencia | `PagoTesoreria`, estados | Alta |
| Pagos parciales | notas `SE PAGA MITAD`, `DEJO DOS FACTURAS PEND` | Excel transferencias/deudas | Multiples pagos por deuda | `PagoTesoreria`, recalc saldo | Alta |
| Flujo efectivo vs banco | `MAPOGO - EFECTIVO` y `MAPOGO - BANCO` | Excel flujo disponibilidades | Doble libro de disponibilidades | movimientos de caja y banco | Alta |
| Arrastre de saldo mensual | `SALDO INICIAL = mes previo` | Excel flujo disponibilidades | Corte mensual y saldo inicial automatico | snapshot mensual o vista acumulada | Media |
| Acreditaciones de tarjeta | `ACREDITADO TJ`, `LIQ PAGO PRISMA` | Excel flujo disponibilidades | Registro de acreditaciones y lotes POS | acreditaciones, conciliacion | Alta |
| Retenciones y descuentos | `DEBITO IIBB`, `DBCR`, `COMISION TRF` | Excel flujo disponibilidades | Descuentos bancarios estructurados | retenciones/descuentos | Alta |
| Pagos bancarios a proveedores | `TRF OSSOLA`, `TRF EL PAL GOL`, `CHEQUE BRUNETTI` | Excel flujo disponibilidades | Relacionar pago con movimiento bancario | pagos + banco | Alta |
| Arqueo manual de disponibilidades | `CAJA TAIS`, `CAJA YO`, `DIFERENCIA` | Excel flujo disponibilidades | Arqueo y diferencia central auditada | arqueos/ajustes | Media |
| Adelantos con autorizacion | "Requieren autorizacion previa" | Relevo encargado | Workflow minimo de autorizacion | aprobaciones/auditoria | Media |
| Gastos urgentes resueltos directo | "Se resuelve directamente" | Relevo encargado | Carga rapida y trazable de gasto urgente | formularios moviles | Alta |
| Control de limites | "Solo a fin de mes" | Relevo encargado + matriz con porcentajes | Dashboard y alertas de desvio mensual | control de gestion | Media |
| Dolor principal | "Pasar datos al excel" | Relevo encargado | Reconstruccion automatica de reportes | todas las epicas | Alta |

Decision funcional:
- el sistema debe replicar el resultado del Excel, pero con entidades persistidas, reglas y auditoria
- los calculos manuales de celdas deben pasar a:
  - pagos parciales
  - retenciones y descuentos
  - saldos iniciales/finales
  - estados de deuda
  - conciliaciones

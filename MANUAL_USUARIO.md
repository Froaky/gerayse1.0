# Manual de Usuario — Gerayse

> Sistema de gestión de caja operativa y tesorería para empresas con múltiples sucursales.
> Versión: mayo 2026.

---

## Índice

1. [Acceso al sistema](#1-acceso-al-sistema)
2. [Selección de empresa](#2-selección-de-empresa)
3. [Caja operativa](#3-caja-operativa)
   - 3.1 [Abrir caja](#31-abrir-caja)
   - 3.2 [Registrar movimientos](#32-registrar-movimientos-en-una-caja-abierta)
   - 3.3 [Cerrar caja](#33-cerrar-caja)
   - 3.4 [Dashboard operativo](#34-dashboard-operativo)
   - 3.5 [Panel de alertas](#35-panel-de-alertas)
   - 3.6 [Matriz de control diaria](#36-matriz-de-control-diaria)
4. [Tesorería](#4-tesorería)
   - 4.1 [Proveedores](#41-proveedores)
   - 4.2 [Categorías de deuda](#42-categorías-de-deuda)
   - 4.3 [Cuentas bancarias](#43-cuentas-bancarias)
   - 4.4 [Cuentas por pagar (obligaciones)](#44-cuentas-por-pagar-obligaciones)
   - 4.5 [Pagos de tesorería](#45-pagos-de-tesorería)
   - 4.6 [Movimientos bancarios](#46-movimientos-bancarios)
   - 4.7 [Lotes POS](#47-lotes-pos)
   - 4.8 [Acreditaciones de tarjeta](#48-acreditaciones-de-tarjeta)
   - 4.9 [Compromisos especiales](#49-compromisos-especiales)
   - 4.10 [Caja central (efectivo de tesorería)](#410-caja-central-efectivo-de-tesorería)
   - 4.11 [Arqueos de disponibilidades](#411-arqueos-de-disponibilidades)
   - 4.12 [Reporte de disponibilidades](#412-reporte-de-disponibilidades)
   - 4.13 [Conciliación bancaria](#413-conciliación-bancaria)
   - 4.14 [Dashboard de tesorería](#414-dashboard-de-tesorería)
5. [Configuración](#5-configuración)
   - 5.1 [Empresas](#51-empresas)
   - 5.2 [Sucursales](#52-sucursales)
   - 5.3 [Turnos](#53-turnos)
   - 5.4 [Rubros operativos](#54-rubros-operativos)
   - 5.5 [Límites de rubros](#55-límites-de-rubros)
   - 5.6 [Reinicio de datos operativos](#56-reinicio-de-datos-operativos)
6. [Usuarios y roles](#6-usuarios-y-roles)
   - 6.1 [Crear usuario](#61-crear-usuario)
   - 6.2 [Editar usuario](#62-editar-usuario)
   - 6.3 [Archivar / reactivar usuario](#63-archivar--reactivar-usuario)
   - 6.4 [Permisos individuales](#64-permisos-individuales)
   - 6.5 [Roles](#65-roles)
7. [Permisos — qué puede hacer cada rol](#7-permisos--qué-puede-hacer-cada-rol)
8. [Flujos de negocio paso a paso](#8-flujos-de-negocio-paso-a-paso)
9. [Preguntas frecuentes](#9-preguntas-frecuentes)

---

## 1. Acceso al sistema

**Quién:** todos los usuarios.

Al ingresar por primera vez, el administrador envía un **link de invitación** que expira en 24 horas. Desde ese link el usuario establece su contraseña. Si el administrador activa la opción "forzar cambio de contraseña", el sistema pedirá una nueva contraseña en el próximo login antes de permitir el acceso a cualquier pantalla.

Para cerrar sesión, usar el botón **Salir** en la barra de navegación superior derecha.

---

## 2. Selección de empresa

**Quién:** todos los usuarios autenticados.

En la barra de navegación superior derecha aparece un selector con las empresas disponibles. Si hay más de una empresa cargada en el sistema, se despliega un menú con checkboxes: seleccioná una o más empresas y presioná **Aplicar selección**.

**Todo lo que muestra el sistema** (cajas, deudas, movimientos, reportes) corresponde únicamente a las empresas seleccionadas. Si no hay ninguna empresa seleccionada, el sistema muestra un aviso en rojo y los listados aparecen vacíos.

La selección persiste durante toda la sesión activa. Al cerrar sesión y volver a entrar, el sistema recuerda la última selección.

---

## 3. Caja operativa

### 3.1 Abrir caja

**Quién:** operarios y administradores con permiso de escritura en caja.

**Menú:** botón **Abrir caja** en la barra superior.

Completar los siguientes campos:

| Campo | Descripción |
|-------|-------------|
| Sucursal | Local donde se opera |
| Turno | Mañana o Tarde |
| Monto inicial | Efectivo con el que arranca la caja |

> **Restricción:** un usuario no puede tener dos cajas abiertas en el mismo turno y sucursal simultáneamente.

---

### 3.2 Registrar movimientos en una caja abierta

Desde el dashboard, hacé clic en tu caja activa para acceder a sus acciones. Cada tipo de movimiento tiene su propio formulario.

#### Gasto

**Acceso:** desde la caja abierta → **Registrar gasto**.

Completar: rubro operativo (categoría del gasto), monto y observación. El gasto reduce el saldo esperado de la caja.

#### Venta

**Acceso:** desde la caja abierta → **Registrar venta**.

Tipos de venta disponibles:

| Tipo | Impacta efectivo |
|------|-----------------|
| Efectivo | Sí — aumenta el saldo físico de la caja |
| Tarjeta | No — se acredita posteriormente por banco |
| Transferencia | No |
| QR / MercadoPago | No |
| PedidosYa | No |

#### Ingreso de efectivo

**Acceso:** desde la caja abierta → **Registrar ingreso**.

Para efectivo recibido fuera de una venta (por ejemplo, un refuerzo de caja enviado desde otra caja). Aumenta el saldo esperado.

#### Traspaso entre cajas

**Acceso:** menú principal → **Traspasos → Entre cajas**.

Mueve dinero de una caja a otra dentro de la **misma sucursal**. Ambas cajas deben estar abiertas. El sistema valida que la caja origen tenga fondos suficientes. El movimiento queda registrado en ambas cajas con trazabilidad cruzada (se puede identificar que son el mismo traspaso).

> Los **traspasos entre sucursales** están temporalmente deshabilitados.

---

### 3.3 Cerrar caja

**Acceso:** desde la caja abierta → **Cerrar caja** (primero muestra un preview).

El sistema calcula el **saldo esperado** en base a todos los movimientos registrados. El operario ingresa el **saldo físico contado**.

**Resultado según diferencia:**

| Diferencia | Acción |
|-----------|--------|
| ≤ $50 | Cierre automático sin justificación |
| > $50 | El sistema pide una justificación escrita antes de cerrar |

Una vez cerrada, la caja **no se puede reabrir**.

---

### 3.4 Dashboard operativo

**Quién:** operarios ven solo sus propias cajas; administradores ven todo.

**Menú:** botón **Caja** en la barra de navegación.

Vistas disponibles (según permiso):

| Vista | Descripción |
|-------|-------------|
| Vista global | Resumen del período para todas las sucursales de las empresas seleccionadas |
| Por sucursal | Detalle de movimientos de una sucursal específica |
| Por caja | Estado en tiempo real de una caja abierta |

El rango de fechas por defecto es el **mes actual** para la vista global. Se puede cambiar con los campos **Fecha desde / Fecha hasta** y presionar **Aplicar**.

KPIs que muestra: ingresos por canal de venta, egresos por rubro, saldo neto del período y semáforo de alertas activas.

---

### 3.5 Panel de alertas

**Quién:** administradores con permiso de lectura de configuración.

**Menú:** Config → **Alertas**.

Tipos de alerta:

| Tipo | Cuándo se genera |
|------|-----------------|
| Diferencia grave | Cierre de caja con diferencia que superó el umbral de $50 |
| Rubro excedido | El gasto en un rubro superó el límite porcentual configurado |

Las alertas se pueden **resolver** manualmente una vez revisadas. Pueden filtrarse por tipo, estado, sucursal y fecha.

---

### 3.6 Matriz de control diaria

**Quién:** administradores.

**Menú:** Config → **Matriz diaria**.

Tabla que muestra por día: ingresos por canal de venta, egresos por rubro y resultado neto. Filtrable por sucursal y rango de fechas. El botón **Exportar** descarga la matriz en formato CSV para trabajar en Excel u otras herramientas.

---

## 4. Tesorería

**Menú:** botón **Tesorería** en la barra de navegación (visible solo si el usuario tiene permiso de lectura en tesorería).

---

### 4.1 Proveedores

**Menú:** Tesorería → Config → **Proveedores**.

Maestro de terceros con quienes se tienen obligaciones de pago.

Datos que se pueden registrar: razón social, identificador fiscal (CUIT/CUIL), contacto, teléfono, email, dirección, sitio web, CBU y alias bancario.

Acciones disponibles: crear, editar, activar/desactivar. Un proveedor **inactivo** no desaparece del historial ni de las obligaciones existentes.

---

### 4.2 Categorías de deuda

**Menú:** Tesorería → Config → **Categorías**.

Clasifican las cuentas por pagar (ejemplos: servicios, insumos, honorarios, alquileres). Pueden vincularse a un **rubro operativo** para unificar la nomenclatura con la caja operativa y facilitar reportes cruzados.

---

### 4.3 Cuentas bancarias

**Menú:** Tesorería → Config → **Cuentas bancarias**.

Cuentas de la empresa desde las cuales se registran los pagos. Datos: banco, tipo (caja de ahorro / cuenta corriente), número de cuenta, CBU, alias y sucursal a la que pertenece (opcional).

> Solo las cuentas **activas** pueden usarse para registrar pagos nuevos.

---

### 4.4 Cuentas por pagar (obligaciones)

**Menú:** Tesorería → **Deudas**.

Registro de todas las obligaciones con proveedores.

**Campos al crear una obligación:**

| Campo | Descripción |
|-------|-------------|
| Proveedor | Obligatorio |
| Categoría | Clasificación de la deuda |
| Sucursal | Opcional — para identificar a qué local corresponde |
| Concepto | Descripción de la obligación |
| Referencia de comprobante | Número de factura u otro documento (opcional, debe ser único por proveedor si se ingresa) |
| Fecha de emisión | Fecha del comprobante |
| Fecha de vencimiento | Debe ser igual o posterior a la fecha de emisión |
| Importe total | Monto de la obligación |
| Observaciones | Texto libre |

**Estados posibles:**

| Estado | Significado |
|--------|-------------|
| Pendiente | Sin pagos realizados; saldo = importe total |
| Parcial | Con uno o más pagos; saldo > 0 |
| Pagada | Saldo = 0 |
| Anulada | Cancelada con motivo; saldo en cero; no permite más pagos |

**Urgencia de vencimiento** (indicador visual en listados):

| Indicador | Significado |
|-----------|-------------|
| 🔴 Vencida | La fecha de vencimiento ya pasó |
| 🟠 Vence hoy | Vence el día de hoy |
| 🟡 Urgente | Vence en los próximos días |
| 🟡 Próximo | Vence pronto |
| 🟢 En plazo | Vencimiento lejano |

Desde el **detalle** de una obligación se accede directamente a los botones para registrar pagos.

---

### 4.5 Pagos de tesorería

**Menú:** Tesorería → **Pagos** o desde el detalle de una cuenta por pagar.

Registrá el pago de una obligación seleccionando el medio de pago:

| Medio | Datos requeridos |
|-------|-----------------|
| Transferencia | Cuenta bancaria de origen, fecha de pago, referencia (opcional) |
| Cheque | Cuenta bancaria, número de cheque, fecha de pago, fecha diferida (opcional) |
| ECHEQ | Cuenta bancaria, número de ECHEQ, fecha de pago, fecha diferida (opcional) |
| Efectivo | Se descuenta directamente de la caja central; no requiere cuenta bancaria |

**Pagos parciales:** el monto puede ser menor al saldo pendiente. La obligación pasa a estado Parcial y el saldo se actualiza automáticamente.

**Anulación:** los pagos registrados **no se editan**. Si hubo un error, se anula el pago con un motivo (el saldo de la obligación se revierte) y se registra uno nuevo.

---

### 4.6 Movimientos bancarios

**Menú:** Tesorería → **Bancos**.

Registro de todos los movimientos en las cuentas bancarias de la empresa: débitos (egresos) y créditos (ingresos). Se ingresan manualmente o se crean automáticamente al registrar una acreditación de tarjeta.

**Clases de movimiento disponibles:**

| Clase | Tipo |
|-------|------|
| Acreditación | Crédito |
| Otro ingreso | Crédito |
| Cheque | Débito |
| ECHEQ | Débito |
| Transferencia a terceros | Débito |
| Impuesto | Débito |
| Comisión bancaria | Débito |
| Retiro | Débito |
| Otro egreso | Débito |

Un movimiento de débito puede **vincularse a un pago de tesorería** para mantener trazabilidad completa entre el registro interno y el extracto bancario real.

---

### 4.7 Lotes POS

**Menú:** Tesorería → Reportes → **Lotes POS**.

Agrupan las transacciones de una terminal de tarjeta en un período de cierre. Cada lote incluye: fecha, nombre de terminal, operador, total del lote y cuenta bancaria destino de la acreditación.

Los lotes POS son la base para registrar posteriormente las acreditaciones.

---

### 4.8 Acreditaciones de tarjeta

**Menú:** Tesorería → Reportes → **Acreditaciones** (o desde Acreditaciones de Tarjeta).

Registra el ingreso bancario por ventas con tarjeta una vez que el procesador deposita los fondos.

**Modos de registro:**

| Modo | Cuándo usarlo |
|------|--------------|
| Diario | Acreditación individual por día de operación |
| Por período | Acreditación agrupada que cubre un rango de fechas (liquidación semanal, quincenal, etc.) |

**Datos a completar:** canal (nombre del procesador, ej: Prisma, Payway), cuenta bancaria destino, monto neto acreditado, lote POS asociado o referencia externa, descuentos aplicados (IIBB, comisión, otros).

Al guardar, el sistema **crea automáticamente** un movimiento bancario de crédito tipo Acreditación. No hace falta crearlo por separado.

---

### 4.9 Compromisos especiales

**Menú:** Tesorería → **Compromisos especiales**.

Para obligaciones que no corresponden a una factura de proveedor estándar.

**Tipos disponibles:**

| Tipo | Ejemplos / Datos específicos |
|------|------------------------------|
| Impuesto | AFIP, IIBB, municipales. Requiere organismo, período fiscal, vencimiento |
| Plan de pago | Cuotas de refinanciación. Requiere nombre del plan, número y total de cuotas, vencimiento |
| Embargo | Judicial. Requiere número de expediente, vencimiento |
| Adelanto | Pago anticipado a empleado. Requiere beneficiario y autorización |
| Sueldo extraordinario | Bonus, aguinaldo especial. Requiere beneficiario y autorización |
| Requerimiento | Trámite administrativo pendiente |

**Flujo de autorización** (para adelantos y sueldos extraordinarios):

1. Tesorero crea el compromiso → estado: **Requiere aprobación**
2. Administrador revisa y toma una decisión (aprueba o rechaza con comentario)
3. Si aprobado → estado: **Aprobado** → se puede ejecutar
4. Si rechazado → estado: **Rechazado** → no se ejecuta

Los compromisos pueden vincularse a una cuenta por pagar existente para consolidar el seguimiento.

---

### 4.10 Caja central (efectivo de tesorería)

**Menú:** Tesorería → Reportes → **Efectivo central**.

Libro de caja de la tesorería. Concentra el **efectivo administrativo** de la empresa (distinto de las cajas operativas de las sucursales). El saldo se calcula automáticamente sumando todos los ingresos y restando los egresos.

**Tipos de movimiento:**

| Tipo | Descripción |
|------|-------------|
| Ingreso desde caja operativa | Efectivo transferido desde una sucursal |
| Aporte de socios | Capital adicional |
| Retiro desde banco | Efectivo extraído de cuenta bancaria |
| Egreso por pago administrativo | Pago en efectivo |
| Depósito en banco | Efectivo depositado en cuenta bancaria |
| Ajuste positivo / negativo | Corrección de diferencias con auditoría |

**Carga inicial:** si la caja ya tiene un saldo preexistente al momento de comenzar a usar el sistema, se registra mediante la opción **Cargar saldo inicial**, indicando fecha, monto y motivo. Queda auditado con usuario y timestamp.

**Egreso de tesorería:** registra pagos y gastos que salen directamente desde tesorería sin pasar por una caja operativa de sucursal. Si el origen es la caja fuerte (efectivo), reduce el libro de efectivo central. Si el origen es una cuenta bancaria, impacta el libro bancario.

---

### 4.11 Arqueos de disponibilidades

**Menú:** Tesorería → **Arqueos**.

Auditoría periódica que compara el **saldo del sistema** contra el **saldo físico contado** en la caja central. Si hay diferencia, queda registrada con observaciones para análisis posterior. Se recomienda realizarlos con frecuencia para detectar desvíos a tiempo.

---

### 4.12 Reporte de disponibilidades

**Menú:** Tesorería → Reportes → **Disponibilidades**.

Consolida por período (mes/año):

- Saldo de la caja central (efectivo)
- Saldos por cuenta bancaria
- **Total de disponibilidades** = efectivo + saldos bancarios

Filtrable por mes y sucursal. Desde esta pantalla también se ejecuta el **cierre mensual**, que genera un snapshot inmutable de todos los saldos al fin de mes para auditoría.

---

### 4.13 Conciliación bancaria

**Menú:** Tesorería → Reportes → **Conciliación**.

Seleccioná una cuenta bancaria y un rango de fechas. El sistema muestra todos los movimientos de esa cuenta con su estado de conciliación:

| Estado | Significado |
|--------|-------------|
| Pendiente | Registrado internamente, aún no confirmado en el extracto |
| Impactado | Confirmado en el extracto bancario |
| Rechazado | Figuró en el extracto como rechazado (ej: cheque rebotado) |

Permite detectar diferencias entre el registro interno y el extracto bancario real.

---

### 4.14 Dashboard de tesorería

**Menú:** Tesorería → **Inicio**.

Resumen del período con filtro de fechas y sucursal opcional.

Incluye:

- Ventas en efectivo e ingresos operativos
- Egresos por caja operativa
- Total de ingresos y egresos bancarios del período
- Saldo de caja central
- Ventas con tarjeta: monto bruto vs. neto acreditado
- Tabla de obligaciones clasificadas por urgencia (vencidas, próximas, en plazo)
- Últimos pagos registrados
- Últimos movimientos bancarios
- Últimos lotes POS cerrados

---

## 5. Configuración

**Quién:** usuarios con permiso de lectura o escritura en configuración.

**Menú:** desplegable **Config** en la barra de navegación superior.

---

### 5.1 Empresas

Entidades madre del sistema. Cada empresa agrupa sus propias sucursales y turnos. Se pueden **activar o desactivar** sin perder historial de operaciones. Una empresa desactivada deja de aparecer en la selección de empresa del header.

---

### 5.2 Sucursales

Locales físicos que pertenecen a una empresa. Cada sucursal tiene un **código único** dentro del sistema. Se pueden activar o desactivar. Una sucursal desactivada no aparece disponible al abrir nuevas cajas.

---

### 5.3 Turnos

Definen los períodos operativos de una empresa: **Mañana (TM)** y **Tarde (TT)**. Un operario no puede tener dos cajas abiertas en el mismo turno y sucursal al mismo tiempo.

---

### 5.4 Rubros operativos

Categorías de gasto que se usan al registrar egresos en las cajas. Se crean con nombre único. Algunos rubros son **de sistema** y no se pueden eliminar ni editar el nombre. Todos pueden activarse o desactivarse.

---

### 5.5 Límites de rubros

Configuran un **porcentaje máximo** de gasto para un rubro en un período, opcionalmente restringido a una sucursal específica. Si una caja supera ese límite durante el período, el sistema genera automáticamente una alerta de tipo "Rubro excedido" en el panel de alertas.

Ejemplo: configurar que el rubro "Limpieza" no supere el 5% del total de gastos en la Sucursal Centro.

---

### 5.6 Reinicio de datos operativos

**Menú:** Config → **Reiniciar datos**.

Elimina **todas** las cajas, movimientos, transferencias, cierres y alertas. **No elimina** la estructura del sistema (empresas, sucursales, turnos, rubros, usuarios, límites).

> ⚠️ Acción **irreversible**. Solo para entornos de prueba o puesta en marcha inicial. Requiere permiso de escritura en configuración.

---

## 6. Usuarios y roles

**Quién:** usuarios con permiso de lectura o escritura en usuarios.

**Menú:** Config → **Usuarios** y Config → **Roles**.

---

### 6.1 Crear usuario

**Menú:** Config → Usuarios → **Nuevo usuario**.

Completar:
- Nombre y apellido, email
- DNI y teléfono (opcionales)
- Rol asignado
- Si el usuario opera siempre en la misma sucursal: activar **usuario fijo** y seleccionar la **sucursal base**

Al guardar, el sistema genera un **link de primer acceso** (válido 24 horas) que se debe compartir con el usuario. Desde ese link el usuario establece su contraseña y accede por primera vez.

---

### 6.2 Editar usuario

Permite modificar datos personales, rol asignado, sucursal base y activar la opción **forzar cambio de contraseña**, que obliga al usuario a cambiar su contraseña en el próximo login.

---

### 6.3 Archivar / reactivar usuario

**Archivar** desactiva el acceso del usuario sin eliminar ningún historial de operaciones (movimientos, cierres, pagos, etc.). Se puede **reactivar** en cualquier momento.

**Eliminar** borra al usuario de forma permanente. Usar con precaución.

---

### 6.4 Permisos individuales

Desde el **detalle de un usuario** se pueden ajustar permisos módulo por módulo, sobreescribiendo los del rol asignado. El sistema muestra de dónde viene cada permiso activo:

| Fuente | Significado |
|--------|-------------|
| Rol: [nombre] | El permiso viene del rol asignado |
| Personalizado | El permiso fue configurado individualmente para este usuario |
| Compatibilidad | Usuario sin rol — acceso por legado |

---

### 6.5 Roles

Los roles agrupan permisos que se aplican a todos los usuarios que los tengan asignados.

**Crear un rol:**
1. Ir a Config → Roles → **Nuevo rol**
2. Asignar código único y nombre descriptivo
3. Configurar los permisos por módulo (lectura y/o escritura)
4. Guardar

Los usuarios que ya tengan ese rol **heredan los cambios automáticamente**, salvo que tengan un override de permiso individual que los sobreescriba.

---

## 7. Permisos — qué puede hacer cada rol

| Permiso | Acceso que otorga |
|---------|------------------|
| **Caja — lectura** | Ver dashboard operativo, cajas abiertas, movimientos e historial de cierres |
| **Caja — escritura** | Abrir caja, registrar gastos / ventas / ingresos, transferir entre cajas, cerrar caja |
| **Config — lectura** | Ver rubros, límites, empresas, sucursales, turnos, alertas y matriz de control |
| **Config — escritura** | Crear y editar rubros, límites, empresas, sucursales y turnos; resolver alertas; exportar matriz; reiniciar datos |
| **Tesorería — lectura** | Ver proveedores, cuentas bancarias, obligaciones, pagos, movimientos bancarios, acreditaciones, reporte de disponibilidades |
| **Tesorería — escritura** | Crear y editar proveedores, cuentas bancarias, obligaciones; registrar pagos; gestionar movimientos bancarios, acreditaciones, compromisos especiales y caja central; cerrar mes |
| **Usuarios — lectura** | Ver listado de usuarios y roles; consultar permisos asignados |
| **Usuarios — escritura** | Crear, editar, archivar y eliminar usuarios; crear y configurar roles; gestionar permisos individuales; enviar links de acceso |

> **Nota:** el permiso de **escritura siempre incluye lectura**. Los superusuarios tienen acceso total sin restricciones. Los usuarios sin rol asignado tienen acceso completo a caja operativa por compatibilidad con versiones anteriores.

---

## 8. Flujos de negocio paso a paso

### Flujo 1 — Operación diaria de caja

```
1. Operario abre caja
   → selecciona sucursal, turno y monto inicial

2. Durante el turno registra:
   → ventas (tarjeta, efectivo, QR, etc.)
   → gastos (con rubro, monto, concepto)
   → ingresos de efectivo (si aplica)

3. El sistema calcula el saldo esperado en tiempo real
   (monto inicial + ingresos en efectivo - egresos en efectivo)

4. Al cierre:
   → operario ingresa el saldo físico contado
   → sistema calcula la diferencia

5. Si diferencia ≤ $50  →  cierre automático
   Si diferencia > $50  →  operario justifica por escrito → cierre

6. Caja queda cerrada e inmutable
```

---

### Flujo 2 — Gestión de una obligación y su pago

```
1. Tesorero recibe factura de proveedor

2. Crea cuenta por pagar:
   → selecciona proveedor, categoría, monto, vencimiento

3. Sistema monitorea vencimiento (urgencia visual en listados)

4. Al momento de pagar:
   → selecciona medio de pago (transferencia / cheque / efectivo)
   → ingresa monto (puede ser parcial)

5. Sistema actualiza saldo pendiente:
   → si saldo > 0: estado = Parcial
   → si saldo = 0: estado = Pagada

6. Si la obligación vence sin pagar:
   → estado visible = "Vencida" (indicador rojo)

7. Si hubo un error en un pago:
   → anular el pago con motivo → el saldo se revierte
   → registrar pago correcto
```

---

### Flujo 3 — Acreditación de tarjeta

```
1. Ventas del día con tarjeta quedan registradas en la caja operativa

2. El procesador de tarjeta liquida los fondos (puede tardar días)

3. Tesorero crea lote POS con datos de la terminal

4. Cuando acredita el banco:
   → registra acreditación (canal, monto neto, descuentos)
   → sistema crea movimiento bancario automáticamente

5. El reporte de disponibilidades consolida el monto acreditado
   vs. el total de ventas con tarjeta (para detectar pendientes)
```

---

### Flujo 4 — Compromiso especial con autorización

```
1. Tesorero detecta pago extraordinario necesario (adelanto, impuesto, embargo)

2. Crea compromiso especial:
   → selecciona tipo, ingresa monto, vencimiento y datos específicos

3. Si requiere autorización (adelanto / sueldo extraordinario):
   → estado = "Requiere aprobación"
   → administrador recibe el compromiso y decide

4. Administrador aprueba o rechaza con comentario:
   → aprobado → estado = "Aprobado" → puede ejecutarse
   → rechazado → estado = "Rechazado" → no se ejecuta

5. Tesorero vincula el compromiso a una cuenta por pagar (opcional)
   y procede con el pago correspondiente
```

---

### Flujo 5 — Cierre mensual de tesorería

```
1. Al finalizar el mes, tesorero accede al Reporte de Disponibilidades

2. Verifica saldos:
   → efectivo en caja central
   → saldos en cada cuenta bancaria
   → total de disponibilidades

3. Realiza arqueo de disponibilidades si corresponde
   (compara saldo físico vs. sistema)

4. Ejecuta el cierre mensual:
   → sistema genera snapshot inmutable de todos los saldos
   → queda registrado el período, fecha y usuario que cerró

5. El snapshot queda disponible para auditoría futura
```

---

## 9. Preguntas frecuentes

**¿Por qué no veo datos en el dashboard?**
Verificá que tenés al menos una empresa seleccionada en el selector del header. Si el selector muestra "Sin empresa", el sistema no filtra por ninguna y los listados aparecen vacíos.

---

**¿Por qué no puedo abrir una caja?**
Un usuario solo puede tener una caja abierta por turno y sucursal. Si ya hay una caja abierta en ese turno y sucursal con tu usuario, el sistema no permite abrir otra. También verificá que tengas permiso de escritura en caja.

---

**¿Qué pasa si me equivoco en un movimiento dentro de la caja?**
Los movimientos de caja no se eliminan ni editan. Si registraste un importe incorrecto, registrá el movimiento inverso para compensarlo y dejá una observación explicando el ajuste.

---

**¿Qué pasa si me equivoco en un pago de tesorería?**
Anulá el pago (desde el detalle del pago → botón Anular, requiere motivo). El saldo pendiente de la obligación se revierte automáticamente. Luego registrá el pago correcto.

---

**¿Puedo ver cajas de otros operarios?**
Los operarios solo ven sus propias cajas en el dashboard. Los usuarios con permiso de lectura de configuración (supervisores y administradores) pueden ver todas las cajas de las sucursales de las empresas seleccionadas.

---

**¿Cuánto tiempo es válido el link de primer acceso?**
24 horas desde que el administrador lo genera. Si venció, pedile al administrador que genere uno nuevo desde el detalle del usuario.

---

**¿Cómo sé qué permisos tengo?**
Consultá con tu administrador. Desde el detalle de tu usuario (si tenés permiso de lectura en usuarios) podés ver qué permisos están activos y de dónde vienen (rol o personalizado).

---

**¿La selección de empresa afecta solo el dashboard o a todo el sistema?**
Afecta a **todo el sistema**: cajas, deudas, pagos, movimientos bancarios, acreditaciones, reportes, alertas y configuración. Todo lo que muestra Gerayse pertenece exclusivamente a las empresas seleccionadas en el header.

---

*Manual de Usuario — Gerayse — Mayo 2026*
*Para reportar errores o solicitar soporte, contactar al administrador del sistema.*

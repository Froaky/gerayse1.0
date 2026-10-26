# Asistencia del personal: manual para administración

Este manual es para quien administra la asistencia del personal en Gerayse: ver cómo va cada local, cargar empleados, imprimir carnets, corregir marcas y sacar el Excel de fin de mes. Todo se hace desde el botón **Asistencia** del menú, con su usuario de siempre, desde la computadora o el celular.

## 1. Antes de empezar: los permisos

En la asistencia hay dos permisos, que se dan en la ficha de cada usuario (o en la del rol, para todo el rol) poniendo **Escritura: Sí** en la fila del permiso:

| Permiso | Para qué sirve |
| --- | --- |
| **Administrar asistencia** | Ver cómo va cada local, el padrón de empleados, los carnets, las correcciones de marcas y el Excel del mes. Es el suyo. |
| **Fichar asistencia** | Escanear los carnets en el local. Es el de los cajeros. |

Son independientes: quien ficha no corrige marcas ni ve los otros locales, y quien administra no ficha salvo que tenga también el permiso de fichar.

**En qué local ficha cada cajero.** Cada marca queda registrada en el local donde se fichó:

- Un cajero ficha siempre en su **Sucursal base** (se carga en la ficha del usuario). No puede elegir otro local: la pantalla de fichar le muestra solo el suyo.
- Si un cajero trabaja en más de un local, en su ficha marque esos locales en **Otros locales donde ficha asistencia**. Entonces, al fichar, elige entre su sucursal base y esos locales, y ningún otro.
- Usted, como administradora, elige en la pantalla de fichar cualquier local de sus empresas.

Si un cajero ve el aviso "Tu usuario no tiene un local asignado", falta cargarle la Sucursal base.

## 2. Ver cómo va cada local

En **Asistencia → Por local** está el día de hoy, local por local:

- Arriba, cuántas personas están **trabajando ahora** y cuántas **ficharon hoy** en total.
- En cada local, la lista de quienes ficharon ahí:
  - en verde, **Trabajando desde** la hora de entrada, con lo que lleva trabajado;
  - **Salió** a tal hora, con las horas del día;
  - **Corregida**, si alguna marca se cargó a mano;
  - **Sin salida**, en un día pasado, si alguien entró y no registró la salida.
- Al final de cada local, **"sin marcas hoy"**: quiénes tienen ese local como habitual y todavía no ficharon. Toque la línea para ver los nombres.

Para ver otro día, elija la fecha en **Día** y toque **Ver**. Para ver un solo local, elíjalo en **Local**. El botón **Hoy** vuelve al día de hoy.

Cada persona tiene un botón **Marcas** que muestra sus marcas de ese día, por si hay que corregir algo.

Las horas se cuentan igual que en el Excel del mes: de cada entrada a su salida. Una salida después de medianoche cuenta en el día de la entrada.

## 3. Empleados

En **Asistencia → Empleados** está el padrón, con buscador por nombre o DNI y filtros por empresa, local y estado.

- **Nuevo empleado**: nombre y apellido, empresa, local habitual y, si lo tiene, DNI. El DNI no es obligatorio, pero ayuda: sin DNI, Gerayse no deja cargar dos veces el mismo nombre en la misma empresa. Si hay dos personas con el mismo nombre, cargue el DNI de las dos. El DNI se puede agregar después con **Editar**.
- **Cargar desde un listado**: suba un Excel con las columnas Apellido, Nombre, Local (y DNI si lo tiene). Toque **Revisar listado**: Gerayse muestra qué filas están listas y cuáles tienen un problema, sin guardar nada. Después toque **Cargar**. Si sube dos veces el mismo listado, no se duplica nadie.
- **Editar**: corrige los datos de un empleado.
- **Dar de baja**: el empleado deja de poder fichar, pero no se borra: sus marcas siguen en los Excel de los meses trabajados. Se puede **reactivar**.

## 4. Carnets

- **Imprimir todos**: en **Asistencia → Carnets** elija empresa o local (o ninguno, para todos) y toque **Descargar carnets**. Sale un PDF A4 con 10 carnets por hoja, del tamaño de una tarjeta, para imprimir y recortar.
- **Uno solo**: desde el padrón, botón **Carnet** del empleado (por ejemplo, para alguien nuevo).
- **Carnet perdido o roto**: desde el padrón, **Anular carnet** con el motivo, y después **Carnet** para imprimir el nuevo. El carnet viejo deja de servir; si alguien lo escanea, la pantalla avisa que está anulado.

Cada carnet tiene el QR y, abajo, un **código de 6 números**. Si la cámara no lee el QR, el cajero tipea esos 6 números. El carnet no lleva el DNI.

## 5. Corregir marcas

En **Asistencia → Marcas** se ven las marcas con filtros por empleado, local y fechas.

- **Agregar marca**: para cuando alguien se olvidó el carnet, llegó antes que el cajero o no había internet. Indique empleado, entrada o salida, fecha, hora, local y motivo. Queda señalada como **Corregida**, con su usuario.
- **Anular**: para una marca equivocada, por ejemplo si se escaneó el carnet de un compañero. Indique el motivo. La marca no se borra: deja de contar, y se ve con el filtro **Mostrar anuladas**.

No se pueden cargar marcas a futuro ni repetir una que ya está.

## 6. Excel del mes

En **Asistencia → Excel del mes** elija el mes (y, si quiere, una empresa o un solo empleado) y toque **Descargar Excel**. Hay una hoja por empleado con todos los días: entradas y salidas, horas del día, observación (Corregida, Sin salida, Sin marcas) y el total del mes. Las marcas anuladas no aparecen.

## 7. Fichar usted misma

Si tiene también el permiso de fichar, en **Asistencia → Fichar** elija arriba el local donde está y toque **Usar este local**. Después, **Usar la cámara** y apunte al carnet. Encima de la imagen aparece en verde la entrada o la salida con el nombre y la hora, con un sonido corto. Si la cámara no lee, escriba los 6 números del carnet y toque **Registrar**.

## 8. Situaciones frecuentes

| Situación | Qué hacer |
| --- | --- |
| Entró un empleado nuevo | **Nuevo empleado** y después **Carnet** para imprimirle el suyo. |
| Alguien perdió el carnet | **Anular carnet** con el motivo y reimprimir con **Carnet**. |
| Alguien dejó de trabajar | **Dar de baja**. Sus marcas quedan para los Excel. |
| Alguien se olvidó de fichar | **Agregar marca** con la hora real y el motivo. |
| En Por local aparece "Sin salida" | Revise con el local a qué hora se fue y agregue la salida. |
| Un cajero no puede fichar | Revise que tenga el permiso **Fichar asistencia** y una **Sucursal base**. |
| Un cajero necesita fichar en otro local | Marque ese local en **Otros locales donde ficha asistencia**, en su ficha. |
| El QR no se lee | Tipear los 6 números del carnet. Si pasa seguido, reimprima el carnet. |

## 9. Respaldo

Los datos de asistencia (empleados, carnets y marcas) se respaldan todos los días fuera del servidor. No hace falta hacer nada.

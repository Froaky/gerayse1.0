# Manual de usuario: Asistencia del personal con QR

Asistencia registra la entrada y la salida de cada empleado con un carnet QR, dentro de Gerayse.
Se entra desde el botón **Asistencia** del menú, con el mismo usuario de siempre.

Hay dos tipos de uso, cada uno con su permiso:

- **Fichar asistencia**: para los cajeros. Permite escanear los carnets en el local.
- **Administrar asistencia**: para administración. Permite manejar el padrón de empleados, los
  carnets, las correcciones de marcas y el Excel del mes.

Los permisos se habilitan como los demás de Gerayse: en la ficha del rol (para todo el rol) o en
la ficha del usuario (para una persona), poniendo **Escritura: Sí** en la fila del permiso. Son
independientes: quien ficha no corrige marcas y quien administra no ficha, salvo que tenga los dos.

Cada cajero que ficha tiene que tener cargada su **Sucursal base** en la ficha del usuario: es el
local que queda registrado en cada marca.

## 1. Fichar (cajeros)

1. Entre a **Asistencia** y presione **Fichar**. Arriba se indica el local donde está fichando:
   es el local asignado a su usuario.
2. Escanee el carnet del empleado de una de estas formas:
   - **Con el celular o la tablet**: presione **Usar la cámara** y permita el acceso a la cámara
     cuando el navegador lo pida. Ponga el QR del carnet frente a la cámara; cada lectura se
     registra sola, sin tocar nada más. Para terminar, presione **Apagar la cámara**.
   - **Con un lector QR enchufado a la computadora**: con la pantalla de fichar abierta, apunte el
     lector al carnet. El código se escribe y se registra solo; el campo queda listo para el
     siguiente carnet.
   - **Si el QR no se lee**: escriba el código que figura debajo del nombre en el carnet y
     presione Enter.
3. La pantalla muestra el nombre del empleado, si fue **Entrada** o **Salida** y la hora.

Cómo decide el sistema:

- La primera marca es la entrada y la siguiente, la salida. En un turno cortado se ficha cada
  vez que el empleado llega y cada vez que se va.
- La hora es la del sistema al recibir la marca; nadie la puede cambiar desde esta pantalla.
- Si el mismo carnet se escanea de nuevo dentro de los 2 minutos, no se registra otra marca.
- Si una entrada quedó abierta más de 16 horas, el próximo escaneo es una entrada nueva.
- Un empleado puede fichar en cualquier local; la marca guarda el local donde se fichó.

Avisos posibles:

| Aviso | Qué hacer |
| --- | --- |
| Ese carnet está anulado | El empleado tiene que usar su carnet nuevo. Avise a administración si no lo tiene. |
| Ese carnet no existe en el padrón / Ese QR no es un carnet | Se escaneó otro QR o un carnet que no corresponde. |
| Está dado de baja: no puede fichar | Consulte con administración. |
| Ya quedó registrada hace unos segundos | No hace falta hacer nada: la marca ya está. |
| Tu usuario no tiene un local asignado | Administración tiene que cargar la Sucursal base en la ficha de su usuario. |
| El navegador no dio permiso para usar la cámara | Habilite la cámara en los permisos del sitio del navegador y vuelva a tocar **Usar la cámara**. Mientras tanto puede escribir el código del carnet. |

Si un empleado se olvidó el carnet o llegó antes que el cajero, no lo fiche con otro carnet:
avise a administración para que cargue la marca a mano (punto 4).

## 2. Padrón de empleados (administración)

En **Asistencia → Empleados** está el padrón. Se puede buscar por nombre o DNI y filtrar por
empresa, local y estado (activos, dados de baja o todos).

- **Nuevo empleado**: nombre y apellido, empresa, local habitual y, si lo tiene, DNI (solo
  números; los puntos se sacan solos), y **Guardar**. Si carga el DNI, no se puede repetir entre
  empleados activos. Sin DNI, no se puede repetir el nombre dentro de la misma empresa: si hay dos
  personas con el mismo nombre, cargue el DNI de las dos. El DNI se puede agregar después con
  **Editar**.
- **Cargar desde un listado**: para cargar muchos empleados de una vez.
  1. Prepare un Excel (.xlsx) o un CSV de hasta 2 MB con una fila de títulos y un empleado por
     fila. Los títulos pueden ser "Nombre y apellido" (o "Apellido" y "Nombre" en columnas
     separadas), "Local" o "Sucursal", y opcionalmente "DNI" (o "Documento") y "Empresa". El local
     se reconoce por su código o por su nombre.
  2. Súbalo y presione **Revisar listado**. El sistema muestra qué filas están listas para cargar
     y cuáles tienen un problema, con el motivo. Todavía no se guardó nada.
  3. Presione **Cargar** para guardar las filas que están bien. Las que tenían un problema se
     pueden corregir y subir en un archivo aparte, o cargar de a una con **Nuevo empleado**.
- **Editar**: corrige los datos de un empleado.
- **Dar de baja**: el empleado deja de poder fichar, pero no se borra: sus marcas siguen saliendo
  en el Excel de los meses trabajados. Se puede **reactivar** después.

## 3. Carnets

- **Imprimir carnets**: en **Asistencia → Carnets** elija la empresa y/o el local y presione
  **Descargar carnets**. Se descarga un PDF A4 con 10 carnets por hoja, del tamaño de una
  tarjeta, para imprimir y recortar. Salen solo los empleados activos.
- **Carnet de un empleado**: desde el padrón, botón **Carnet**, para imprimir uno solo (por
  ejemplo, para un empleado nuevo).
- **Anular carnet**: si un carnet se pierde o se rompe, desde el padrón presione **Anular carnet**
  e indique el motivo. El carnet viejo deja de servir y no se puede deshacer. Después imprima el
  nuevo con el botón **Carnet**. Si alguien escanea el carnet anulado, la pantalla de fichar lo
  avisa.

El QR solo contiene un código; no lleva el nombre ni el DNI del empleado.

## 4. Corregir marcas

En **Asistencia → Marcas** se ven las marcas por empleado, local y fechas. Desde el padrón, el
botón **Marcas** de cada empleado abre directamente las suyas.

- **Agregar marca**: para un olvido o cuando el empleado llegó antes que el cajero. Indique el
  empleado, si es entrada o salida, la fecha, la hora, el local y el motivo. La marca queda
  señalada como **corregida**, con su usuario y la fecha de carga.
- **Anular**: para una marca equivocada (por ejemplo, se escaneó el carnet de un compañero).
  Indique el motivo. La marca no se borra: deja de contar y se puede ver con el filtro
  **Mostrar anuladas**.

No se pueden cargar marcas a futuro ni repetir una marca ya cargada.

## 5. Excel del mes

En **Asistencia → Excel del mes** elija el mes y, si quiere, una empresa o un solo empleado, y
presione **Descargar Excel**.

- Hay una hoja por empleado con todos los días del mes: entradas y salidas, horas del día,
  observación y el total del mes.
- Entran todos los empleados activos y los dados de baja que tengan marcas en ese mes.
- Las observaciones posibles son:
  - **Corregida**: alguna marca del día se cargó a mano.
  - **Sin salida**: hay una entrada sin su salida; esas horas no se suman.
  - **Salida sin entrada**: hay una salida sin su entrada; tampoco suma.
  - **Sin marcas**: ese día no fichó.
- Una salida después de medianoche cuenta en el día de la entrada.
- Las marcas anuladas no aparecen.

Para corregir un día, agregue o anule la marca (punto 4) y vuelva a descargar el Excel.

## 6. Respaldo

Los datos de asistencia (empleados, carnets y marcas) se respaldan todos los días fuera del
servidor. No hace falta hacer nada.

# Guia de usuario: cajeros, validacion de efectivo y gastos como deuda

> Estado: funcionalidad en desarrollo (rama `staging`). Esta guia describe como va a funcionar
> el sistema cuando se active, para poder explicarselo al cliente. Se actualiza junto con el
> desarrollo. Ultima actualizacion: 2026-07-14.

## Que cambia, en una frase

El efectivo que entrega un empleado al cerrar su caja **no cuenta para el negocio hasta que una
persona autorizada lo reciba fisicamente y lo valide en el sistema**; y los gastos ya no salen
de la caja: quedan registrados como **deuda pendiente** que administracion paga despues.

## 1. Nuevos permisos configurables

Cada usuario ya tiene una ficha con permisos de Lectura y Escritura por modulo. A eso se suma
un nuevo tipo de permiso: el **permiso por accion**. La primera accion disponible es:

- **Validar efectivo**: habilita a ver las cajas pendientes de validacion y a validarlas o
  rechazarlas.

Como todos los permisos, se puede asignar:
- por **rol** (por ejemplo, que todo el rol Administracion valide), o
- por **usuario puntual** (por ejemplo, solo Tais valida, sin importar su rol).

No hace falta crear roles nuevos para esto: es un tilde mas en la ficha de permisos.

## 2. El usuario cajero

Un cajero es un usuario con permisos acotados:

- puede abrir, cargar y cerrar **solo la caja de su sucursal** (la que tiene asignada)
- no puede entrar a tesoreria, configuracion ni usuarios
- no puede validar efectivo (salvo que se le de ese permiso a proposito)
- no puede cargar cajas de otra sucursal, aunque conozca la direccion de la pantalla:
  el sistema lo rechaza igual

Los usuarios que ya existen siguen funcionando exactamente igual que hoy.

## 3. Validacion de efectivo, paso a paso

1. El empleado abre su caja y carga las ventas del dia como siempre: efectivo, tarjeta,
   debito, credito, QR, apps. **Mientras la caja esta abierta, los tableros muestran la
   operacion del dia en vivo, como hasta ahora.**
2. Al terminar el turno, el empleado **cierra la caja** y **entrega el efectivo fisico** a la
   persona responsable.
3. Desde el momento del cierre, si la caja tuvo **efectivo**, queda **pendiente de
   validacion**: sus numeros **dejan de sumar en todos los reportes, dashboards y saldos**
   del sistema. Ni el efectivo, ni las tarjetas, ni nada de esa caja. La caja se ve, con su
   estado bien visible, pero no cuenta. La plata fisica tampoco entra a tesoreria todavia.
4. La responsable (con el permiso *Validar efectivo*) entra a **Validaciones** (pendientes de
   validacion). Ahi ve cada caja pendiente con su sucursal, fecha, turno, responsable y el
   **efectivo declarado al cierre** contra el **esperado segun el sistema**.
5. Cuenta el efectivo que le entregaron y lo coteja contra lo que dice la pantalla.
6. **Si coincide**: aprieta **Validar**. Desde ese momento la caja contabiliza normalmente en
   todo el sistema y el efectivo entra a tesoreria. Queda registrado quien valido y cuando.
7. **Si no coincide**: aprieta **Rechazar** e indica el motivo. La caja queda observada y sigue
   sin contar. Se corrige la carga con la correccion auditada de siempre (motivo, valor
   anterior y valor nuevo) y se vuelve a validar.

## 4. Cajas sin efectivo

Si una caja **no tiene ningun movimiento de efectivo** (por ejemplo, solo ventas con tarjeta y
QR), **cuenta normal desde el primer momento**: no hay nada fisico que validar, asi que no pasa
por el paso de validacion.

## 5. Gastos desde la caja: ahora son deuda

Cambio importante en la operatoria de gastos:

- Cuando el empleado registra un gasto desde su caja, **no sale efectivo de la caja**.
- El gasto queda registrado como una **deuda pendiente** (con rubro, sucursal y periodo), en la
  misma bandeja de cuentas por pagar que ya usa administracion.
- **Administracion/tesoreria paga esa deuda despues**, por el medio que corresponda, como paga
  cualquier otra deuda.

Que significa esto para los numeros:

- La **situacion economica** (cuanto gasto el negocio en el periodo) toma el gasto **apenas se
  carga**, porque la deuda ya existe.
- La **situacion financiera** (cuanta plata salio de verdad) lo toma **recien cuando se paga**.
- El gasto **nunca se cuenta dos veces**: una sola deuda, un solo pago.

El egreso de efectivo tradicional (sacar plata fisica de una caja, por ejemplo para un
traspaso) sigue existiendo, pero queda reservado a quien tenga ese permiso; no es el flujo del
cajero.

## 6. Que NO cambia

- Las cajas ya cerradas antes de esta version cuentan como siempre; ningun numero historico
  se mueve.
- El cierre de caja, las diferencias y sus justificaciones siguen funcionando igual.
- Los reportes son los mismos; solo que ahora excluyen las cajas que todavia no fueron
  validadas.

## Preguntas frecuentes

**¿Quien puede validar?**
Cualquier usuario que tenga el permiso *Validar efectivo*, sin importar el rol. El permiso se
da por rol o por usuario puntual.

**¿Puedo validar mi propia caja?**
Si tenes el permiso, si. Esta pensado para que administracion valide lo que recibe; si un dia
carga una caja alguien de administracion, puede validarla.

**¿Que pasa si el empleado cargo solo tarjeta?**
La caja cuenta normal sin validacion: no hay efectivo fisico que confirmar.

**¿Y mientras la caja esta abierta durante el dia?**
Cuenta normal en los tableros del dia, para no perder la vista en vivo de la operacion. El
freno arranca cuando la caja se cierra y el efectivo queda en manos de alguien sin validar.

**¿La caja pendiente "desaparece" de los reportes?**
No desaparece: se ve en los listados con su estado *pendiente de validacion*. Lo que no hace es
sumar a ningun total hasta que se valide.

**¿Que pasa si el efectivo no coincide?**
Se rechaza con motivo, se corrige la carga con la correccion auditada (queda registrado que se
cambio, quien y por que) y se vuelve a validar. Nada se borra.

**¿El cajero puede pagar un gasto con la plata de la caja?**
No. El gasto se carga como deuda y lo paga administracion. Si hace falta sacar efectivo fisico
de una caja, eso es un egreso tradicional y requiere otro permiso.

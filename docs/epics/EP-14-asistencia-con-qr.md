# EP-14 Asistencia del Personal con QR

Aprobada por el cliente el 2026-10-21 (Tais dio el OK al presupuesto "version simple").
Sale de `PROPUESTA-EP-14-asistencia-con-qr.md`; esta epica es el alcance que se implementa.
Desarrollo en rama `staging`; no se integra a `main` sin OK explicito de Mateo.

## Para retomar desde cualquier computadora

1. `git fetch origin` y `git switch staging` (o `git pull` si ya estas en `staging`). Leer esta
   epica y la entrada de EP-14 en `context.md`. Con eso alcanza: no hace falta el PDF.
2. El PDF del presupuesto NO esta en el repo porque tiene precios y el repo es publico. Todo lo
   que el PDF promete esta transcripto en "Lo que se prometio al cliente". Si algo de esta epica
   lo contradice, manda el PDF: preguntarle a Mateo.
3. Antes de la primera linea de codigo hay que cerrar la decision D1 (donde vive el codigo del
   servicio). El resto de las decisiones tecnicas ya estan tomadas abajo.
4. Marcar cada historia con `[x]` recien cuando tenga tests y este en `staging`, y anotar en
   `context.md` que quedo hecho, asi la proxima computadora sabe donde seguir.
5. Fuera de alcance por decision de Mateo: piloto, acompanamiento presencial, login de respaldo,
   horarios, tardanzas y todo lo de "No incluye".

## Objetivo

Reemplazar el cuaderno de asistencia: cada empleado tiene un carnet con QR, el cajero lo escanea
desde Gerayse al llegar y al irse, la hora la pone el sistema y a fin de mes administracion
descarga un Excel con los dias y horarios de cada empleado. El servicio corre con hosting y base
de datos propios, aparte de Gerayse, con respaldo diario.

## Lo que se prometio al cliente (PDF del 2026-10-21)

- carnet con QR para los 85 empleados (50 ARMADI, 35 Vivre), listo para imprimir; el QR no lleva
  datos personales y si un carnet se pierde se anula y se imprime otro
- fichaje de entrada y salida en los 6 locales, con lector QR USB 2D en la compu de la caja o con
  la camara de un celular, con Gerayse abierto en el navegador y sin instalar aplicaciones
- la primera marca del dia es la entrada y la siguiente la salida; con turno cortado se repite
- la hora la pone el sistema, no la compu ni el empleado
- si alguien se olvido de fichar, administracion agrega o corrige la marca y queda registrado
  quien lo hizo y cuando; la marca sale como "Corregida"
- Excel por mes con una hoja por empleado: dias, entradas, salidas y horas del dia, con total
- se usa desde Gerayse, con el mismo usuario, sin abrir otro programa ni volver a iniciar sesion
- el servicio corre en su propio servidor, con su propia base de datos y un respaldo diario fuera
  del servidor; por eso tiene un mantenimiento mensual por local, aparte del de Gerayse
- carga inicial de los empleados desde el listado del cliente y manual de usuario de todo
- arranque definitivo de a un local o todos juntos, como elija el cliente
- NO se prometio: seguir fichando si Gerayse se cae (se saco a pedido de Mateo)

## Incluye

- servicio de asistencia con deploy, base y respaldo propios
- acceso desde el menu de Gerayse con el mismo usuario
- padron de empleados con carga inicial desde un listado
- carnets con QR imprimibles, anulables y reimprimibles
- pantalla de fichaje con lector QR y con camara de celular
- correccion auditada de marcas por administracion
- Excel mensual por empleado
- carga de los empleados y manual de usuario para cajeros y administracion

## No incluye todavia

- horarios por empleado, grilla semanal, tardanza, tolerancia u horas extra
- avisos en pantalla, faltas, justificaciones o presentismo
- resumenes o reportes por local dentro del sistema
- calculo de sueldos o montos
- login propio del servicio o fichaje con Gerayse caido
- fichaje del empleado desde su propio celular, biometria o fotos
- migrar al sistema lo que ya esta en las planillas
- lectores, celulares e impresion de carnets (los pone el cliente)

## Decisiones tecnicas

- D1 PENDIENTE: donde vive el codigo del servicio. Recomendado: repo nuevo privado con un Django
  chico. Alternativa: carpeta propia en este repo (que es publico) con deploy aparte. En Gerayse,
  en cualquier caso, solo va la entrada del menu, los permisos y la emision del token.
- D2 hosting: servicio web y Postgres propios en Railway, en un proyecto aparte del de Gerayse,
  con dominio propio y HTTPS (la camara del celular lo exige).
- D3 acceso: Gerayse firma un token HMAC-SHA256 con un secreto compartido (variable de entorno en
  los dos lados), de vida corta (60 segundos) y de un solo uso, con usuario, nombre, sucursal base
  y permisos de asistencia. El servicio lo valida y abre su propia sesion. No hay otro login.
- D4 respaldo: `pg_dump` diario a un almacenamiento externo compatible con S3 (a elegir al montar
  D2), con retencion de 30 dias y aviso a soporte si un respaldo falla.
- D5 zona horaria `America/Argentina/Buenos_Aires`, igual que Gerayse.
- D6 lectura por camara: libreria JS de QR servida desde los estaticos del servicio, sin CDN
  (`BarcodeDetector` no existe en Safari de iPhone).
- D7 Excel con `openpyxl`, como dependencia del servicio (Gerayse no la necesita).
- D8 bases separadas: el servicio no escribe en la base de Gerayse ni Gerayse en la del servicio.

## Reglas de negocio

- la hora de una marca escaneada es la del servidor del servicio al recibirla; nadie la edita
- la unica forma de registrar otra hora es una marca manual de administracion, con motivo; queda
  senalada como corregida
- ninguna marca se borra: se anula con motivo, usuario y fecha, y deja de contar
- el tipo de marca se decide por empleado: si tiene una entrada abierta de menos de 16 horas, el
  escaneo es su salida; si no, es una entrada nueva
- una entrada abierta de mas de 16 horas queda como "sin salida" y el proximo escaneo es entrada
- el mismo carnet escaneado de nuevo dentro de los 2 minutos no genera marca
- un carnet anulado, un codigo desconocido o un empleado dado de baja no generan marca y la
  pantalla dice por que
- el QR contiene solo un codigo aleatorio; nunca nombre ni DNI
- toda marca guarda el local donde se ficho, que es la sucursal base del cajero en Gerayse
- un empleado puede fichar en cualquier local
- las horas del dia suman los tramos entrada-salida cuya entrada cae en ese dia; una salida despues
  de medianoche cuenta en el dia de la entrada; un tramo sin salida no suma y queda senalado
- fichar y administrar asistencia son permisos por accion separados en la matriz de Gerayse; el
  servicio rechaza en backend lo que el token no habilita
- datos personales minimos: nombre, DNI, empresa y local habitual

## User Stories

### [ ] US-14.1 Servicio de asistencia con hosting y respaldo propios

Como administracion
Quiero que la asistencia viva en un servicio con servidor y base de datos propios, respaldados
todos los dias
Para que un problema en Gerayse o en el servidor no haga perder las marcas

Criterios:
- el servicio esta desplegado aparte de Gerayse, con su propia base Postgres y dominio con HTTPS
- se genera un respaldo completo por dia fuera del servidor y se guardan los ultimos 30
- si un respaldo falla, soporte recibe un aviso
- una restauracion de prueba desde un respaldo reconstruye empleados, carnets y marcas
- el procedimiento de restauracion queda escrito en el repo del servicio
- estimado: 7 h

### [ ] US-14.2 Entrar a Asistencia desde Gerayse con el mismo usuario

Como cajero o administracion
Quiero entrar a Asistencia desde el menu de Gerayse sin volver a iniciar sesion
Para trabajar en un solo sistema y no perder tiempo en otra pantalla de login

Criterios:
- la matriz de permisos de Gerayse suma dos acciones: fichar asistencia y administrar asistencia
- la entrada "Asistencia" del menu solo aparece a quien tiene alguna de las dos
- Gerayse emite el token de D3 y el servicio abre sesion con el usuario, su sucursal base y sus
  permisos
- un token vencido, adulterado o ya usado se rechaza y no abre sesion
- el servicio no tiene login propio: entrar directo a su direccion sin token no abre sesion
- un usuario sin permiso de administrar no ve ni puede usar padron, carnets, correcciones ni Excel
  aunque conozca la URL
- estimado: 3-4 h

### [ ] US-14.3 Padron de empleados con carga inicial desde un listado

Como administracion
Quiero dar de alta, editar y dar de baja empleados, y cargar los 85 de una vez desde un listado
Para tener a todo el personal en el sistema sin tipearlo uno por uno

Criterios:
- cada empleado tiene nombre y apellido, DNI, empresa (ARMADI o Vivre) y local habitual
- el DNI no se repite entre empleados activos
- la carga desde un CSV o Excel informa fila por fila que entro y que se rechazo y por que, y no
  carga a medias un empleado con datos invalidos
- dar de baja no borra: el empleado deja de poder fichar y sus marcas siguen en los Excel
- el listado se filtra por empresa, local y activos/baja
- estimado: 4 h

### [ ] US-14.4 Carnets con QR para imprimir, anular y reimprimir

Como administracion
Quiero generar los carnets con QR en PDF y anular el de quien lo pierde
Para que cada empleado tenga su carnet y un carnet perdido no sirva mas

Criterios:
- al dar de alta un empleado se le asigna un codigo aleatorio, sin datos personales
- se descarga un PDF A4 con varios carnets por hoja (nombre, empresa y QR), de todos, de una
  empresa, de un local o de un empleado
- anular un carnet le asigna un codigo nuevo; el viejo deja de servir para fichar
- el carnet nuevo se puede reimprimir solo
- queda registrado quien anulo cada carnet y cuando
- estimado: 3 h

### [ ] US-14.5 Fichar entrada y salida con lector QR

Como cajero
Quiero escanear el carnet con el lector de la caja y ver al instante que quedo registrado
Para reemplazar el cuaderno sin frenar la fila

Criterios:
- la pantalla de fichaje queda lista para escanear: el lector "tipea" el codigo y Enter, sin
  clics ni confirmaciones
- se registra entrada o salida segun las reglas, con la hora del servidor y el local del cajero
- la pantalla muestra en grande el nombre, si fue entrada o salida y la hora, y vuelve a quedar
  lista para el proximo carnet
- un doble escaneo dentro de 2 minutos, un carnet anulado, un codigo desconocido o un empleado
  de baja no generan marca y se explica en pantalla
- con turno cortado, las marcas alternan entrada y salida en el mismo dia
- tests: alternancia, 16 horas, doble escaneo, medianoche, rechazos y permiso
- estimado: 4 h

### [ ] US-14.6 Fichar con la camara del celular

Como cajero
Quiero escanear el carnet con la camara del celular, con Gerayse abierto en el navegador
Para fichar en un local que no tiene lector

Criterios:
- la misma pantalla ofrece leer con la camara; registra exactamente igual que con el lector
- funciona en Chrome de Android y en Safari de iPhone, sin instalar nada
- si el navegador no da permiso de camara, la pantalla lo dice y sigue sirviendo con el lector
- se prueba en un Android y en un iPhone reales antes de cerrar la historia
- estimado: 3 h

### [ ] US-14.7 Corregir marcas olvidadas o mal hechas

Como administracion
Quiero agregar una marca con la hora real o anular una marca equivocada, con motivo
Para que el Excel refleje la asistencia real cuando alguien no ficho o llego antes que el cajero

Criterios:
- agregar una marca pide empleado, fecha, hora, si es entrada o salida, local y motivo
- anular una marca pide motivo; la marca deja de contar pero no se borra
- toda correccion guarda quien la hizo y cuando
- en el Excel la marca agregada sale como "Corregida" y la anulada no aparece
- un cajero sin permiso de administrar no puede corregir
- estimado: 3 h

### [ ] US-14.8 Excel del mes por empleado

Como administracion
Quiero descargar un Excel del mes con una hoja por empleado
Para tener los dias y horarios de cada uno sin pasar nada a mano

Criterios:
- se elige el mes y, opcionalmente, la empresa; tambien se puede bajar el de un solo empleado
- cada hoja lista todos los dias del mes: entradas y salidas en pares, horas del dia y una
  observacion ("Corregida", "Sin salida", "Sin marcas")
- las horas del dia y el total del mes siguen las reglas (medianoche y tramos sin salida)
- entra todo empleado activo y todo dado de baja que tenga marcas ese mes
- tests: turno cortado, medianoche, sin salida, marca corregida, marca anulada y mes sin marcas
- estimado: 5 h

### [ ] US-14.9 Arranque con los empleados cargados y manual de usuario

Como administracion y cajeros
Queremos arrancar con los 85 empleados cargados, los carnets listos y un manual
Para dejar el cuaderno desde el primer dia sin depender de alguien en el local

Criterios:
- los empleados del listado del cliente estan cargados y sin rechazos pendientes
- el PDF de carnets de todos esta entregado para imprimir
- el manual de usuario cubre fichar (lector y celular), padron, carnets, correcciones y Excel
- el arranque es definitivo, de a un local o todos juntos segun elija el cliente; sin piloto
- el mantenimiento mensual de cada local corre desde que ese local empieza a fichar
- estimado: 4 h

## Dependencias

- `EP-09` y `EP-13`: usuarios, matriz de permisos por accion y sucursal base del cajero
  (implementadas); US-14.2 suma dos acciones a esa matriz
- D1 cerrada antes de US-14.1
- del cliente: el listado de empleados (nombre, DNI, empresa y local) antes de US-14.9 y los
  lectores o celulares antes del arranque
- falta confirmar cual de las 7 sucursales activas de Gerayse no ficha: el presupuesto habla de 6

## Orden tecnico sugerido

1. D1, despues US-14.1: sin servicio y respaldo no hay donde guardar nada
2. US-14.2: es la puerta de entrada y define permisos para todo lo demas
3. US-14.3 y US-14.4: padron y carnets; con esto el cliente ya puede imprimir
4. US-14.5 y US-14.7: fichar y corregir; juntas son lo minimo que reemplaza al cuaderno
5. US-14.8: el Excel, que es lo que administracion usa a fin de mes
6. US-14.6: la camara del celular, el riesgo de compatibilidad mas alto, aislado
7. US-14.9: carga real, manual y arranque

Total estimado: 36-37 h.

## Criterio de cierre

- un cajero entra desde Gerayse sin otro login, escanea un carnet con lector o con celular y la
  marca queda con la hora del servidor y su local, sin poder cambiarla
- administracion descarga el Excel de un mes y cada empleado tiene su hoja con dias, entradas,
  salidas, horas del dia y total, sin pasar nada a mano
- toda marca corregida o anulada tiene motivo, usuario y fecha
- existe un respaldo diario fuera del servidor y una restauracion probada
- hay tests de alternancia, doble escaneo, medianoche, rechazos, permisos y armado del Excel
- el cliente dejo el cuaderno en los locales que arrancaron

# EP-14 Asistencia del Personal con QR

Aprobada por el cliente el 2026-10-21 (Tais dio el OK al presupuesto "version simple").
Sale de `PROPUESTA-EP-14-asistencia-con-qr.md`; esta epica es el alcance que se implementa.
Decision de Mateo del 2026-10-21: se construye DENTRO de Gerayse, como app `asistencia`, con el
mismo deploy, la misma base y el mismo login. No hay servicio aparte.
Desarrollo en rama `feat/asistencia-qr` -> `staging`; no se integra a `main` sin OK explicito de Mateo.

## Para retomar desde cualquier computadora

1. `git fetch origin` y `git switch staging` (o `git pull` si ya estas en `staging`). Leer esta
   epica y la entrada de EP-14 en `context.md`. Con eso alcanza: no hace falta el PDF.
2. El PDF del presupuesto NO esta en el repo porque tiene precios y el repo es publico. Lo que
   promete esta transcripto en "Lo que se prometio al cliente", con la aclaracion de lo que Mateo
   decidio hacer distinto. Ante una duda de alcance, preguntarle a Mateo.
3. Las historias van en orden: la siguiente sin `[x]` es la que sigue.
4. Marcar cada historia con `[x]` recien cuando tenga tests y este en `staging`, y anotar en
   `context.md` que quedo hecho, asi la proxima computadora sabe donde seguir.
5. Fuera de alcance por decision de Mateo: piloto, acompanamiento presencial, servicio aparte,
   horarios, tardanzas y todo lo de "No incluye".

## Objetivo

Reemplazar el cuaderno de asistencia: cada empleado tiene un carnet con QR, el cajero lo escanea
desde Gerayse al llegar y al irse, la hora la pone el sistema y a fin de mes administracion
descarga un Excel con los dias y horarios de cada empleado. Los datos de asistencia se respaldan
todos los dias fuera del servidor.

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
- respaldo diario de la informacion fuera del servidor
- carga inicial de los empleados desde el listado del cliente y manual de usuario de todo
- arranque definitivo de a un local o todos juntos, como elija el cliente
- mantenimiento mensual por local, aparte del de Gerayse
- NO se prometio: seguir fichando si Gerayse se cae

Aclaracion: el PDF dice ademas que el servicio "corre en su propio servidor, con su propia base de
datos". Mateo decidio el 2026-10-21 hacerlo dentro de Gerayse y dejar el PDF como esta. No
construir un servicio, hosting ni base aparte.

## Incluye

- app `asistencia` dentro de Gerayse, con la entrada en el menu segun permisos
- dos permisos por accion nuevos: fichar asistencia y administrar asistencia
- padron de empleados con carga inicial desde un listado
- carnets con QR imprimibles, anulables y reimprimibles
- pantalla de fichaje con lector QR y con camara de celular
- correccion auditada de marcas por administracion
- Excel mensual por empleado
- respaldo diario de los datos de asistencia fuera del servidor, con restauracion probada
- carga de los empleados y manual de usuario para cajeros y administracion

## No incluye todavia

- horarios por empleado, grilla semanal, tardanza, tolerancia u horas extra
- avisos en pantalla, faltas, justificaciones o presentismo
- resumenes o reportes por local dentro del sistema
- calculo de sueldos o montos
- servicio, hosting o base de datos aparte de Gerayse
- fichaje del empleado desde su propio celular, biometria o fotos
- migrar al sistema lo que ya esta en las planillas
- lectores, celulares e impresion de carnets (los pone el cliente)

## Decisiones tecnicas

- D1 app `asistencia` en este repo, con el mismo deploy y la misma base que Gerayse.
- D2 permisos: dos entradas nuevas en `users.PermissionModule`, por accion como las de EP-13:
  fichar asistencia y administrar asistencia. Se asignan por rol (default) o por usuario
  (override) desde la matriz que ya existe. Los administradores del sistema las tienen, como
  todo permiso.
- D3 el empleado NO es un usuario de Gerayse: es un registro propio de `asistencia`. Su empresa y
  su local habitual apuntan a `cashops.Empresa` y `cashops.Sucursal`.
- D4 el local de una marca es la sucursal base del cajero que escanea (`User.sucursal_base`).
- D5 respaldo: volcado logico diario de los datos de la app `asistencia` (JSON comprimido) a un
  almacenamiento externo compatible con S3, con retencion de 30 dias, aviso si falla y comando de
  restauracion. El cron y el almacenamiento los configura el chat de servidor (Railway); este chat
  deja el comando, los tests y las instrucciones.
- D6 zona horaria `America/Argentina/Buenos_Aires`, la de Gerayse.
- D7 lectura por camara: libreria JS de QR servida desde `static/`, sin CDN (`BarcodeDetector`
  no existe en Safari de iPhone). La camara exige HTTPS, que Gerayse ya tiene.
- D8 Excel con `openpyxl` (dependencia nueva en `requirements.txt`). PDF de carnets con
  `reportlab`, que ya esta.
- D9 (2026-10-21) de momento se ficha con el celular: la camara es la forma principal desde el
  arranque (US-14.4). El lector USB queda para cuando los locales lo compren (US-14.8).

## Reglas de negocio

- la hora de una marca escaneada es la del servidor al recibirla; nadie la edita
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
- toda marca guarda el local donde se ficho; un empleado puede fichar en cualquier local
- las horas del dia suman los tramos entrada-salida cuya entrada cae en ese dia; una salida despues
  de medianoche cuenta en el dia de la entrada; un tramo sin salida no suma y queda senalado
- fichar y administrar son permisos separados; el backend rechaza lo que el permiso no habilita
  aunque se conozca la URL
- aislamiento por empresa (AGENTS.md seccion 8): administracion solo ve, carga, corrige y exporta
  empleados de sus empresas permitidas y del contexto de empresa activo; los forms no ofrecen
  empresas ni sucursales ajenas
- datos personales minimos: nombre, DNI, empresa y local habitual
- el DNI es unico entre activos de TODAS las empresas, pero ningun mensaje muestra el nombre de un
  empleado de una empresa fuera del alcance de quien carga
- un archivo subido nunca da 500: lo que no se puede leer se informa en pantalla

## User Stories

### [x] US-14.1 Entrar a Asistencia desde el menu de Gerayse segun permisos

Como cajero o administracion
Quiero ver Asistencia en el menu de Gerayse cuando tengo permiso, sin otro inicio de sesion
Para usar la asistencia dentro del mismo sistema

Criterios:
- la matriz de permisos suma "Fichar asistencia" y "Administrar asistencia", asignables por rol y
  por usuario como validar efectivo
- roles y usuarios existentes no reciben ninguna de las dos hasta que se las asignen (salvo los
  administradores del sistema, que tienen todo)
- la entrada "Asistencia" del menu aparece solo a quien tiene alguna de las dos
- la pantalla de inicio de Asistencia muestra solo lo que el usuario puede hacer
- quien no tiene ninguna de las dos recibe 403 aunque conozca la URL
- tests de matriz, menu y acceso
- estimado: 2 h
- hecha 2026-10-21: `asistencia/tests.py` + regresion de la ficha de rol en `users/tests.py`

### [x] US-14.2 Padron de empleados con carga inicial desde un listado

Como administracion
Quiero dar de alta, editar y dar de baja empleados, y cargar los 85 de una vez desde un listado
Para tener a todo el personal en el sistema sin tipearlo uno por uno

Criterios:
- cada empleado tiene nombre y apellido, DNI, empresa (ARMADI o Vivre) y local habitual de esa
  empresa
- el DNI no se repite entre empleados activos
- la carga desde un CSV o Excel informa fila por fila que entro y que se rechazo y por que, y no
  carga a medias un empleado con datos invalidos
- dar de baja no borra: el empleado deja de poder fichar y sus marcas siguen en los Excel
- el listado se filtra por empresa, local y activos/baja, y respeta el aislamiento por empresa
- estimado: 4 h
- hecha 2026-10-21: `asistencia/tests_padron.py`. La carga es en dos pasos (revisar sin guardar,
  confirmar con token y revalidando); la empresa sale del local; columnas "Nombre y apellido" o
  "Apellido" + "Nombre", "DNI"/"Documento", "Local"/"Sucursal" y "Empresa" opcional

### [x] US-14.3 Carnets con QR para imprimir, anular y reimprimir

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
- hecha 2026-10-21: `asistencia/tests_carnets.py`. Codigo `GA1` + 24 base32 al azar
  (`Empleado.codigo_carnet`); los anulados quedan en `CarnetAnulado` con el codigo viejo para que
  el fichaje diga "carnet anulado". PDF de 10 por A4 con el codigo tambien escrito, para tipearlo
  si la camara no lee

### [ ] US-14.4 Fichar entrada y salida con la camara del celular

Como cajero
Quiero escanear el carnet con la camara de mi celular, con Gerayse abierto en el navegador, y ver
al instante que quedo registrado
Para reemplazar el cuaderno sin frenar la fila y sin comprar lectores

Criterios:
- la pantalla de fichaje esta pensada para el celular: abre la camara, lee el QR y registra sin
  clics ni confirmaciones; despues de cada carnet queda lista para el proximo
- funciona en Chrome de Android y en Safari de iPhone, sin instalar nada
- se registra entrada o salida segun las reglas, con la hora del servidor y el local del cajero
- la pantalla muestra en grande el nombre, si fue entrada o salida y la hora
- un doble escaneo dentro de 2 minutos, un carnet anulado, un codigo desconocido o un empleado
  de baja no generan marca y se explica en pantalla
- con turno cortado, las marcas alternan entrada y salida en el mismo dia
- un usuario sin sucursal base no puede fichar
- si el navegador no da permiso de camara, la pantalla lo dice y explica como habilitarlo
- la misma pantalla acepta tambien el codigo tipeado + Enter (es lo que manda un lector USB), asi
  la US-14.8 no necesita otra pantalla
- tests: alternancia, 16 horas, doble escaneo, medianoche, rechazos y permiso
- se prueba en un Android y en un iPhone reales antes de cerrar la historia
- estimado: 6 h
- avance 2026-10-21 (en staging): modelo `Marca`, reglas en `asistencia/fichaje.py`, pantalla
  `/asistencia/fichar/` con codigo tipeado + Enter (sirve para un lector USB) y lectura con la
  camara (`static/asistencia/fichar-camara.js` + jsQR 1.4.0 en `static/asistencia/vendor/`,
  Apache-2.0, bajado de npm con integridad verificada). Probado de punta a punta en un navegador
  con una camara simulada que mostraba un carnet real: lee, registra una sola marca y avisa si
  no hay permiso de camara. jsQR lee los 10 carnets de prueba (nombres largos incluidos).
  FALTA solo la prueba en un Android y un iPhone reales para cerrar la historia

### [x] US-14.5 Corregir marcas olvidadas o mal hechas

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
- hecha 2026-10-21: `asistencia/tests_marcas.py`. Pantalla `/asistencia/marcas/` (ultimos 7 dias
  por defecto, hasta 500), agregar y anular con motivo. Las anuladas no cuentan para el fichaje.
  "Corregida" en el Excel (origen MANUAL) y la anulada afuera quedan para US-14.6, que lee estos
  campos

### [x] US-14.6 Excel del mes por empleado

Como administracion
Quiero descargar un Excel del mes con una hoja por empleado
Para tener los dias y horarios de cada uno sin pasar nada a mano

Criterios:
- se elige el mes y, opcionalmente, la empresa; tambien se puede bajar el de un solo empleado
- cada hoja lista todos los dias del mes: entradas y salidas en pares, horas del dia y una
  observacion ("Corregida", "Sin salida", "Sin marcas")
- las horas del dia y el total del mes siguen las reglas (medianoche y tramos sin salida)
- entra todo empleado activo y todo dado de baja que tenga marcas ese mes
- solo empleados de las empresas permitidas
- tests: turno cortado, medianoche, sin salida, marca corregida, marca anulada y mes sin marcas
- estimado: 5 h
- hecho 21/10: `/asistencia/excel/` (`asistencia/excel.py`, openpyxl 3.1.5). Los tramos se arman
  con la regla de 16 h del fichaje sobre las marcas no anuladas, desde 16 h antes hasta 16 h despues
  del mes; la salida del dia 1 que cierra el mes anterior queda afuera. Horas en formato `[h]:mm`;
  un nombre que empieza con "=" queda como texto. Tests en `asistencia/tests_excel.py`

### [ ] US-14.7 Respaldo diario de la asistencia fuera del servidor

Como administracion
Quiero que los datos de asistencia se respalden todos los dias fuera del servidor
Para no perder empleados, carnets ni marcas si falla el servidor o la base

Criterios:
- un comando genera el respaldo de los datos de `asistencia` y lo sube al almacenamiento externo
- se guardan los ultimos 30 respaldos; los mas viejos se borran solos
- si el respaldo falla, el comando termina con error y avisa a soporte
- un comando restaura un respaldo y reconstruye empleados, carnets y marcas; esta probado con tests
- el procedimiento (variables, cron y restauracion) queda escrito para el chat de servidor
- estimado: 3-4 h

### [ ] US-14.8 Fichar con lector QR USB

Como cajero
Quiero escanear el carnet con un lector QR enchufado a la compu de la caja
Para fichar mas rapido cuando el local compre un lector

Criterios:
- la pantalla de fichaje de US-14.4 funciona en la compu de la caja con el lector: el lector
  "tipea" el codigo y Enter, sin clics; el campo queda enfocado para el proximo carnet
- registra exactamente igual que con la camara
- se prueba con un lector 2D real antes de cerrar la historia
- estimado: 1 h (la logica ya esta en US-14.4)

### [ ] US-14.9 Arranque con los empleados cargados y manual de usuario

Como administracion y cajeros
Queremos arrancar con los 85 empleados cargados, los carnets listos y un manual
Para dejar el cuaderno desde el primer dia sin depender de alguien en el local

Criterios:
- los empleados del listado del cliente estan cargados y sin rechazos pendientes
- los cajeros de los locales que arrancan tienen el permiso de fichar
- el PDF de carnets de todos esta entregado para imprimir
- el manual de usuario cubre fichar (lector y celular), padron, carnets, correcciones y Excel
- el arranque es definitivo, de a un local o todos juntos segun elija el cliente; sin piloto
- el respaldo diario esta andando y se probo una restauracion con datos reales
- estimado: 4 h

## Dependencias

- `EP-09` y `EP-13`: usuarios, matriz de permisos por accion y sucursal base del cajero
  (implementadas); US-14.1 suma dos acciones a esa matriz
- `EP-12`: contexto de empresa activa y empresas permitidas (implementada)
- chat de servidor: almacenamiento externo, variables y cron del respaldo (US-14.7)
- del cliente: el listado de empleados (nombre, DNI, empresa y local) antes de US-14.9 y los
  lectores o celulares antes del arranque
- falta confirmar cual de las 7 sucursales activas de Gerayse no ficha: el presupuesto habla de 6

## Orden tecnico sugerido

El de la numeracion: cada historia usa lo de la anterior.

1. US-14.1: permisos y entrada; todo lo demas cuelga de aca
2. US-14.2 y US-14.3: padron y carnets; con esto el cliente ya puede imprimir
3. US-14.4 y US-14.5: fichar con el celular y corregir; juntas son lo minimo que reemplaza al
   cuaderno
4. US-14.6: el Excel, que es lo que administracion usa a fin de mes
5. US-14.7: el respaldo, cuando ya existen todos los datos que hay que respaldar
6. US-14.8: el lector USB, solo si los locales lo compran; no frena el arranque
7. US-14.9: carga real, permisos de los cajeros, manual y arranque

Total estimado: 29-30 h.

## Criterio de cierre

- un cajero entra a Asistencia desde el menu de Gerayse, escanea un carnet con lector o con
  celular y la marca queda con la hora del servidor y su local, sin poder cambiarla
- administracion descarga el Excel de un mes y cada empleado tiene su hoja con dias, entradas,
  salidas, horas del dia y total, sin pasar nada a mano
- toda marca corregida o anulada tiene motivo, usuario y fecha
- existe un respaldo diario fuera del servidor y una restauracion probada
- hay tests de permisos, aislamiento por empresa, alternancia, doble escaneo, medianoche,
  rechazos y armado del Excel
- el cliente dejo el cuaderno en los locales que arrancaron

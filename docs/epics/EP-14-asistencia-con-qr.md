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

- ~~horarios por empleado, grilla semanal, tardanza, tolerancia u horas extra~~ y ~~faltas~~:
  entran con la ampliacion del 30/10 (US-14.10 a US-14.15, D18)
- feriados: el cliente los calcula a mano (decision de Mateo del 30/10)
- avisos en pantalla, justificaciones o presentismo
- resumenes o reportes por local dentro del sistema (fuera de la hoja Resumen del Excel)
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
- D10 (2026-10-26, pedido de Mateo) el DNI es opcional: el listado del cliente llego sin DNI y se
  arranca asi. Si se carga, no se repite entre activos de todas las empresas; sin DNI, no se
  repite el nombre entre activos de la misma empresa (evita la doble carga). Migracion
  `asistencia/0007_dni_opcional`; tests en `asistencia/tests_sin_dni.py`.
- D11 (2026-10-26, pedido de Mateo) quien administra asistencia elige en Fichar el local donde
  esta fichando (queda en la sesion; arranca en su sucursal base si tiene). El cajero sigue fijo
  en su sucursal base, sin selector (D4). El servicio revalida: solo quien administra, local
  activo de una empresa permitida. Tests en `asistencia/tests_fichar_local.py`.
- D12 (2026-10-26, pedido de Mateo) el codigo del carnet es de 6 digitos (no empieza con 0) y va
  escrito grande en el carnet ("483 920"): el anterior (GA1 + 24) era imposible de tipear. Nunca
  repite un codigo vigente ni anulado. Se resigna que sea imposible de adivinar: fichar exige un
  cajero logueado. La migracion `asistencia/0008_carnet_codigo_corto` da codigo nuevo a cada
  empleado y deja el anterior como carnet anulado (escanear uno viejo avisa "anulado"). Tests en
  `asistencia/tests_codigo_corto.py`.
- D13 (2026-10-26) al escanear con la camara el resultado se ve ENCIMA del video (verde, amarillo
  o rojo, con nombre y hora), con sonido corto y vibracion donde se puede: antes quedaba arriba,
  fuera de la vista en el celular, y Mateo no se enteraba de que habia fichado.
- D14 (2026-10-26, pedido de Mateo) el cajero con permiso de fichar ve solo su local, fijo, salvo
  que tenga mas de un local asignado: campo nuevo del usuario "Otros locales donde ficha
  asistencia" (`users.User.sucursales_asistencia`, migracion `users/0021`); con eso elige entre
  su sucursal base y esos, y ningun otro. Va aparte de "Sucursales adicionales para cargar deuda"
  para no habilitar deuda de rebote. Quien administra sigue eligiendo entre todos los de su
  alcance (D11).
- D15 (2026-10-26, pedido de Mateo) pantalla "Por local" (`/asistencia/por-local/`,
  `asistencia/por_local.py`): por dia y local, quien esta trabajando (desde que hora y cuanto
  lleva), quien salio y sus horas, corregidas, sin salida y quienes del local no ficharon. Misma
  regla de tramos que el Excel. Manuales con el formato del de julio (de vos):
  `docs/manual-asistencia-administracion.html` y `docs/manual-asistencia-cajeros.html`.
- D16 (2026-10-26, pedido de Mateo) "Carnet celular" en el padron
  (`/asistencia/empleados/<id>/carnet/celular/`, `generar_carnet_celular_pdf`): el carnet de un
  empleado en una hoja vertical de 70 x 124 mm, con forma de pantalla, para mandarlo por WhatsApp y
  mostrarlo en el celular. Mismo codigo vigente que el impreso, QR de 54 mm y los 6 numeros en
  Helvetica (los visores del celular cambian Courier por otra letra). Al anular el carnet, el del
  celular tambien deja de servir: hay que mandar el nuevo. Tests en
  `asistencia/tests_carnet_celular.py`.
- D17 (2026-10-27, pedido de Mateo) carnets celular de varios juntos: en el padron cada activo
  tiene una casilla, mas "Marcar todos" (marca los que muestra el filtro). "Bajar carnets celular"
  manda un POST a `/asistencia/carnets/celular/` y baja `carnets-celular.zip` con un PDF por
  persona, `carnet-celular-<nombre>.pdf`, listo para adjuntar en WhatsApp
  (`generar_zip_carnets_celular`). Si dos se llaman igual, el segundo suma el codigo de su local
  y, si tambien coincide, un numero: ningun archivo pisa a otro. Solo entran activos de las
  empresas del usuario; un id ajeno o de baja se ignora. POST y no GET para no chocar con el largo
  maximo de la URL con muchos marcados. Tests en `asistencia/tests_carnet_celular.py`.
- D18 (2026-10-30, pedido de Tais via Mateo) horarios, resumen, tardanzas, faltas y horas extra.
  Respuestas de Tais: los horarios cambian cada semana; las horas extra son lo que pasa de 8:30
  por dia; la tolerancia es la suma de las llegadas tarde del mes, que no pase de 30 minutos;
  puede haber gente que entre dos veces el mismo dia. Decisiones de Mateo:
  - nada hardcodeado: jornada diaria y tolerancia del mes son reglas por empresa que edita
    quien administra (arrancan en 8:30 y 30 min); las formulas quedan en codigo, con tests;
  - feriados no: los calcula el cliente a mano;
  - los horarios los carga el encargado de cada local en una grilla semanal: por empleado y dia,
    la hora de entrada (hasta dos, turno cortado) o "Franco"; solo entrada, sin salida;
  - permiso nuevo "Cargar horarios de asistencia", asignable por rol o por usuario. Los grupos
    ya existen: son los roles de Usuarios -> Roles (permisos default del rol + personalizado por
    usuario); se arma el rol "Encargado de local" con fichar + cargar horarios;
  - el encargado ve solo a los empleados de sus locales (sucursal base + "otros locales donde
    ficha asistencia"); quien administra ve todos los de su alcance;
  - el encargado carga y cambia hasta el dia anterior; el dia mismo y los pasados solo quien
    administra. Todo cambio queda registrado (quien, cuando, antes y despues);
  - el horario es del empleado, no del local: si cubre en otro local, la marca se compara igual.
  Reglas de calculo (dominio en `asistencia/resumen.py`, el Excel solo lo muestra):
  - horas extra del dia = horas del dia - jornada de su empresa, si da positivo;
  - llegada tarde = la entrada N del dia despues de la entrada N del horario (a minuto); se suman
    los minutos del mes y se marca si pasan la tolerancia de su empresa;
  - falta = dia con horario (no franco) sin ninguna marca, hasta ayer;
  - dia sin horario = no cuenta como falta ni tardanza; el resumen dice cuantos hay, desde el alta
    y hasta ayer o la baja, asi se ve que local no cargo;
  - si cambian una regla, el Excel de un mes anterior se recalcula con el valor nuevo (no se
    guarda desde cuando rige; se agrega si hace falta).

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
- datos personales minimos: nombre, DNI (opcional, D10), empresa y local habitual
- el DNI, si esta cargado, es unico entre activos de TODAS las empresas, pero ningun mensaje
  muestra el nombre de un empleado de una empresa fuera del alcance de quien carga; sin DNI, el
  nombre no se repite entre activos de la misma empresa
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
- avance 2026-10-21 (en staging): `asistencia/respaldo.py` y los comandos `respaldar_asistencia`
  y `restaurar_asistencia` (destino S3 o carpeta, 30 respaldos, aviso por webhook si falla, la
  restauracion no pisa datos sin `--reemplazar` y reinicia los ids). Revision 22/10: destino mal
  escrito da error, el nombre lleva el entorno y la limpieza solo toca los suyos (30 dias, minimo
  30 archivos), latido opcional `RESPALDO_OK_URL`, la restauracion verifica empresas y locales,
  deja vacios los usuarios borrados y chequea las FK antes de reiniciar los ids. Dependencia nueva
  `boto3==1.43.100` + `botocore==1.43.100`. Tests en `asistencia/tests_respaldo.py`. Procedimiento
  en `docs/asistencia-respaldo.md`. FALTA que el chat de servidor configure bucket, variables y
  cron, y pruebe un respaldo y una restauracion reales para cerrar la historia

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
- avance 2026-10-21 (en staging): tests en `asistencia/tests_lector.py`: el codigo con el Enter,
  salto de linea o Tab del lector registra; con Bloq Mayus (minusculas) tambien; despues de cada
  lectura el campo vuelve vacio y con el foco. El codigo usa solo A-Z y 2-7, que no cambian entre
  teclado espanol, latinoamericano e ingles. Al configurar el lector: modo teclado (HID) con Enter
  al final. FALTA solo la prueba con un lector 2D real para cerrar la historia

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
- avance 2026-10-26: manuales de administracion y de cajeros con el formato del manual de julio
  (`docs/manual-asistencia-administracion.html`, `docs/manual-asistencia-cajeros.html`). Falta el
  arranque (carga real, permisos de cajeros, carnets impresos)

### Ampliacion del 30/10: horarios y resumen del mes (D18)

### [ ] US-14.10 Reglas de asistencia por empresa

Como administracion
Quiero editar la jornada diaria y la tolerancia de llegadas tarde del mes de cada empresa
Para que las horas extra y las tardanzas se calculen con nuestras reglas, y cambiarlas sin pedirle
a sistemas

Criterios:
- pantalla Asistencia -> Reglas, solo para quien administra, con las empresas de su alcance
- jornada diaria en horas y minutos ("8:30") y tolerancia del mes en minutos ("30")
- sin reglas guardadas valen 8:30 y 30 min; guardar registra quien y cuando
- valores fuera de rango (jornada de 0 o de mas de 24 h, tolerancia negativa) se rechazan
- una empresa fuera del alcance no se ve ni se puede guardar por URL
- estimado: 2 h

### [ ] US-14.11 Hoja Resumen y horas extra en el Excel

Como administracion y encargados
Queremos una primera hoja con una fila por empleado y los totales del mes
Para no tener que abrir la hoja de cada uno

Criterios:
- la hoja "Resumen" va primera, ordenada por local y nombre, con filtro
- columnas: empleado, empresa, local, dias trabajados, horas del mes, horas extra, dias sin
  salida y marcas corregidas (US-14.14 suma tardanzas, faltas y dias sin horario)
- cada hoja de empleado suma la columna "Horas extra" por dia y su total
- horas extra con la jornada de la empresa del empleado (US-14.10)
- tests: dia justo en la jornada (0 extra), un minuto de mas, turno cortado, sin salida (no suma)
- estimado: 3 h

### [ ] US-14.12 Permiso "Cargar horarios de asistencia"

Como administracion
Quiero darle a los encargados el permiso de cargar horarios, por rol o por usuario
Para que cada encargado cargue los de su local sin ver nada mas

Criterios:
- la matriz de permisos suma "Cargar horarios de asistencia"; roles y usuarios existentes no lo
  reciben (salvo los administradores del sistema)
- con ese permiso se ve Asistencia en el menu y, en su inicio, solo Horarios (y Fichar si lo tiene)
- sus locales son la sucursal base y los otros locales donde ficha; sin ninguno no ve nada
- quien administra asistencia tambien carga horarios, de todos los locales de su alcance
- estimado: 1,5 h

### [ ] US-14.13 Grilla semanal de horarios por local

Como encargado de local
Quiero cargar la semana de mi local en una grilla: cada empleado, de lunes a domingo, su hora de
entrada o franco
Para que el sistema sepa a que hora entra cada uno aunque cambie todas las semanas

Criterios:
- se elige local (de los suyos) y semana; filas: empleados activos de ese local; columnas: dias
- cada casilla acepta una entrada ("8", "8:30"), dos ("8 17" o "8:00 17:00", turno cortado),
  "F" o "franco", o vacio (sin horario); lo que no se entiende se marca en la casilla y no se guarda
  nada de la semana hasta corregirlo
- "Copiar la semana anterior" llena solo las casillas vacias que el usuario puede tocar
- el encargado no puede cambiar hoy ni dias pasados (ni por URL); quien administra si
- todo cambio guarda quien, cuando, antes y despues
- los horarios entran al respaldo diario
- tests: formatos, segunda entrada antes de la primera, dia bloqueado, local ajeno, empleado de
  otro local, copiar sin pisar, historial
- estimado: 8 h

### [ ] US-14.14 Llegadas tarde y faltas en el Excel

Como administracion y encargados
Queremos ver en el Excel quien llego tarde, cuantos minutos en el mes, si paso la tolerancia y
quien falto
Para controlar la asistencia sin leer dia por dia

Criterios:
- cada hoja de empleado suma "Horario" y "Tarde (min)" por dia; la observacion dice "Falta",
  "Franco" o "Sin horario"
- el Resumen suma llegadas tarde, minutos tarde, "Pasa la tolerancia", faltas y dias sin horario
- reglas de D18 (entrada N contra entrada N, falta solo hasta ayer, sin horario no suma)
- tests: a horario, un minuto tarde, turno cortado tarde en el segundo, falta, franco trabajado,
  dia de hoy sin marca (no es falta), dias antes del alta, tolerancia justa y pasada
- estimado: 4 h

### [ ] US-14.15 Manuales: encargados y administracion

Criterios:
- manual nuevo para encargados (cargar la semana) con el formato de los otros
- el de administracion suma reglas, permiso de encargado (rol) y la hoja Resumen
- estimado: 2,5 h

Total de la ampliacion: 21 h.

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

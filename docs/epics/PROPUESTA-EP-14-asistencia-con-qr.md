# PROPUESTA EP-14 Asistencia del Personal con QR

> **ESTADO: PROPUESTA PARA PRESUPUESTAR. NO IMPLEMENTAR SIN OK EXPLICITO DEL USUARIO.**
> Este documento existe para que el usuario pueda dimensionar y presupuestar el trabajo.
> No se toca codigo ni migraciones a partir de este documento hasta que el usuario lo apruebe.
> Las horas son estimaciones de desarrollo; el precio lo define el usuario.

## Estado al 2026-10-20 y como seguir

- Tais respondio las preguntas el 20/10 (ver "Respuestas de Tais"). Con eso la base quedo en
  **61-88 h**: el turno cortado y la grilla de horarios rotativos pasaron de opcional a la base.
- **Presupuesto cerrado el 20/10**: 78 h a la vista (por slice: 7, 5, 16, 11, 10, 8, 12, 9) con un
  precio redondeado que decidio Mateo, mas un abono mensual por sucursal aparte, porque el
  fichaje es un servicio que funciona todo el dia.
  Opcionales cotizados: D 5 h, B 10 h, C 7 h. El PDF esta en la carpeta del traspaso.
- El valor hora y los importes los tiene Mateo y NO se escriben aca: el repo es publico.
- Quedan 3 puntos para cerrar con ella (ver "Lo que queda abierto"); se puede cotizar igual
  dejandolos como supuestos escritos en el presupuesto.
- Tais esta averiguando los lectores QR (uno por sucursal, 6 en total).
- Las fotos de la planilla y el audio quedaron en el chat de wpp, no en el repo (tienen nombres
  de empleados). Lo que importa de ellos esta en "Como lo hacen hoy".
- Siguiente paso: Mateo le pasa el presupuesto a Tais. Recien con su OK explicito se implementa por slices
  (rama `feat/asistencia-qr` desde `main`, despues `staging`).

## Origen del pedido (2026-10-20, Tais por wpp + audio)

1. Presupuesto para registrar la asistencia con QR: 50 empleados en ARMADI y 35 en Vivre (MAPOGO).
2. Lo que quiere registrar: hora de entrada y los minutos de retraso. Despues sumo la salida
   ("si ya lo hacemos que tambien pongan"), para que se cuenten las horas extra.
3. Idea de ella: un QR por empleado; al llegar, el cajero lo escanea y la marca queda registrada.
   "Hagamos el mismo para todos": mismo esquema en las dos empresas.

## Como lo hacen hoy

- Cada empleado anota en un cuaderno; el cajero, cuando tiene tiempo, lo tipea en la compu.
- Planilla (Google Sheets, por las fotos): un selector de empleado arriba y una fila por dia del
  mes con `Llegada | Ingreso | Tardanza | Salida | Egreso | Hs. Extra`. Por cada extremo hay dos
  columnas: el horario pactado y la hora real. `Tardanza` muestra "A tiempo" o el retraso;
  `Hs. Extra` sale de la salida real contra la pactada.
- Tiene una vista `PresentismoFebrero` con columnas "Con Presentismo" / "Sin Presentismo": usan
  la asistencia para decidir el presentismo del mes (por ahora no lo quiere en el sistema).
- Dolor real: el dato se carga tarde, a mano, desde un papel; la hora la escribe el propio
  empleado y nadie la controla.

## Respuestas de Tais (2026-10-20)

| # | Pregunta | Respuesta | Impacto |
|---|---|---|---|
| 1 | Turno cortado | Si, hay gente con turno cortado. | Pasa a la base: hasta dos tramos entrada/salida por dia. |
| 2 | Horarios fijos o rotativos | Rotativos, cambian cada semana. | Pasa a la base: grilla semanal de turnos (era el opcional E). |
| 3 | Si alguien llega antes que el cajero | Pasa a veces; "no habria problema que lo loguee al horario que llegue el cajero" (panaderia etc. no tocan la compu). | Ver "Lo que queda abierto" 3: escanearlo a la hora del cajero le genera una tardanza falsa. La base lo resuelve con la marca con hora informada (slice 6). |
| 4 | En que sucursales fichan | En todas; todas tienen caja. | 6 puntos de fichaje (Mateo lo corrigio el 20/10). El sistema tiene 7 sucursales activas (TERM-01, CENT-02, EB1-03, EB2-04, YH-05, PP-OV-06, VIV-01): falta confirmar cual no ficha. |
| 5 | Tolerancia | 30 minutos acumulativos por mes. | No hay tolerancia diaria: cada minuto tarde suma y el sistema lleva el saldo del mes por empleado. |
| 6 | Horas extra | "Se pagan por fraccion de media hora o superior". No contesto si se aprueban. | Bloques de 30 min (menos de 30 no cuenta). La aprobacion queda abierta (opcional D). |
| 7 | Faltas y presentismo | "Con las marcas alcanza por ahora". | B y C quedan fuera por ahora. |
| 8 | Planilla de Google | "Prefiero verlo y descargarlo del Gerayse y dejar de usar las planillas". | G queda fuera. La exportacion a Excel es de la base. |

## Diagnostico tecnico de partida (que ya existe, que falta)

- **Empresas y sucursales**: ya existen (`Empresa`, `Sucursal`). ARMADI: TERM-01, CENT-02, EB1-03,
  EB2-04, YH-05, PP-OV-06. MAPOGO: VIV-01.
- **Cajero por sucursal**: ya existe (EP-13). El cajero es usuario fijo con `sucursal_base`, entra
  a Gerayse en la PC de la caja y tiene permisos por accion. La pantalla de fichaje se apoya en
  eso sin inventar login nuevo.
- **Permisos por accion**: ya existe el patron (`PermissionModule` con acciones sueltas). Se suman
  los de marcar asistencia, cargar horarios y ver/corregir asistencia.
- **PDF**: ya esta `reportlab`, que trae generador de QR. Los carnets salen sin dependencia nueva.
- **No existe**: empleado como entidad (los empleados NO son usuarios del sistema), horarios,
  marcas, calculo de tardanza/horas extra ni exportacion a Excel. Para el Excel se suma
  `openpyxl` (dependencia nueva).

## Decision de arquitectura

**Modulo nuevo dentro de Gerayse** (app `asistencia`), no una app aparte ni escritura en la
planilla de Google. Tais lo confirmo (respuesta 8): quiere dejar las planillas.

- Reusa login, cajeros, sucursales, permisos, hosting, backups y deploy. Una app aparte suma
  usuarios, hosting y mantenimiento propios (+15 a 25 h y un costo mensual mas).
- La fuente de verdad pasa a ser Gerayse; la planilla se reemplaza por un reporte con las mismas
  columnas y exportable. Coherente con el principio del backlog: replicar el resultado, no el Excel.

## Reglas de negocio (con las respuestas)

- **Jornada**: cada empleado tiene por dia franco o uno o dos tramos (entrada pactada, salida
  pactada, sucursal). Una salida despues de medianoche pertenece a la jornada que empezo el dia
  anterior.
- **Marca**: la hora es la del servidor al escanear. La unica forma de registrar otra hora es la
  marca manual (slice 6), que queda senalada con motivo, usuario y fecha.
- **Tardanza**: minutos entre la entrada pactada y la marca de entrada de cada tramo, sin
  tolerancia diaria. "A tiempo" si es 0.
- **Tolerancia**: 30 minutos acumulados por empleado por mes calendario. El reporte muestra
  usado, saldo y exceso. Que pasa cuando se excede lo decide Tais fuera del sistema (respuesta 7).
- **Horas extra**: minutos trabajados despues de la salida pactada de cada tramo, sumados por
  dia y llevados hacia abajo a bloques de 30 min (29 → 0:00, 45 → 0:30, 65 → 1:00). Llegar antes
  de hora no suma extra.
- **Sin horario cargado**: la marca se guarda igual, pero ese dia no se calcula tardanza ni
  extra y aparece senalado en el reporte.

## Fase 1 (base): fichaje con QR, turnos rotativos, tardanza y horas extra

| # | Slice | Que incluye | Horas |
|---|---|---|---|
| 1 | Padron de empleados | Alta/baja/edicion (nombre, DNI, empresa, sucursal habitual, activo). Carga inicial masiva de los 85 desde un CSV/planilla. Baja logica, nunca borrado. | 6-8 |
| 2 | Credenciales QR | QR con un codigo opaco (sin datos personales adentro), revocable y reimprimible. PDF de carnets en A4 para recortar, por empresa o sucursal. | 4-6 |
| 3 | Grilla semanal de turnos | Grilla por sucursal y semana: por empleado y dia, franco o uno/dos tramos. Copiar la semana anterior para cargar rapido. Valida solapamientos y cruce de medianoche. | 12-18 |
| 4 | Pantalla de fichaje | Pantalla del cajero, pensada para lector QR USB (el lector "tipea" el codigo) y que tambien sirve con camara. Muestra grande nombre, entrada/salida del tramo que corresponde y "llego 12 min tarde". Hora del servidor. Anti doble escaneo. Solo marca en la sucursal del cajero. | 9-13 |
| 5 | Calculo | Tardanza por tramo, saldo mensual de la tolerancia de 30 min, horas extra en bloques de 30 min, turno cortado, cruce de medianoche, jornadas sin salida o sin horario senaladas. Tests de todas las reglas. | 8-12 |
| 6 | Correcciones auditadas | Marca manual con hora informada y motivo: olvido el carnet, se corto internet, o llego antes que el cajero (respuesta 3). Queda senalada en el reporte. Anular con motivo. Quien y cuando, siempre. | 6-9 |
| 7 | Reporte mensual | Por empleado con las mismas columnas que su planilla + tolerancia usada/saldo/exceso + resumen por empresa y sucursal. Descarga en Excel. | 9-13 |
| 8 | Puesta en marcha | Carga de los 85, staging, pruebas, pase a produccion, guia corta para cajeros y para quien carga la grilla, arranque definitivo acompañado: de a una sucursal o todas juntas, segun elijan (sin piloto, lo decidio Mateo el 20/10). | 7-9 |
|  | **Total fase 1** |  | **61-88** |

## Opcionales

| # | Opcional | Estado al 20/10 | Horas |
|---|---|---|---|
| A | Turno cortado | Pasa a la base (respuesta 1), repartido en los slices 3, 4 y 5. | - |
| B | Faltas y novedades (falta automatica, justificar franco/enfermedad/vacaciones/licencia) | Fuera por ahora (respuesta 7). El reporte igual muestra el dia con turno y sin marca. | 8-12 |
| C | Presentismo del mes (regla configurable y lista Con/Sin presentismo). Requiere B. | Fuera por ahora (respuesta 7). | 5-8 |
| D | Aprobacion de horas extra (solo cuentan las aprobadas por un encargado/admin) | Abierto: no lo contesto. | 4-6 |
| E | Grilla de turnos rotativos | Pasa a la base (respuesta 2): es el slice 3. | - |
| F | Foto al escanear (el cajero confirma que es la persona; guarda fotos, dato personal) | No consultado. | 3-5 |
| G | Volcado a su Google Sheet | Fuera (respuesta 8). | - |
| H | Modo sin internet (cola local que sube al volver la conexion) | No recomendado: con el slice 6 alcanza. | 10-16 |

## Lo que queda abierto (para cerrar el numero)

1. **Quien carga la grilla de horarios cada semana y hasta cuando**: Tais/administracion para
   todas las sucursales, o el encargado/cajero de cada una. Sin grilla cargada no hay tardanza.
   Cambia permisos y pantallas, no mucho las horas.
2. **Horas extra: se cuentan solas o las aprueba alguien?** Sin respuesta. Supuesto del
   presupuesto: se cuentan solas; si las aprueban, se suma D.
3. **Gente que llega antes que el cajero**: Tais dijo que no hay problema en marcarlos a la hora
   que llega el cajero, pero eso les deja una tardanza falsa que se come los 30 min del mes. La
   base lo cubre con la marca con hora informada (el cajero pone la hora real y queda senalada).
   Confirmar con ella que el cajero cargue la hora real en esos casos.

Supuestos que conviene escribir en el presupuesto (no hace falta preguntarlos antes):

- La tardanza del segundo tramo del turno cortado tambien consume la tolerancia del mes.
- El redondeo a bloques de 30 min de las horas extra es por dia, como la columna de la planilla.
- Un empleado puede tener tramos en distintas sucursales; la grilla lo permite.

## No incluye

- Liquidacion de sueldos ni montos: el sistema dice minutos y horas, no cuanto se paga.
- Recargos por feriado o nocturnidad.
- Faltas, justificaciones y presentismo (opcionales B y C, por ahora no).
- Hardware: lectores QR, impresion y plastificado de carnets (los compra/imprime el cliente).
- Reloj biometrico, app nativa, fichaje desde el celular del empleado con geolocalizacion.
- Migrar el historico de la planilla actual (si lo quieren, es aparte).

## Hardware (lo compra el cliente; Tais lo esta averiguando)

- Un **lector QR 2D USB** por sucursal: 6 en total (ojo: 2D; los lectores 1D de codigo de barras
  no leen QR). Funciona como teclado, no necesita drivers y es lo mas rapido y confiable.
- Alternativas sin costo: la webcam de la PC de la caja o el celular del cajero con Gerayse
  abierto. Andan, pero son mas lentas, y las webcams de las cajas suelen ser malas.

## Impacto de datos y hosting

- Tablas nuevas: empleados, credenciales, grilla de turnos, marcas, correcciones. No toca caja,
  tesoreria ni ningun importe.
- Volumen: marcas 85 empleados x 2 a 4 por dia x ~26 dias = ~4.400 a 8.800/mes; grilla ~2.600
  tramos/mes. En total ~1-2 MB/mes. Hoy la base crece ~4-5k filas/mes: el ritmo de filas se
  duplica o triplica, pero el tamano sigue siendo chico.
- Datos personales: nombre, DNI y horarios. QR sin datos adentro. Sin biometria. Foto solo si
  se aprueba F.
- Dependencia nueva: `openpyxl` para la descarga en Excel.

## Orden sugerido si se aprueba

1. Slices 1 y 2 (padron y carnets): se pueden imprimir los carnets mientras se arma el resto.
2. Slice 3 (grilla): que la carguen una semana antes del arranque.
3. Slices 4 y 5 juntos: es la capacidad minima visible (fichar y ver la tardanza).
4. Slice 6 antes del arranque: sin correcciones el primer corte de internet frena todo.
5. Slice 7 y arranque definitivo (slice 8), de a una sucursal o todas juntas.

## Dependencias

- `EP-13` cajeros por sucursal y permisos por accion (ya implementado).
- `EP-08` sucursales y `EP-12` empresas y contexto activo (ya implementados).

## Criterio de cierre (fase 1)

- un cajero escanea un carnet y queda la marca con hora del servidor, en su sucursal y en el
  tramo que corresponde, sin poder editar la hora
- la grilla de una semana se carga por sucursal, admite turno cortado y se copia de la anterior
- el reporte mensual de un empleado muestra horario pactado, hora real, tardanza, horas extra en
  bloques de 30 min y el saldo de la tolerancia de 30 min, y se descarga en Excel
- toda marca manual o anulada queda con motivo, usuario y fecha, y se distingue en el reporte
- hay tests de tardanza, tolerancia mensual, bloques de horas extra, turno cortado y cruce de
  medianoche

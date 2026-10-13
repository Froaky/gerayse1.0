# Bitácora — Módulo de reportes PDF

Rama `feat/reportes-pdf` (parte de `origin/staging` en 695a679, que ya trae la
regularización de deudas US-3.20). Presupuesto aceptado por Tais el 2026-10-13:
$ 450.000, 25 h, 7 reportes (3 definidos + 4 a definir, que quedan a cuenta).

El plan técnico completo está en el PDF "Plan Implementacion Reportes Gerayse"
(fuente HTML en `C:\tmp\gerayse-presupuesto-reportes\plan-implementacion-reportes.html`
en la máquina de la oficina). Esta bitácora es lo que hay que leer para retomar.

## ESTADO AL CIERRE (2026-10-13 16:55) — leer esto primero

**El desarrollo está terminado y probado en la rama; falta validarlo en staging y
pasarlo a main.** Todo está commiteado y empujado a `origin/feat/reportes-pdf`.

- Hecho: S0 a S5 + revisión adversarial con sus arreglos. Los 3 reportes (situación
  económica, situación financiera, desglose de deudas) se descargan desde
  Tesorería > Reportes > Reportes PDF, por empresa (razón social en el encabezado),
  por sucursal, general, o general + cada sucursal en un solo PDF.
- Suite completa: **965 OK (4 skipped)** (843 de base + 122 nuevos). Sin migraciones.
- Verificado a ojo con la base de demo (datos ficticios): los 3 PDFs, por sucursal,
  general y "general y cada sucursal".

### Qué falta, en orden

1. **S6 — Staging.** Confirmar que `origin/staging` sigue en 695a679 (si se movió, hay
   que mergear/rebasear `feat/reportes-pdf` sobre staging primero) y hacer
   fast-forward:
   ```bash
   git fetch origin
   git log --oneline -1 origin/staging        # tiene que ser 695a679 o ancestro de la rama
   git checkout staging && git merge --ff-only origin/feat/reportes-pdf
   git push origin staging
   ```
   Railway instala `reportlab==5.0.0` (Python puro + Pillow); revisar que el deploy de
   staging quede verde. Después, en staging: bajar los 3 reportes de septiembre 2026
   para Armadi general, cada sucursal y Mapogo, y comparar contra el dashboard de
   tesorería con el mismo alcance (los números tienen que ser los mismos: los PDFs
   leen los mismos snapshots).
2. **Mandarle a Tais PDFs de muestra** y cerrar las decisiones abiertas (abajo).
3. **S7 — main**, solo con OK de Mateo. Agregar la nota en `context.md` (borrador al
   final de esta bitácora) y correr la suite antes del push.
4. **Reportes 4 a 7**, cuando Tais los pida (quedan a cuenta). Cada uno es: un módulo
   `treasury/reportes/<nombre>.py` con `SLUG`, `TITULO`, `build_<nombre>(scope)` y
   `nombre_archivo(scope)` que lea una lectura existente (si hace falta una nueva, va
   en `treasury/services.py` con su test), una entrada en `treasury/reportes/registro.py`
   y su `treasury/tests_reportes_<nombre>.py`. Candidatos: disponibilidades del mes
   (`build_disponibilidades_snapshot`; por sucursal el saldo inicial da 0 porque los
   cierres son por empresa), cheques por vencer, ventas por canal, cierres de caja,
   pagos a proveedores (partir de `PagoTesoreria.objects.con_movimiento_de_fondos()`),
   cuenta de un proveedor (`build_supplier_history_snapshot`).

### Para seguir en otra computadora

- La demo local (`C:/tmp/gerayse-reportes-demo/gerayse_demo.sqlite3`, config
  `reportes-demo` del `.claude/launch.json` del checkout principal, puerto 8011) solo
  existe en la máquina de la oficina. En otra máquina alcanza con los tests; para ver
  un PDF, cualquier base con datos y `python manage.py runserver` (con `DEBUG=True`).
- En la oficina quedaron: worktree `C:/tmp/gerayse-reportes`, venv
  `C:/tmp/venv-gerayse-reportes`. Se pueden borrar cuando la rama esté en main
  (`git worktree remove C:/tmp/gerayse-reportes`).

## Cómo retomar (por ejemplo, en casa)

```bash
git fetch origin
git checkout feat/reportes-pdf          # o: git worktree add ../gerayse-reportes feat/reportes-pdf
py -3.12 -m venv .venv-reportes          # cualquier Python 3.12/3.13 con ruedas de psycopg
.venv-reportes/Scripts/pip install -r requirements.txt
.venv-reportes/Scripts/python manage.py test treasury.tests_reportes_nucleo -v 1
# antes de cada push:
.venv-reportes/Scripts/python manage.py test cashops treasury users core -v 1
.venv-reportes/Scripts/python manage.py makemigrations --check --dry-run
.venv-reportes/Scripts/python -m compileall cashops treasury users core config
```

Para generar un PDF a mano desde la shell hace falta `DEBUG=True` (si no,
`config/settings.py` exige `DJANGO_SECRET_KEY`).

La rama sigue a `origin/feat/reportes-pdf` (un `git push` pelado va ahí). Nació de
`origin/staging`: a staging y a main se pasa solo con los pasos S6/S7 de arriba.

## Estado por slice

| Slice | Qué | Estado |
|---|---|---|
| S0 | Rama, venv, `reportlab==5.0.0` en requirements | Hecho (6b46117) |
| S1 | Núcleo: `documento.py`, `alcance.py`, `pdf.py` + `tests_reportes_nucleo.py` | Hecho (6b46117) |
| fix | Egresos de bóveda sin imputar acotados por empresa (lectura económica) | Hecho (2817ca3) |
| S2 | R1 situación económica (builder + tests) | Hecho (94df731) |
| S3 | R2 situación financiera (builder + tests) | Hecho (94df731) |
| S4 | R3 desglose de deudas (lectura nueva en services + builder + tests) | Hecho (94df731) |
| S5 | Formulario, vistas, URLs, página y menú (+ tests de permisos y aislamiento) | Hecho (d8e4fc1) |
| rev | Revisión adversarial de toda la rama y sus 5 arreglos | Hecho (819c031) |
| S6 | "General + todas las sucursales" en un PDF (hecho en S5); validación en staging | Pendiente: staging |
| S7 | Pase a main con OK de Mateo; nota en `context.md` | Pendiente |

## Mapa del código

- `treasury/reportes/documento.py`: dataclasses sin Django (`ReportDocument`, `Kpi`,
  `TableBlock`, `NoteBlock`, `Pct`). Los montos van como `Decimal`; el renderer formatea.
- `treasury/reportes/alcance.py`: `ReportScope` (empresa, sucursal o None, fechas,
  `sucursal_unica` para Mapogo), rótulos, `filename()`, `rango_de_meses()`, `nombre_mes()`.
  La razón social es `Empresa.nombre` y el CUIT `Empresa.identificador_fiscal`.
- `treasury/reportes/pdf.py`: `render_report_pdf(docs) -> bytes`. Encabezado por página
  dibujado en `onPageEnd` (un flowable invisible `_DocMarker` marca qué documento ocupa
  la página), "Página X de Y" con un canvas de dos pasadas, `LongTable` con encabezado
  repetido, celdas envueltas en `Paragraph` solo si no entran en la columna.

## Registro

### 2026-10-13 15:57 — S0 y S1

- Worktree en `C:\tmp\gerayse-reportes`, venv en `C:\tmp\venv-gerayse-reportes`
  (Python 3.12 + requirements + reportlab 5.0.0).
- Línea de base antes de tocar nada: `manage.py test cashops treasury users core`
  → 843 tests OK (4 skipped), 42 s.
- `reportlab==5.0.0` agregado a `requirements.txt` (cambio de dependencia declarado).
  Es Python puro (depende de Pillow y charset-normalizer): no necesita paquetes de
  sistema en Railway.
- Núcleo escrito y probado: `treasury/tests_reportes_nucleo.py` → 19 tests OK
  (formato de valores, validación de tablas, alcance y nombre de archivo, PDF de una
  y varias páginas, tabla vacía, filas de grupo y total, 2.000 filas).
- Muestra visual: 63 filas en 22 ms, 3 páginas, nombres largos con salto de línea.
- Desvíos respecto del plan:
  - Un módulo por reporte (`economica.py`, `financiera.py`, `deudas.py`) y un archivo de
    tests por área (`tests_reportes_*.py`) en vez de `builders.py` y `tests_reportes.py`
    únicos: permite trabajar los tres reportes en paralelo sin pisarse.
  - El registro de reportes va en `treasury/reportes/registro.py` (S5), no en
    `__init__.py`, para que importar el paquete no arrastre todos los builders.
  - `periodo_label` es el rótulo completo ("Período 01/09/2026 al 30/09/2026" o
    "Deuda abierta al 13/10/2026"); el encabezado no le antepone nada.
  - Los porcentajes se muestran con el redondeo que ya traen los snapshots (2
    decimales); el renderer no re-redondea.

### 2026-10-13 16:04 — S2-S4 en paralelo, S5 en preparación

- S2, S3 y S4 los implementan tres agentes en paralelo (workflow), cada uno en sus
  archivos: `treasury/reportes/{economica,financiera,deudas}.py`,
  `treasury/tests_reportes_{economica,financiera,deudas}.py` y, para R3, la lectura
  `build_open_payables_breakdown` agregada en `treasury/services.py` justo después de
  `build_financial_period_snapshot`. Después dos revisores por reporte (lente
  datos/fórmulas y lente tests/aislamiento) y un corrector.
- S5 hecho en paralelo, sin cablear todavía (cablear antes rompía los imports de los
  tests de los agentes):
  - `ReportFilterForm` en `treasury/forms.py`. Desvío respecto del plan: en vez de dos
    campos (empresa + alcance) hay UN desplegable "Empresa y alcance" agrupado por razón
    social, con valores `e<empresa>` (general), `t<empresa>` (general + cada sucursal en
    un PDF) y `s<sucursal>`. Así no hace falta JavaScript y la sucursal siempre se valida
    contra la empresa. Empresa con una sola sucursal activa: solo "General · Vivre
    (única sucursal)". Meses con `input type=month`; en R3 son opcionales (los dos o
    ninguno).
  - `ReportScope.todas_las_sucursales` (solo cambia el nombre del archivo:
    `_general-y-sucursales_`).
  - `treasury/reportes/registro.py` (`REGISTRO`), `templates/treasury/reportes.html`
    (una tarjeta con su formulario por reporte) y `treasury/tests_reportes_vistas.py`.
  - Falta aplicar: vistas `reportes_index` / `reporte_pdf` + `_report_empresas` en
    `treasury/views.py`, las dos rutas en `treasury/urls.py` y "Reportes PDF" en el
    desplegable "Reportes" de `templates/treasury/layout.html`.
  - Empresas ofrecidas = activas ∩ seleccionadas en la sesión ∩ `empresas_permitidas`.
    Alcance ajeno, inactivo o mal formado por URL → 404 (no cae en silencio al general).
    Formulario incompleto o inválido → vuelve a la página con un mensaje.

### 2026-10-13 16:33 — S2-S5 terminados, fix de bóveda

- Workflow de S2-S4: 12 agentes (implementación, dos revisores por reporte con
  mutaciones, corrector). Hallazgos confirmados y corregidos:
  - R1: filas que solo tienen ventas (ventas con tarjeta caen en "Sin rubro") ya no se
    listan con todo en cero; nota que lo explica. Sin ventas, margen y % salen "—".
    Nota de objetivos distinta para empresa de una sola sucursal. Aclaración cuando un
    grupo tiene objetivo solo en algunos rubros.
  - R2: la deuda pendiente del snapshot NO tiene fecha (es la vigente al emitir) aunque
    los saldos sean al corte: los textos lo dicen. Por sucursal la "caja fuerte" es el
    neto imputado a la sucursal, no efectivo: los hints lo dicen.
  - R3: "Pagado" (= importe − saldo) incluye cancelaciones sin movimiento de fondos;
    `build_open_payables_breakdown` devuelve `settled_without_funds_total` y el PDF lo
    aclara en una nota cuando es > 0.
- FIX encontrado por la revisión (commit aparte 2817ca3): la lectura económica sumaba
  como "gasto sin imputar" los egresos de bóveda de OTRA empresa (el banco sí cortaba por
  empresa). Ahora usa el mismo criterio que las cuentas
  (`bank_account_empresa_scope_query(..., prefix="caja_central__")`): bóveda de la
  empresa, y las bóvedas viejas sin empresa siguen visibles. Afecta la tarjeta/nota de
  gasto sin imputar del dashboard y del PDF; nada más.
- Límite conocido que queda: una deuda sin sucursal suma en el general de TODAS las
  empresas (criterio heredado de la lectura financiera; en prod hay 0). En el desglose
  se ve como "Sin sucursal".
- S5 cableado (vistas, URLs, menú). Renderer: el rótulo de subtotales ocupa las celdas
  vacías que le siguen.
- Verificación visual con la base de DEMO (datos ficticios, Café Bruma / Panadería
  Trigal): copia en `C:/tmp/gerayse-reportes-demo/gerayse_demo.sqlite3` migrada a esta
  rama; config `reportes-demo` agregada al `.claude/launch.json` del checkout principal
  (puerto 8011, auto-login de demo). Los 3 reportes, por sucursal, general y "general y
  cada sucursal" se ven bien.
- Suite completa: 956 OK (4 skipped) = 843 de base + 113 nuevos. `makemigrations
  --check`: sin cambios. `compileall` y `check` OK.

### 2026-10-13 16:55 — Revisión adversarial y cierre del día

- Workflow de revisión de toda la rama: 4 lentes (seguridad/aislamiento, dominio,
  bordes, contrato/calidad), 16 hallazgos, cada uno verificado por 3 escépticos;
  sobrevivieron 7 (5 problemas distintos), todos corregidos en 819c031:
  1. "Mes hasta" futuro aceptado: la financiera tomaba de corte una fecha futura.
     Ahora el form lo rechaza (R1/R2), el input tiene `max`, y `rango_de_meses` nunca
     devuelve un corte futuro.
  2. Años absurdos (9999-12, 0001-01) daban 500: tope desde 2000; en el desglose
     (que admite meses futuros por deudas imputadas por adelantado) hasta el año que
     viene.
  3. El detalle del encabezado se salía de la hoja: pasa a dos líneas si no entra; la
     razón social se achica para no pisar el título (`header_layout` en pdf.py;
     MARGIN_TOP 32 mm).
  4. "Única sucursal" solo si la empresa no tiene OTRAS sucursales, ni cerradas (el
     general suma todas).
  5. Situación económica de una empresa de sucursal única (Mapogo) se lee por esa
     sucursal (`lectura_sucursal` en economica.py) para que aplique sus objetivos
     propios. La financiera y el desglose siguen leyendo el general de la empresa.
- Descartados por los escépticos (no son defectos): deuda sin sucursal en todas las
  empresas (regla heredada, documentada), caracteres fuera de Latin-1 (salen como ■;
  no aparecen en los datos reales), duplicación del filtro de alcance de deuda con la
  lectura financiera (a propósito, concilia con un test), detalles de tests.
- Suite completa: 965 OK (4 skipped). `makemigrations --check` sin cambios.

## Borrador de nota para `context.md` (agregar en S7, arriba de "Current Session")

```
### Treasury Reportes PDF (presupuesto aceptado 2026-10-13) — rama feat/reportes-pdf

- Tesoreria > Reportes > Reportes PDF: situacion economica, situacion financiera y
  desglose de deudas en PDF (ReportLab, `reportlab==5.0.0`), por empresa (razon social =
  `Empresa.nombre`, CUIT = `Empresa.identificador_fiscal`), por sucursal, general o
  general + cada sucursal en un PDF. Presupuesto: 7 reportes, 3 hechos, 4 a cuenta.
- Capas: snapshot existente -> builder (`treasury/reportes/<reporte>.py`) ->
  `ReportDocument` -> `render_report_pdf`. Sin formulas nuevas de dinero. Lectura nueva:
  `build_open_payables_breakdown` (mismo alcance que pending_payables; concilia).
- Seguridad: permiso treasury read; empresas = activas ∩ sesion ∩ permitidas; alcance
  ajeno por URL -> 404. Los snapshots ignoran empresa_ids con sucursal: la pertenencia
  la valida `ReportFilterForm`.
- FIX incluido: egresos de boveda sin imputar acotados por empresa en la lectura
  economica (antes aparecian los de la otra empresa).
- Limite conocido: deuda sin sucursal suma en el general de todas las empresas.
- Bitacora completa: docs/bitacora-reportes-pdf.md.
```

## Decisiones abiertas (preguntar a Mateo, no resolver solo)

1. "El mismo diseño" que pidió Tais: ¿pantallas del dashboard o el PDF de desglose de
   deuda entregado en julio?
2. Proveedor interno (Oveja Negra): HECHO como propuesta — se incluye y se marca
   "(interno)" con una nota. Si Mateo prefiere excluirlo, es un filtro en
   `build_open_payables_breakdown` (y habría que dejar de conciliar con pending_total).
3. Publicar el desglose después de que corra la regularización en producción, o avisarle
   a Tais que va a ver la deuda inflada por pagos sin atar.
4. Banco por sucursal en Armadi: la cuenta es de la empresa, el reporte por sucursal sale
   sin saldo bancario (lleva una nota que lo explica). Validar con Tais que se entienda.
5. Precio de un reporte extra y si los reportes "a cuenta" vencen: no está en el
   presupuesto.
6. Empresa de sucursal única (Mapogo): la situación económica se lee por la sucursal
   (aplica objetivos de Vivre); la financiera y el desglose, por la empresa. Si Tais
   prefiere otra cosa es un cambio de una línea en cada builder.

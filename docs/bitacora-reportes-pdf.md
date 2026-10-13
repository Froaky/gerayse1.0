# Bitácora — Módulo de reportes PDF

Rama `feat/reportes-pdf` (parte de `origin/staging` en 695a679, que ya trae la
regularización de deudas US-3.20). Presupuesto aceptado por Tais el 2026-10-13:
$ 450.000, 25 h, 7 reportes (3 definidos + 4 a definir, que quedan a cuenta).

El plan técnico completo está en el PDF "Plan Implementacion Reportes Gerayse"
(fuente HTML en `C:\tmp\gerayse-presupuesto-reportes\plan-implementacion-reportes.html`
en la máquina de la oficina). Esta bitácora es lo que hay que leer para retomar.

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

Ojo: la rama NO tiene upstream configurado a propósito (nació de `origin/staging`
y un `git push` pelado la habría empujado a staging). Empujar siempre con
`git push -u origin feat/reportes-pdf`.

## Estado por slice

| Slice | Qué | Estado |
|---|---|---|
| S0 | Rama, venv, `reportlab==5.0.0` en requirements | Hecho |
| S1 | Núcleo: `documento.py`, `alcance.py`, `pdf.py` + `tests_reportes_nucleo.py` | Hecho |
| S2 | R1 situación económica (builder + tests) | Pendiente |
| S3 | R2 situación financiera (builder + tests) | Pendiente |
| S4 | R3 desglose de deudas (lectura nueva en services + builder + tests) | Pendiente |
| S5 | Formulario, vistas, URLs, página y menú (+ tests de permisos y aislamiento) | Pendiente |
| S6 | "General + todas las sucursales" en un PDF; validación en staging | Pendiente |
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

## Decisiones abiertas (preguntar a Mateo, no resolver solo)

1. "El mismo diseño" que pidió Tais: ¿pantallas del dashboard o el PDF de desglose de
   deuda entregado en julio?
2. Proveedor interno (Oveja Negra): incluirlo en el desglose marcado "(interno)" es la
   propuesta. `Proveedor.es_interno` ya está en esta rama (vino de staging).
3. Publicar el desglose después de que corra la regularización en producción, o avisarle
   a Tais que va a ver la deuda inflada por pagos sin atar.
4. Banco por sucursal en Armadi: la cuenta es de la empresa, el reporte por sucursal sale
   sin saldo bancario (se agrega una nota).

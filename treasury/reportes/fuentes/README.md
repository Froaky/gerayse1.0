# Fuentes de los reportes PDF

Tipografías del Manual de Identidad Visual de Gerayse, embebidas por `treasury/reportes/pdf.py`:

| Archivo | Uso |
|---|---|
| `PublicSans-Regular/SemiBold/Bold.ttf` | Cuerpo, tablas, rótulos y razón social |
| `Fraunces-SemiBold.ttf` | Título del reporte y de cada sección |
| `SplineSansMono-SemiBold.ttf` | Cifras de las tarjetas (KPI) |

Son instancias estáticas de las fuentes variables de Google Fonts (ReportLab no maneja fuentes
variables), generadas con `fontTools.varLib.instancer`: Public Sans `wght` 400/600/700, Fraunces
`wght` 600 + `opsz` 14 + `SOFT` 0 + `WONK` 0, Spline Sans Mono `wght` 600, con los nombres internos
de cada estilo y, en la Bold, `instancer.setRibbiBits` (bits de negrita en OS/2 y head). No se
recortaron glifos: ReportLab embebe en cada PDF solo los que usa.

Licencia: SIL Open Font License 1.1 (`OFL-*.txt`, copiados de `github.com/google/fonts`). Ninguna
declara Reserved Font Name. Public Sans tiene además la nota de USWDS sobre su versión modificada:
https://github.com/uswds/public-sans/blob/master/LICENSE.md

"""Modulo de reportes PDF de tesoreria.

Capas: snapshot existente -> builder (un modulo por reporte) -> `ReportDocument`
(documento.py) -> `render_report_pdf` (pdf.py). El registro de reportes que ve
la UI esta en `registro.py`.
"""

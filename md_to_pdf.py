"""Convert MANUAL_USUARIO.md to PDF using xhtml2pdf."""
import markdown
from xhtml2pdf import pisa

with open("MANUAL_USUARIO.md", encoding="utf-8") as f:
    md_text = f.read()

md = markdown.Markdown(extensions=["tables", "toc", "fenced_code"])
body_html = md.convert(md_text)

html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<style>
@page {{
    size: A4;
    margin: 20mm 18mm 22mm 18mm;
}}

body {{
    font-family: Helvetica, Arial, sans-serif;
    font-size: 10pt;
    line-height: 1.6;
    color: #1e293b;
}}

h1 {{
    font-size: 20pt;
    font-weight: bold;
    color: #0f172a;
    margin-top: 0;
    margin-bottom: 6pt;
    padding-bottom: 8pt;
    border-bottom: 3pt solid #3b82f6;
}}

h2 {{
    font-size: 13pt;
    font-weight: bold;
    color: #1e40af;
    margin-top: 20pt;
    margin-bottom: 5pt;
    padding-bottom: 4pt;
    border-bottom: 1pt solid #bfdbfe;
}}

h3 {{
    font-size: 11pt;
    font-weight: bold;
    color: #1e293b;
    margin-top: 13pt;
    margin-bottom: 4pt;
}}

h4 {{
    font-size: 10pt;
    font-weight: bold;
    color: #475569;
    margin-top: 9pt;
    margin-bottom: 3pt;
}}

p {{
    margin-top: 0;
    margin-bottom: 6pt;
}}

ul, ol {{
    margin-top: 3pt;
    margin-bottom: 7pt;
    padding-left: 16pt;
}}

li {{
    margin-bottom: 2pt;
}}

blockquote {{
    margin: 8pt 0;
    padding: 7pt 12pt;
    background: #f0f9ff;
    border-left: 4pt solid #3b82f6;
    color: #1e40af;
    font-style: italic;
}}

table {{
    width: 100%;
    border-collapse: collapse;
    margin: 8pt 0 12pt 0;
    font-size: 9pt;
}}

th {{
    background-color: #1e40af;
    color: white;
    font-weight: bold;
    padding: 5pt 7pt;
    text-align: left;
}}

td {{
    padding: 4pt 7pt;
    border-bottom: 0.5pt solid #e2e8f0;
}}

tr.even td {{
    background-color: #f8fafc;
}}

code {{
    font-family: Courier, monospace;
    font-size: 8.5pt;
    background: #f1f5f9;
    color: #0f172a;
    padding: 1pt 3pt;
}}

pre {{
    background: #f1f5f9;
    border: 0.5pt solid #e2e8f0;
    padding: 8pt;
    margin: 7pt 0;
    font-size: 8pt;
    font-family: Courier, monospace;
}}

hr {{
    border-top: 1pt solid #e2e8f0;
    margin: 14pt 0;
}}

a {{
    color: #2563eb;
    text-decoration: none;
}}

strong {{
    font-weight: bold;
}}
</style>
</head>
<body>
{body_html}
</body>
</html>"""

output_path = "MANUAL_USUARIO.pdf"
with open(output_path, "wb") as f:
    result = pisa.CreatePDF(html.encode("utf-8"), dest=f, encoding="utf-8")

if result.err:
    print(f"Errores: {result.err}")
else:
    print(f"PDF generado: {output_path}")

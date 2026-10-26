---
name: documentos-gerayse
description: armar cualquier documento que sale de Gerayse (informe, manual de uso, presupuesto, reporte) con el único diseño de la marca, usando la hoja de estilos y las plantillas de docs/marca/.
---

Usar esta skill cada vez que haya que escribir un informe, un manual (impreso o web), un presupuesto
o cualquier documento que vaya a ver Tais, un cajero, un dueño o un contador.

La referencia completa es `docs/marca/manual-de-marca.html` (leerlo antes del primer documento de
la sesión). En resumen:

1. **Una sola hoja de estilos:** `docs/marca/gerayse-documento.css`. Fraunces 600 para títulos,
   Public Sans para el texto, Spline Sans Mono para cifras; verde bosque `#1b3a2e` sobre papel
   blanco; la G verde señal `#22c47e` como única marca. No se definen colores ni fuentes dentro
   del documento. Si falta una pieza, se agrega a la hoja y al manual de marca.
2. **Arrancar de una plantilla:** `docs/marca/plantilla-documento.html` (A4: informe, presupuesto,
   manual para imprimir; `body.doc`) o `docs/marca/plantilla-manual-web.html` (manual web con el
   formato de julio: encabezado, índice al costado, secciones numeradas, notas Tip/Ojo/Recordá/
   Tranquila, antes/ahora, checklist, preguntas frecuentes, día de ejemplo; `body.manual`).
   El documento nuevo queda en `docs/` con nombre en minúsculas y guiones, y enlaza la hoja con
   `<link rel="stylesheet" href="marca/gerayse-documento.css">`.
3. **Encabezado fijo:** G → rótulo (`.eyebrow`, "Manual de uso · Tesorería") → título en Fraunces
   → bajada → firma "Sistema Gerayse · fecha" → caja `.resumen` "En una línea". Los presupuestos
   usan `.portada--tapa` con el bloque `.datos` (preparado para / por, fecha, estado, validez).
   El nombre del documento va al pie de cada página con
   `@page { @bottom-left { content: "Gerayse · Nombre"; } }` en el `<style>` de la página.
4. **Piezas, siempre las de la hoja:** `.nota` (y `--ok`, `--aviso`, `--peligro`, `--gris`),
   tablas con `th` verde y `td.cifra` a la derecha, `.kpis` + `.kpi`, `ol.pasos`, `.ruta` con
   `.pill`, `.boton` para nombrar botones del sistema, `.dos` + `.tarjeta--si/--no/--antes/--ahora`,
   `.checklist`, `.captura` + `.leyenda`, `details.faq`, `.dia`.
5. **Datos:** plata `$ 1.234.567,89` en `.cifra`; fechas `26/10/2026` en tablas y "26 de octubre
   de 2026" en texto; porcentajes `26,55 %`. Ejemplos y capturas con datos inventados y la
   leyenda "Datos inventados"; nunca datos reales de un cliente.
6. **Voz:** de vos y cálido en manuales ("Tranquila, no se pierde nada"), sobrio en informes (la
   respuesta corta primero), formal-cercano en presupuestos. Firma "Sistema Gerayse"; ningún
   nombre propio salvo "Preparado por" en presupuestos. Los avisos formales en pantalla al
   cliente van de usted (eso no es un documento: ver copy en `context.md`).
7. **Entrega:** `python docs/marca/armar.py docs/<documento>.html --destino "<carpeta gerayse del
   escritorio>/<manuales|presupuestos|informes>"` deja el HTML autocontenido (la hoja adentro)
   y el PDF impreso con Edge headless, esperando a que carguen las fuentes. Verificar en el PDF
   que los títulos salieron en Fraunces (no Georgia) y que el pie numera las páginas.

No hacer: fondos de color o tapas verdes en papel, otra tipografía, verde señal en texto o
fondos, sombras, emojis, color como único aviso de estado, texto justificado, piezas nuevas
definidas dentro de un documento.

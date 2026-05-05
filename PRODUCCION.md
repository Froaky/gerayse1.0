# Notas para puesta en produccion

## IMPORTANTE — Leer antes de hacer el deploy productivo

### Boton "Reiniciar datos" — DEBE ELIMINARSE ANTES DE PRODUCCION

Existe un boton de "Reiniciar datos de cajas" visible en:
- La pagina de Empresas (Config → Empresas, zona inferior)
- El dropdown de Config en la barra de navegacion ("Reiniciar datos")

**Este boton elimina TODOS los datos operativos del sistema** (cajas, movimientos, cierres, cuentas por pagar, efectivo central, etc.) con solo dos confirmaciones. Es una herramienta de testing, no debe existir en produccion.

### Que eliminar antes del deploy:

1. **`cashops/views.py`** — eliminar la funcion `reset_operational_data` (al final del archivo, bajo el comentario `# --- Reinicio de datos operativos`)

2. **`cashops/urls.py`** — eliminar la linea:
   ```python
   path("config/reiniciar/", views.reset_operational_data, name="reset_operational_data"),
   ```

3. **`templates/cashops/layout.html`** — eliminar las dos lineas del separador y el link "Reiniciar datos" en el dropdown de Config.

4. **`templates/cashops/list_page.html`** — eliminar el bloque `{% if show_danger_zone %}...{% endif %}` al final del template.

5. **`templates/cashops/reset_confirm.html`** — eliminar el archivo completo.

6. Este archivo `PRODUCCION.md` tambien puede eliminarse.

---

_Nota generada el 2026-05-05. Confirmar que ninguna de estas rutas quede expuesta en el entorno productivo._

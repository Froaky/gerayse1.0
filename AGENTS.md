# AGENTS.md - Reglas de desarrollo asistido para Gerayse

Este archivo es la instruccion principal para cualquier agente de IA que trabaje en este repo.
El objetivo no es solo que el codigo funcione, sino que cada cambio sea aislado, trazable, seguro para datos reales y escalable.

## 1. Contexto obligatorio del repo

Gerayse es un monolito Django para operacion de caja, tesoreria y gestion economico-financiera.

Stack actual:

- Django 5.2.x
- Django ORM
- PostgreSQL en produccion / SQLite local segun entorno
- Django Templates + HTMX
- Whitenoise para estaticos

Apps principales:

- `cashops`: caja operativa, turnos, movimientos, cierres, traspasos, alertas operativas.
- `treasury`: proveedores, cuentas por pagar, pagos, banco, caja central, acreditaciones, disponibilidades, dashboards financieros/economicos.
- `users`: usuario custom, roles, permisos, contexto de empresas/sucursales.
- `core`: shell, dashboard inicial y contexto global.

Documentacion que debe leerse antes de cambios sustanciales:

1. `context.md`
2. `docs/engineering-guidelines.md`
3. Documento de epica relevante en `docs/epics/`, si aplica.
4. Skill interno relevante en `.agents/skills/`, si aplica.
5. Archivos reales involucrados antes de proponer cambios.

## 2. Regla de oro

No hagas cambios amplios para resolver problemas chicos.

Cada pedido debe convertirse en un slice chico, verificable y reversible. Un fix no debe convertirse en refactor general. Una mejora visual no debe alterar reglas de negocio. Una regla de negocio no debe esconderse en templates.

## 3. Traduccion obligatoria de pedidos informales

Los pedidos del usuario pueden venir en lenguaje informal, por ejemplo:

- "arregla esto"
- "que no se mezcle"
- "hacelo mas prolijo"
- "agrega tal boton"
- "esto esta mal en el dashboard"

Antes de implementar, reinterpretar el pedido como contrato tecnico-funcional usando este formato mental:

- Pedido literal: que dijo el usuario.
- Intencion real: que resultado operativo espera.
- Dominio afectado: cashops, treasury, users, core, UI, datos o combinacion.
- Fuente de verdad: modelo, servicio o snapshot que debe mandar.
- Alcance incluido: lo minimo que se va a tocar.
- Alcance excluido: lo que no se debe tocar aunque este cerca.
- Riesgo principal: dinero, deuda, permisos, empresa, migracion, UI, dashboard, concurrencia o legacy.
- Criterios de aceptacion: como se demuestra que quedo bien.
- Tests necesarios: tests focalizados y regresion proporcional.

Si falta informacion, elegir el supuesto mas seguro y dejarlo escrito. Solo preguntar si hay varias opciones funcionales incompatibles y ninguna es segura.

## 4. Respuesta esperada antes de cambios no triviales

Antes de modificar codigo en tareas medianas o riesgosas, explicar brevemente:

1. Diagnostico.
2. Opcion recomendada.
3. Si es optima o no.
4. Alcance aislado.
5. Archivos probables a tocar.
6. Pruebas a correr.

Usar esta clasificacion:

- `OPTIMA`: resuelve la causa raiz, respeta arquitectura y no mete deuda innecesaria.
- `ACEPTABLE`: resuelve el problema con algun compromiso controlado y documentado.
- `NO OPTIMA`: parchea sintomas, duplica logica, rompe separacion de capas o aumenta riesgo de datos.

No implementar una opcion `NO OPTIMA` salvo pedido explicito del usuario y dejando el riesgo claro.

## 5. Arquitectura y SOLID aplicado

### Single Responsibility

- `models.py`: estado, relaciones, invariantes que nunca deben romperse.
- `services.py`: casos de uso, transacciones, dinero, deuda, cierres, pagos, snapshots y efectos entre agregados.
- `forms.py`: validacion de entrada y adaptacion a servicios.
- `views.py`: autenticacion, autorizacion, seleccion de form, mensajes y render.
- `templates/`: presentacion. Nunca formulas criticas ni reglas de dinero.
- `permissions.py`: reglas de acceso reutilizables.

### Open/Closed

Extender con servicios, helpers o funciones especificas antes que repetir condicionales en vistas/templates.
Si una formula aparece en dos lugares, extraerla.
Si una regla nueva afecta varios flujos, centralizarla en dominio/servicio.

### Liskov Substitution

No crear variantes que rompan contratos existentes. Si un servicio registra un pago, cierre o movimiento, debe mantener validaciones, auditoria y side effects esperados.

### Interface Segregation

Forms, vistas y servicios deben pedir solo lo necesario. No exponer campos decorativos que no tengan efecto real.

### Dependency Inversion

La UI depende del dominio; el dominio no depende de templates ni request. Las integraciones futuras deben entrar detras de una interfaz o servicio propio, no contaminando reglas actuales.

## 6. Reglas de aislamiento por cambio

Cada cambio debe cumplir:

- Un objetivo funcional principal.
- Un dominio dueño principal.
- Archivos tocados justificados.
- Sin refactors no pedidos.
- Sin cambios de copy masivos salvo tarea de copy.
- Sin cambios de formato global salvo tarea de formato.
- Sin modificar migraciones ya aplicadas salvo razon extrema.
- Sin borrar trazabilidad historica para "limpiar" datos.

Cuando el pedido mezcle varias cosas, cortar por riesgo:

1. datos/dominio,
2. permisos,
3. servicios,
4. UI,
5. limpieza/refactor.

Implementar de a un slice.

## 7. Reglas criticas de datos e integridad

Estas reglas son obligatorias:

- Usar `Decimal` para dinero.
- No calcular dinero critico en templates.
- No duplicar formulas de saldos, deuda, cierre, disponibilidad o resultado economico.
- No hacer deletes fisicos de registros financieros/operativos auditables; preferir anulacion o reversa auditada.
- Todo flujo con dinero, deuda, cierre, pago, acreditacion, caja central o banco debe pasar por servicios.
- Usar `transaction.atomic()` cuando el caso de uso cree o modifique mas de un registro relacionado.
- Usar `select_for_update()` o estrategia equivalente cuando exista riesgo de doble pago, doble cierre, doble anulacion o carrera de saldo.
- Ejecutar `full_clean()` antes de guardar entidades sensibles, salvo excepcion documentada.
- Mantener auditoria: usuario, fecha, origen, motivo y relacion con movimiento fuente cuando aplique.

## 8. Aislamiento por empresa y sucursal

Regla permanente: las empresas estan aisladas por defecto.

Todo cambio que toque empresa, sucursal, caja, cuenta, proveedor, pago, movimiento, dashboard o reporte debe validar:

- listados respetan contexto de empresa seleccionado;
- querysets de forms no ofrecen datos de empresas no seleccionadas;
- acceso directo por URL no filtra datos ajenos;
- servicios rechazan cruces invalidos;
- dashboards y snapshots no suman datos fuera de contexto;
- tests cubren fuga cross-company si el riesgo existe.

## 9. Migraciones y legacy

No endurecer datos historicos de golpe.

Estrategia preferida:

1. Agregar campo/regla compatible.
2. Backfill o comando de saneamiento.
3. Validar nuevas escrituras.
4. Volver obligatorio recien cuando legacy este controlado.

Toda migracion riesgosa debe declarar:

- que datos viejos existen;
- como se transforman;
- que pasa si hay datos incompletos;
- como se prueba rollback conceptual o compatibilidad.

## 10. Testing obligatorio proporcional al riesgo

Cambios que toquen dinero, deuda, permisos, cierres, anulaciones, dashboards, migraciones o reportes requieren tests.

Minimo esperado:

- Test de servicio para regla de negocio.
- Test de vista si hay permisos, filtros, botones o formularios.
- Test de migracion/comando si hay legacy o backfill.
- Test de regresion que falle sin el fix cuando se corrige un bug.

Comandos sugeridos:

```bash
python manage.py test cashops.tests -v 1
python manage.py test treasury.tests -v 1
python manage.py test users.tests -v 1
python manage.py makemigrations --check --dry-run
python -m compileall cashops treasury users core
```

En Windows del repo, puede requerirse:

```powershell
$env:PYTHONPATH=".venv\Lib\site-packages"
py -3.13 manage.py test treasury.tests -v 1
py -3.13 manage.py makemigrations --check --dry-run
py -3.13 -m compileall cashops treasury users core
```

Si no se pueden correr tests, no inventar. Informar exactamente que no se pudo validar y que riesgo queda.

## 11. Uso de skills internos

Cuando corresponda, leer el skill antes de tocar codigo:

- Caja, turnos, movimientos, cierres: `.agents/skills/caja-sucursales-operativa/SKILL.md` o `.agents/skills/cash-ops-domain/SKILL.md`.
- Tesoreria, banco, deuda, pagos, disponibilidades: `.agents/skills/tesoreria-financiera-consolidada/SKILL.md`.
- Rentabilidad, periodos, rubros, situacion economica: `.agents/skills/control-gestion-rentabilidad/SKILL.md`.
- Usuarios, roles, permisos: `.agents/skills/usuarios-operativos-admin/SKILL.md`.
- UI/templates/CSS: `.agents/skills/ui-desktop-first/SKILL.md` o `.agents/skills/mobile-first-htmx-ui/SKILL.md`.
- Testing: `.agents/skills/testing-riguroso-extremo/SKILL.md`.
- Backlog/epicas/user stories: `.agents/skills/analista-funcional-backlog/SKILL.md`.

Los skills no reemplazan este archivo. Este archivo manda a nivel repo.

## 12. Context.md

Actualizar `context.md` con notas compactas cuando haya:

- decision funcional nueva;
- regla de dominio descubierta;
- bug critico;
- workaround temporal;
- archivos tocados;
- tests corridos y resultado;
- riesgo pendiente.

No convertir `context.md` en diario largo. Debe servir para que el proximo agente entienda el estado real.

## 13. Prohibiciones practicas

No hacer:

- cambios silenciosos en reglas de dinero;
- formulas duplicadas en vista y servicio;
- botones que aparecen pero no tienen permiso/validacion real;
- filtros visuales que no filtran en backend;
- deletes fisicos de movimientos financieros;
- mezclar caja operativa, banco y caja central sin fuente de verdad clara;
- arreglos de UI que cambian comportamiento;
- refactors grandes junto con fixes;
- commits con `.env`, `.venv`, `db.sqlite3`, `__pycache__`, logs o archivos generados.

## 14. Cierre de tarea

Toda entrega debe informar:

- que se cambio;
- por que esa opcion es optima, aceptable o no optima;
- archivos tocados;
- tests ejecutados;
- riesgos o pendientes reales;
- si `context.md` fue actualizado.

Si el cambio no esta listo, decirlo. Mejor una entrega parcial honesta que un verde falso.

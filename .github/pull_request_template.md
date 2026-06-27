# Resumen

## Tipo de cambio

- [ ] Bugfix
- [ ] Feature
- [ ] Mejora UI/copy
- [ ] Refactor aislado
- [ ] Migracion / legacy
- [ ] Tests / documentacion

## Opcion elegida

- [ ] OPTIMA: corrige causa raiz, respeta arquitectura y tiene tests proporcionales.
- [ ] ACEPTABLE: resuelve con compromiso controlado y documentado.
- [ ] NO OPTIMA: parche temporal solicitado explicitamente.

Explicacion breve:

## Alcance

Incluye:

No incluye:

## Dominio afectado

- [ ] `cashops`
- [ ] `treasury`
- [ ] `users`
- [ ] `core`
- [ ] templates/CSS
- [ ] migraciones
- [ ] documentacion

## Seguridad e integridad

- [ ] No duplica formulas criticas.
- [ ] No calcula dinero en templates.
- [ ] No borra trazabilidad financiera/operativa.
- [ ] Respeta empresa/sucursal seleccionada.
- [ ] Protege acceso directo por URL si hay permisos.
- [ ] Considera legacy/backfill si toca modelos.
- [ ] Usa servicios para dinero/deuda/cierres/pagos.

## Validacion

Tests ejecutados:

```text

```

Resultado:

Riesgos pendientes:

## Contexto

- [ ] `context.md` actualizado si habia decision, bug, riesgo o cambio funcional relevante.
- [ ] Epica/backlog actualizado si corresponde.

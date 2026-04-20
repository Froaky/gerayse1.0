# Gerayse Backlog Format

Usar esta referencia cuando el pedido sea para este repo.

## Fuentes primarias

- `context.md`
- `docs/epics/README.md`
- `docs/epics/EP-03-tesoreria-central.md`
- `docs/epics/EP-04-bancos-y-conciliacion.md`
- `docs/epics/EP-05-flujo-de-disponibilidades.md`
- `docs/epics/EP-06-control-de-gestion-y-alertas.md`
- `docs/epics/EP-07-impuestos-planes-y-autorizaciones.md`

## Estilo observado

- Markdown simple, sin tablas.
- Espanol directo y operativo.
- Listas cortas.
- Reglas de negocio expresadas como invariantes.
- Historias numeradas por epica: `US-3.1`, `US-3.2`, etc.
- Las epicas implementadas o cerradas usan `### [x] ...`.
- Para backlog nuevo, usar `### [ ] ...` salvo que el usuario pida otro estado.

## Estructura base

```md
# EP-XX Nombre de la epica

## Objetivo

## Incluye

## No incluye todavia

## Reglas de negocio

## User Stories

### [ ] US-x.y Titulo

Como ...
Quiero ...
Para ...

Criterios:
- ...

## Dependencias

## Orden tecnico sugerido

## Criterio de cierre
```

## Criterios de buena epica

- Resuelve un problema operativo reconocible.
- Se puede implementar en iteraciones sin romper trazabilidad.
- No mezcla capas que todavia no agregan valor visible.
- Explicita exclusiones para evitar sobrealcance.
- Tiene cierre verificable desde negocio, no solo desde codigo.

## Sesgos del producto actual

- Priorizar control interno y auditabilidad.
- No asumir integraciones bancarias reales si no estan pedidas.
- Separar caja, tesoreria, bancos y control de gestion.
- Evitar estados libres o controles manuales no trazables.

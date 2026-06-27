# ADR 0001 - Control de cambios asistidos por IA

## Estado

Propuesto.

## Contexto

Gerayse es un sistema operativo-financiero donde cambios pequenos pueden afectar saldos, deuda, cierres, permisos y trazabilidad. El desarrollo asistido por IA acelera implementacion, pero tambien aumenta el riesgo de mezclar arreglos, refactors y cambios funcionales no pedidos.

El repo ya tiene guias de ingenieria, contexto historico y skills por dominio. Faltaba una regla central que obligue a convertir pedidos informales en cambios aislados y verificables.

## Decision

Adoptar `AGENTS.md` como contrato principal para cualquier agente de IA.

Agregar documentos de apoyo en `docs/ai/` para:

- protocolo de cambio;
- mapa de arquitectura;
- matriz de testing;
- contrato prompt -> implementacion.

Agregar wrappers para herramientas comunes:

- `CLAUDE.md` para Claude Code;
- `.cursor/rules/gerayse-agent.mdc` para Cursor;
- `.github/pull_request_template.md` para revisar cambios humanos o asistidos.

## Consecuencias positivas

- Los cambios quedan acotados por slice.
- El agente debe explicar si la opcion es optima o no.
- Se reduce riesgo de duplicar formulas o mover reglas a templates.
- Se vuelve obligatorio pensar en empresa/sucursal, legacy, auditoria y tests.
- Las entregas quedan mas faciles de revisar.

## Consecuencias negativas

- Cambios simples pueden requerir un poco mas de disciplina.
- El agente debe leer mas contexto antes de tocar areas sensibles.
- Algunas soluciones rapidas se rechazaran por no ser optimas.

## Regla de revision

Esta decision debe revisarse si:

- el repo deja de ser monolito Django;
- se separan servicios por dominio;
- se incorpora API externa real bancaria/contable;
- se adopta otro flujo formal de PR/CI que reemplace parte de estas reglas.

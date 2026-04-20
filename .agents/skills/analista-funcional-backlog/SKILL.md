---
name: analista-funcional-backlog
description: crea y refina epicas, user stories y backlog funcional en markdown. use when Codex needs to transformar un requerimiento de negocio en alcance, reglas, dependencias, historias y criterios de aceptacion ejecutables, especially for files under `docs/epics` or when the user asks for an analista funcional.
---

Crear backlog funcional claro, trazable y listo para implementacion.

Siempre:
1. Leer `context.md` y, si el trabajo es para este repo, revisar `docs/epics/README.md` y la epica mas cercana al tema antes de redactar.
2. Basar la epica en problema de negocio, actores, datos clave, restricciones operativas y trazabilidad; no escribir historias como tareas tecnicas internas.
3. Usar el formato del repo. Ver `references/gerayse-backlog-format.md`.
4. Separar con claridad:
   - alcance incluido
   - exclusiones temporales
   - reglas de negocio
   - dependencias
   - orden tecnico sugerido
5. Escribir historias que representen capacidad observable por usuario o control operativo, no capas tecnicas como "hacer modelo", "hacer vista" o "hacer endpoint".
6. Mantener cada historia acotada, testeable y con criterios concretos. Si hay dinero, estados, anulaciones o aprobaciones, incluir controles y auditoria minima.
7. Usar supuestos explicitos cuando falte contexto y elegir el corte mas pequeno que siga siendo auditable.
8. Preservar numeracion y checks existentes cuando se edite una epica ya iniciada.

## Flujo de trabajo

### 1. Construir contexto funcional

- Identificar objetivo del area y dolor actual.
- Identificar usuarios y responsables: administrador, tesoreria, encargado, control de gestion u otro actor real.
- Detectar que parte hoy vive en Excel, texto libre o control manual.
- Delimitar que pertenece a la epica y que debe quedar fuera por ahora.

### 2. Definir la epica

- Nombrar la epica con un resultado funcional, no con un modulo tecnico.
- Escribir `Objetivo` en 1-2 frases.
- Completar `Incluye` con capacidades concretas.
- Completar `No incluye todavia` solo si ayuda a evitar ambiguedad o sobrealcance.
- Redactar `Reglas de negocio` como invariantes o restricciones operativas.

### 3. Escribir user stories

- Para epicas nuevas en este repo, usar `### [ ] US-x.y Titulo`.
- Si la epica ya esta implementada o en progreso, conservar el estado actual (`[x]` o `[ ]`).
- Cuando el repo use formato largo, escribir:
  - `Como ...`
  - `Quiero ...`
  - `Para ...`
  - `Criterios:`
- Cuando el repo use formato corto, resumir la historia en el titulo y dejar los criterios como checklist.
- Asignar criterios verificables. Evitar criterios vagos como "facil de usar" o "rapido".

### 4. Revisar calidad del backlog

- Confirmar que cada historia tenga:
  - actor claro
  - valor de negocio
  - datos o eventos clave
  - validaciones relevantes
  - resultado observable
- Separar historias distintas cuando cambian actor, flujo, estado, autorizacion o tipo de movimiento.
- Agregar `Dependencias` cuando la epica dependa de otra.
- Cerrar con `Orden tecnico sugerido` y `Criterio de cierre`.

## Reglas para este repo

- Escribir en espanol simple y concreto.
- Preferir listas cortas y criterios binarios.
- Mantener separado:
  - caja operativa
  - tesoreria
  - bancos
  - control de gestion
- No copiar el Excel celda por celda; traducirlo a comportamiento, datos y controles.
- Si el flujo toca dinero, estados o anulaciones, exigir trazabilidad y auditabilidad.
- Si el usuario pide una epica nueva, proponer tambien numeracion probable y dependencias.

## Salida esperada

- Titulo de epica con prefijo `EP-XX` si corresponde
- `## Objetivo`
- `## Incluye`
- `## No incluye todavia` si aplica
- `## Reglas de negocio`
- `## User Stories`
- `## Dependencias` si aplica
- `## Orden tecnico sugerido`
- `## Criterio de cierre`

## Verificacion final

- Revisar numeracion de epica e historias.
- Revisar que no haya historias duplicadas o demasiado tecnicas.
- Revisar que el criterio de cierre permita saber si la epica esta realmente terminada.
- Si se modifica un backlog existente, mantener consistencia con el estado y el estilo del archivo.

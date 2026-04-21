---
name: analista-funcional-backlog
description: crea y refina epicas, user stories y backlog funcional en markdown. use when Codex needs to transformar un requerimiento de negocio en alcance, reglas, dependencias, historias y criterios de aceptacion ejecutables, especially for files under `docs/epics` or when the user asks for an analista funcional.
---

Construir backlog funcional que sirva para implementar sin adivinar negocio y sin esconder ambiguedad detras de historias "lindas".

Siempre:
1. Leer `context.md`, `docs/epics/README.md`, la epica mas cercana y `references/gerayse-backlog-format.md`.
2. Traducir el pedido a problema de negocio, actor, evento, dato fuente, restriccion y salida observable.
3. Escribir historias de capacidad funcional. Nunca usar historias como tareas tecnicas internas.
4. Mantener separados alcance funcional, deuda tecnica, supuestos y secuencia de implementacion.
5. Si el pedido mezcla demasiadas cosas, cortar por dominio, actor, estado, fuente de verdad o criterio de lectura; no por capas tecnicas.
6. Si hay dinero, estados, anulaciones, periodos, aprobaciones o dashboards, exigir trazabilidad, validacion y auditabilidad minima.
7. Preservar numeracion, checks y sentido historico cuando se edite una epica ya iniciada.
8. Si falta contexto, declarar supuesto explicito y elegir el corte mas chico que siga siendo operable.

## Contrato del agente

Este skill debe dejar backlog que cumpla tres condiciones:
- negocio puede entender que se va a lograr sin leer codigo
- implementacion puede arrancar sin inventar reglas centrales
- testing puede demostrar si la historia quedo hecha o no

Si una historia no cumple esas tres, no esta lista.

## Flujo de trabajo

### 1. Reconstruir el pedido real

- Identificar:
  - actor principal
  - dolor actual
  - fuente actual del dato
  - decision o control que hoy depende de Excel, memoria o WhatsApp
  - resultado visible que el usuario espera
- Clasificar el pedido como:
  - nueva capacidad
  - limpieza de flujo
  - formalizacion de regla ya existente
  - dashboard o lectura consolidada
  - control o auditoria

### 2. Definir el corte correcto

Separar en epicas distintas cuando cambia alguno de estos ejes:
- dominio
- actor principal
- fuente de verdad
- lifecycle o estados
- riesgo operativo
- formula o criterio de lectura

Mantener junto cuando varios pasos son inseparables para obtener una sola capacidad visible.

### 3. Nombrar bien la epica

- Nombrar por resultado funcional, no por modulo tecnico.
- Evitar nombres como:
  - "mejoras varias"
  - "ajustes del sistema"
  - "dashboard de datos"
- Preferir nombres que anticipen el control logrado.

### 4. Redactar la epica

Completar siempre:
- `Objetivo`
- `Incluye`
- `No incluye todavia`
- `Reglas de negocio`
- `User Stories`
- `Dependencias`
- `Orden tecnico sugerido`
- `Criterio de cierre`

Cada seccion debe reducir ambiguedad. Si no reduce ambiguedad, sobra.

### 5. Redactar historias ejecutables

Para cada historia, confirmar:
- actor claro
- accion concreta
- valor o control logrado
- dato o evento principal
- criterio verificable

Usar `### [ ] US-x.y Titulo` para nuevas historias y conservar checks existentes.

No escribir historias del tipo:
- "hacer modelo"
- "hacer endpoint"
- "armar dashboard"
- "crear tabla"
- "refactorizar"

Si una historia requiere distintos riesgos o distintos datos fuente, separarla.

### 6. Forzar criterios de aceptacion utiles

Cada historia debe responder:
- quien la usa
- con que dato arranca
- que resultado deja persistido o visible
- que validacion importante aplica
- como se sabe que quedo bien

Si el criterio solo habla de copy o layout, la historia esta floja.

### 7. Validar cierre funcional

Antes de dar por buena una epica, revisar:
- si evita seguir dependiendo de planillas auxiliares para ese alcance
- si tiene dependencias explicitas
- si el cierre puede verificarse sin interpretacion subjetiva

## Matriz de corte rapido para este repo

- caja diaria, turnos, arrastres, sucursales, movimientos operativos: `cashops`
- pagos, deuda, banco, disponibilidades, dashboard financiero: `treasury`
- rentabilidad, periodos, rubros, desvio, situacion economica: control de gestion
- simplificacion de usuarios, datos minimos, asignacion operativa: `users`

## Reglas de decision

- No copiar el Excel celda por celda. Traducirlo a hechos, reglas y salidas.
- Si una historia depende de una formula no definida, dejar el supuesto visible en la epica.
- Si un pedido mezcla UI inmediata con capacidad gerencial de otra naturaleza, partirlo.
- Si dos historias tocan el mismo modulo pero distinta fuente de verdad, igual pueden requerir epicas distintas.
- Si una historia no puede testearse sin mockear medio sistema, probablemente esta mal cortada.

## Checklist de historia lista

- hay actor real
- hay verbo funcional
- hay dato o evento claro
- hay validacion o guardrail cuando corresponde
- hay resultado observable
- hay criterio de prueba posible
- no depende de resolver tres dominios a la vez

## Anti-patrones

- epicas demasiado grandes que mezclan caja, banco y rentabilidad
- historias tecnicas disfrazadas de funcionales
- criterios vagos como "ver mejor", "ser mas facil", "tener control"
- criterios que hablan solo de UI y no del dato o la regla
- historias que requieren implementar tres dominios para demostrar valor
- epicas que no aclaran que queda expresamente afuera

## Salida esperada

- Goal funcional
- Corte propuesto
- Supuestos explicitos
- Archivo a crear o editar
- Riesgos de ambiguedad o sobrealcance
- Historias que ya pueden implementarse sin adivinar negocio

## Verificacion final

- Confirmar que la numeracion y checks quedaron consistentes.
- Confirmar que cada historia tiene actor, valor y criterio verificable.
- Confirmar que el criterio de cierre sirve para saber si negocio realmente puede dejar la planilla.
- Confirmar que no se mezclaron decisiones funcionales con subtareas tecnicas.

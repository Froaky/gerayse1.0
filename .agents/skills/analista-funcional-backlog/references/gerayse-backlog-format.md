# Gerayse Backlog Format

Usar esta referencia cuando el pedido sea para este repo.

## 1. Fuentes primarias

- `context.md`
- `docs/epics/README.md`
- `docs/epics/EP-03-tesoreria-central.md`
- `docs/epics/EP-04-bancos-y-conciliacion.md`
- `docs/epics/EP-05-flujo-de-disponibilidades.md`
- `docs/epics/EP-06-control-de-gestion-y-alertas.md`
- `docs/epics/EP-07-impuestos-planes-y-autorizaciones.md`
- `docs/epics/EP-08-ajustes-operativos-de-caja-y-sucursales.md`
- `docs/epics/EP-09-usuarios-operativos-y-datos-minimos.md`
- `docs/epics/EP-10-situacion-financiera-y-alertas-consolidadas.md`
- `docs/epics/EP-11-rentabilidad-y-situacion-economica.md`

## 2. Mapa funcional del backlog actual

- `EP-03` a `EP-05`: tesoreria, pagos, bancos, disponibilidades
- `EP-06` y `EP-07`: control, alertas, autorizaciones, impuestos
- `EP-08`: caja operativa y sucursales
- `EP-09`: usuarios operativos y datos minimos
- `EP-10`: lectura financiera consolidada
- `EP-11`: rentabilidad y situacion economica

## 3. Estilo observado

- Markdown simple, sin tablas.
- Espanol directo y operativo.
- Reglas de negocio escritas como invariantes.
- Historias numeradas por epica: `US-3.1`, `US-8.4`, etc.
- Backlog nuevo con `### [ ] ...`.
- Historias implementadas o cerradas con `### [x] ...`.

## 4. Estructura base

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

## 5. Como cortar bien una epica

Separar en epicas distintas cuando cambia:
- dominio
- actor principal
- fuente de verdad
- formula o criterio de lectura
- riesgo operativo

Mantener junto cuando:
- varios pasos son inseparables para que exista una sola capacidad visible
- una historia sin la otra deja una pantalla pero no una solucion

## 6. Reglas de split de user stories

Separar historias cuando cambia:
- el actor que recibe valor
- el hecho fuente
- la validacion critica
- el nivel de riesgo operativo
- el tipo de lectura: carga, aprobacion, dashboard, cierre, auditoria

Mantener en una sola historia cuando:
- varios pasos cortos sostienen una sola accion operativa
- sin el segundo paso el usuario no obtiene valor real
- comparten mismo actor, mismo dato fuente y mismo criterio de done

## 7. Checklist de historia lista para implementar

- actor real
- verbo funcional claro
- dato o evento clave
- validacion relevante
- resultado observable
- criterio testeable

## 8. Checklist de epica sana

- tiene objetivo acotado
- deja explicito que no entra todavia
- no mezcla modulos solo porque comparten pantalla
- tiene dependencias visibles
- tiene orden tecnico sugerido razonable
- tiene criterio de cierre que negocio pueda usar

## 9. Anti-patrones del repo

- mezclar caja, tesoreria y control de gestion en una sola historia
- escribir historias como subtareas de desarrollo
- usar criterios que solo hablan de copy o layout
- cerrar una epica sin un criterio de negocio verificable

## 10. Sesgos del producto actual

- priorizar control interno y auditabilidad
- no asumir integraciones bancarias reales si no estan pedidas
- separar caja, tesoreria, bancos y control de gestion
- evitar estados libres o controles manuales no trazables

## 11. Regla practica de cierre

Antes de marcar `[x]` una historia, poder responder:
- que capacidad nueva ya existe
- que validacion relevante ya corre
- que dato queda mas trazable que antes
- que test o evidencia automatizada la sostiene

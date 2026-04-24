# Engineering Guidelines

## Objetivo

Definir reglas de diseno e implementacion para que el codigo de Gerayse pueda crecer sin degradar consistencia, trazabilidad ni mantenibilidad.

Estas guias no reemplazan el criterio de ingenieria. Lo vuelven explicito y verificable en el repo.

## Principios base

- priorizar reglas de dominio claras sobre atajos de UI
- mantener separacion entre operacion de caja, lectura financiera y lectura economica
- endurecer el sistema sin romper datos legacy sin plan de migracion
- preferir cambios pequenos, testeables y trazables
- modelar procesos reales con entidades y servicios, no replicar Excel literalmente

## SOLID aplicado a este repo

### Single Responsibility

- cada modulo debe tener una responsabilidad dominante
- `models.py` define estado, relaciones e invariantes de dominio
- `services.py` implementa casos de uso, reglas transaccionales y efectos entre agregados
- `forms.py` valida entrada del usuario y adapta datos a servicios
- `views.py` orquesta request/response, permisos de acceso y rendering
- `templates/` presenta datos; no decide reglas de negocio

### Open/Closed

- extender comportamiento por nuevos servicios, helpers o clases especializadas antes que parchear condicionales repetidos
- si una regla nueva cambia varios flujos, centralizarla en dominio o servicio compartido
- evitar duplicar formulas de snapshot, saldo, deuda o desvio en mas de una capa

### Liskov Substitution

- no introducir variantes de flujo que rompan contratos existentes de servicios o estados
- si un servicio acepta una entidad o DTO implicito, debe mantener validaciones y side effects esperados
- no usar flags para saltear invariantes salvo excepciones tecnicas muy controladas, como backfills o guards internos explicitados

### Interface Segregation

- formularios y vistas deben depender de entradas minimas necesarias para el caso de uso
- no exponer campos o filtros decorativos que no tengan efecto real
- separar lecturas operativas, financieras y economicas cuando el usuario o el calculo no necesiten la misma interfaz

### Dependency Inversion

- la UI depende del dominio, no al reves
- views y forms llaman servicios; no deben reimplementar reglas sensibles
- snapshots y reportes deben construirse desde fuentes persistidas y reglas compartidas
- cuando una integracion externa no exista, modelar una interfaz interna estable antes de acoplar el flujo a detalles futuros

## Reglas por capa

### Models

- poner en modelos las invariantes que nunca deben romperse
- usar `clean()` o validaciones equivalentes cuando una restriccion pertenece a la entidad
- no meter coordinacion de procesos multi-paso en `save()`
- si un `save()` directo es peligroso, protegerlo con guards explicitos y documentados

### Services

- todo flujo con dinero, deuda, cierres, arrastres, pagos o acreditaciones debe pasar por servicios
- un servicio debe dejar claro:
  - inputs requeridos
  - validaciones de negocio
  - efectos secundarios
  - transaccionalidad si aplica
- preferir funciones chicas y compuestas antes que servicios gigantes con ramas opacas
- si un servicio devuelve datos para dashboard o lectura consolidada, mantener una sola formula canonica

### Forms

- validar consistencia de entrada y mensajes de usuario
- no calcular estados de deuda, saldos, cierres o clasificaciones financieras en formularios
- si el formulario expone un campo, el servicio correspondiente debe soportarlo de forma explicita

### Views

- mantenerlas finas
- resolver autenticacion, autorizacion de acceso, seleccion de form y render
- no duplicar reglas que ya viven en servicios o modelos
- si una vista necesita armar demasiada logica, moverla a servicio o helper dedicado

### Templates

- no poner logica de negocio ni calculos criticos
- limitarse a presentacion, etiquetas, estados visuales y navegacion
- si un calculo afecta dinero, alertas o decisiones, debe llegar precomputado desde Python

## Dominio y modelado

- una fuente de verdad por concepto:
  - estado de deuda: `CuentaPorPagar`
  - pagos: servicios de tesoreria + pagos no anulados
  - caja operativa: `cashops`
  - lectura financiera consolidada: snapshots financieros
  - lectura economica: snapshots economicos por periodo
- no mezclar caja fisica, banco y ventas a acreditar
- no usar texto libre para estados criticos si puede existir enum, FK o regla estructurada
- todo cambio de comportamiento sobre legacy debe declarar:
  - que datos viejos quedan admitidos
  - que datos nuevos pasan a ser obligatorios
  - como se evita romper consultas existentes

## Migraciones y compatibilidad

- toda migracion que endurece una restriccion debe tener estrategia de backfill o compatibilidad transitoria
- preferir:
  1. agregar campo o regla en modo compatible
  2. poblar legacy
  3. volver obligatorio
- documentar impacto funcional cuando legacy queda fuera de una lectura consolidada
- no usar migraciones para esconder decisiones de negocio no cerradas

## Testing minimo esperado

- todo cambio en dinero, deuda, permisos, cierres, dashboards, migraciones o reglas operativas sensibles requiere tests
- cubrir al menos:
  - caso feliz
  - validacion de regla principal
  - regresion o borde mas riesgoso
- cuando una historia toca varias capas, priorizar tests de servicio y agregar tests de vista donde haya riesgo de integracion
- antes de cerrar un slice sensible, correr como minimo:
  - tests focalizados del modulo afectado
  - `makemigrations --check`
  - `python -m compileall` sobre apps tocadas cuando corresponda

## Reglas de crecimiento

- una nueva feature debe entrar en la app dueña del dominio; no crear dependencias circulares por comodidad
- si una regla empieza a repetirse entre apps, extraer helper o servicio compartido con ownership claro
- evitar helpers genericos sin dominio real
- nombrar segun lenguaje del negocio cuando el concepto sea estable; usar nombres tecnicos solo para infraestructura
- una abstraccion nueva debe bajar complejidad real, no esconderla

## Checklist de revision

Antes de dar por bueno un cambio, confirmar:

- la logica sensible vive en dominio o servicios
- no se duplico una formula ya existente
- la UI usa terminos consistentes con negocio
- legacy quedo tratado de forma explicita
- hay tests proporcionales al riesgo
- `context.md` refleja la decision, impacto y riesgos

## Regla practica para este repo

Si una decision afecta dinero, deuda, cierres, autorizaciones, snapshots o trazabilidad, la implementacion debe ser defendible desde dominio, no solo desde la pantalla.

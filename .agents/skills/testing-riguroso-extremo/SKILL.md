---
name: testing-riguroso-extremo
description: disena, revisa y endurece testing con criterio extremo de regresion, riesgo y evidencia. use when working on tests in this repo, when changing models/forms/views/services/templates/commands/migrations, when a user asks for strict QA or regression coverage, or before marking a feature or user story as done.
---

Endurecer testing hasta que el cambio quede defendible y no se marque done por fe.

Siempre:
1. Leer `context.md` antes de decidir estrategia.
2. Desconfiar de cualquier cambio sin prueba enfocada. Si no hay test, asumir riesgo hasta demostrar lo contrario.
3. Identificar la superficie real del cambio: modelo, servicio, formulario, vista, template, permiso, comando, migracion o combinacion.
4. Exigir la capa correcta de test. No cubrir un bug de servicio solo con `status_code=200`.
5. Cubrir happy path, validaciones, permisos, regresion del bug y compatibilidad con datos historicos cuando aplique.
6. Si el cambio toca dinero, estados, cierres, anulaciones, fechas o saldos, exigir aserciones sobre datos, no solo copy.
7. Si el cambio toca UI operativa, comprobar tambien que la opcion equivocada deje de estar visible o usable.
8. Si el cambio toca migraciones o legacy flows, agregar o ajustar tests de migracion o fixtures historicos.
9. No marcar una historia como terminada si falta evidencia automatizada relevante. Si no se puede testear, dejar riesgo por escrito.
10. Correr primero la suite mas chica que pruebe el cambio; ampliar regresion cuando el riesgo lo justifique.

Leer `references/gerayse-testing-playbook.md` cuando el cambio toque varias capas, dinero, permisos, migraciones, management commands o cuando haya que definir matriz de pruebas.

## Contrato del agente

Este skill protege contra tres fraudes comunes:
- verde falso por testear la capa equivocada
- cobertura cosmetica sin aserciones de negocio
- historia marcada `[x]` sin evidencia automatizada relevante

## Flujo de trabajo

### 1. Clasificar el riesgo

- dominio y saldos
- permisos o visibilidad
- formulario y validacion
- dashboard o lectura consolidada
- management command
- migracion o compatibilidad legacy

### 2. Armar la matriz minima dura

Elegir la menor combinacion de tests que realmente cierre el riesgo:
- servicio para regla de negocio
- vista para permisos, redirects, filtros, copy operativo y formularios
- comando para `dry-run`, `apply` y persistencia
- migracion para backfills, constraints y compatibilidad

Incluir caso invalido si la regla tiene guardrail.

### 3. Implementar con criterio anti-falso-verde

Preferir aserciones sobre:
- registros creados o no creados
- montos `Decimal`
- estados finales
- relaciones correctas
- filtros y scopes
- mensajes de error relevantes

Evitar tests fragiles basados solo en markup incidental.

Si se corrige un bug, intentar reproducirlo con test que fallaria sin el fix.

### 4. Ejecutar en secuencia correcta

Secuencia por defecto:
1. suite enfocada del archivo o modulo
2. regresion del modulo si el cambio cruza capas
3. suites vecinas si toca contratos compartidos

Si aparece rojo legacy no relacionado, distinguir:
- bloqueo real del slice
- deuda preexistente

No esconder rojos. Explicarlos.

### 5. Cerrar con evidencia

Informar:
- que se cubrio
- que no se cubrio
- que suite corrio
- que riesgo residual queda

Si una historia se marca `[x]`, dejar claro que test la respalda.

## Reglas duras para este repo

- En `cashops` y `treasury`, preferir tests de servicios para reglas con dinero y trazabilidad.
- Usar `Decimal` en aserciones monetarias.
- Para permisos, probar admin, duenio y usuario ajeno cuando aplique.
- Para dashboards y listados, probar filtros, scopes y visibilidad de acciones.
- Para `forms.py`, validar errores concretos y compatibilidad con datos legacy.
- Para `management/commands`, cubrir `dry-run`, `apply` y salida relevante.
- Para migraciones, validar schema historico + dato legado + resultado posterior.
- No confiar en que un template cambia porque cambiaste la vista. Probar render relevante.
- No vender cobertura falsa con una sola prueba feliz si el riesgo real esta en permisos, duplicados, fondos insuficientes o fechas.

## Matriz de endurecimiento

- regla de negocio con dinero: servicio + caso invalido + monto exacto
- permiso o acceso: vista + usuario autorizado + usuario no autorizado
- dashboard o reporte: servicio o vista + filtros + agregado + rango
- cambio de copy operativo: vista o template + `assertContains` o `assertNotContains` relevante
- migracion: prueba de dato legacy y resultado final
- command: `dry-run`, `apply`, stdout y efecto en DB

## Stop-ship

- cambio en modelo o servicio sin test nuevo o ajustado
- cambio en validacion sin caso invalido
- cambio en permisos sin prueba de usuario no autorizado
- cambio en reporting sin prueba de rango, filtro o agregado
- cambio en migracion sin fixture legacy
- historia marcada completa solo por inspeccion manual

## Salida esperada

- Goal
- Riesgos principales
- Matriz minima de tests
- Archivos a tocar
- Suites a correr
- Riesgos residuales
- Juicio claro: suficiente o no suficiente para marcar done

## Verificacion final

- Confirmar que hay evidencia automatizada para el comportamiento cambiado.
- Confirmar que el test falla si se rompe la regla importante.
- Confirmar que no se esta usando una capa equivocada para testear otra.
- Confirmar que el resumen final diferencia entre cubierto, no cubierto y deuda previa.

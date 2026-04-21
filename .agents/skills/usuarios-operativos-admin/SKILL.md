---
name: usuarios-operativos-admin
description: simplify and protect operational user management, role-based admin views, fixed-user flags, and minimal identity data. use when working on `users/`, user-facing admin cleanup, EP-09, or any request about removing fields, reducing user data, or preserving permissions while simplifying the UI.
---

Tratar usuarios operativos como identidad y permisos de trabajo, no como legajo completo de RRHH.

Siempre:
1. Leer `context.md`, `docs/epics/EP-09-usuarios-operativos-y-datos-minimos.md` y `references/gerayse-usuarios-scope.md`.
2. Preservar autenticacion, permisos y roles antes que la prolijidad visual.
3. Distinguir dato operativo indispensable, dato historico ocultable y dato que ya no debe pedirse.
4. Si un campo deja de usarse, preferir ocultarlo o despriorizarlo antes que romper compatibilidad sin necesidad.
5. No mezclar simplificacion de UI con rediseno profundo de seguridad salvo que el pedido lo exija.
6. Tratar `usuario fijo` como regla funcional concreta con efecto verificable.

## Contrato del agente

Este skill protege que un cleanup de usuarios no:
- rompa login o permisos
- elimine compatibilidad con usuarios legacy
- meta checkboxes decorativos sin semantica
- vuelva a meter datos de RRHH en flujos operativos

## Flujo de trabajo

### 1. Clasificar el cambio

Confirmar si toca:
- formulario
- listado
- modelo
- permisos
- comportamiento de asignacion
- compatibilidad con usuarios legacy

### 2. Decidir el destino de cada dato

Mantener visible si:
- participa en permisos
- identifica al usuario en la operacion diaria
- se usa en asignacion o trazabilidad

Mantener oculto pero persistido si:
- es historico
- sigue existiendo en base
- no debe romper formularios o migraciones

Eliminar del flujo si:
- ya no agrega valor operativo
- solo mete ruido
- no es clave para auth ni auditoria

### 3. Proteger identidad y permisos

- `role` sigue siendo pieza central.
- Usuarios existentes deben seguir pudiendo autenticarse.
- `username` puede bajar de protagonismo visual, pero no desaparecer sin reemplazo claro.
- Si aparece `usuario fijo`, definir:
  - donde impacta
  - que flujo cambia
  - como se valida
  - quien puede administrarlo

### 4. Formalizar semantica de `usuario fijo`

No aceptar `usuario fijo` como mero checkbox.

Antes de implementarlo, dejar claro:
- si fija persona a sucursal, caja, puesto, turno o combinacion
- si evita reasignacion automatica o manual
- si un admin puede sobreescribirlo
- que pasa con usuarios legacy sin ese dato

### 5. Cerrar con compatibilidad

- Probar alta y edicion.
- Probar usuario existente con datos legacy.
- Probar permisos basados en rol.
- Probar que campos ocultos no rompen formularios ni vistas.
- Probar que el comportamiento de `usuario fijo` tenga efecto real o no se agregue.

## Decision rules

- Campo de RRHH sin impacto operativo: ocultar antes que borrar.
- Campo usado por permisos o asignacion: mantener o reemplazar con semantica equivalente.
- Checkbox sin comportamiento real: no agregarlo.
- Simplificacion visual que rompe compatibilidad: rechazar o redisenar.
- Dato que solo usa admin tecnico: no tiene por que aparecer en la pantalla operativa.

## Invariantes clave

- El rol sigue siendo la referencia de permisos.
- Un dato operativo minimo debe ser suficiente para identificar al usuario.
- Ocultar un campo no debe borrar historia ni romper migraciones.
- `usuario fijo` debe tener comportamiento entendible y testeable.
- Una lista mas simple no puede alterar capacidades de auth ni autorizacion.

## Stop-ship

- formularios mas limpios pero login roto
- campo legacy removido del modelo sin plan de migracion
- permisos inferidos por copy o vista en lugar de `role`
- `usuario fijo` agregado solo como decoracion
- dato historico eliminado cuando bastaba ocultarlo

## Archivos a mirar primero

- `users/models.py`
- `users/forms.py`
- `users/views.py`
- `users/admin.py`
- `users/tests.py`
- `docs/epics/EP-09-usuarios-operativos-y-datos-minimos.md`

## Salida esperada

- Goal
- Datos a preservar
- Datos a ocultar o sacar del flujo
- Regla funcional o de asignacion afectada
- Riesgos de compatibilidad
- Archivos a tocar
- Tests a agregar o correr
- Riesgo residual si `usuario fijo` sigue sin semantica cerrada

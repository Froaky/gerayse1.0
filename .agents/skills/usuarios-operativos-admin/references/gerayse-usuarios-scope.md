# Gerayse Usuarios Scope

Usar esta referencia cuando el pedido toque usuarios, personal o admin operativo.

## 1. Fuentes primarias

- `docs/epics/EP-09-usuarios-operativos-y-datos-minimos.md`
- `users/models.py`
- `users/forms.py`
- `users/views.py`
- `users/admin.py`
- `users/tests.py`

## 2. Estado actual

- `User` custom con `role`, `dni`, `legajo` y `telefono`
- deteccion de admin por `role.code`
- formularios y vistas ya simplificados en parte
- `legajo` ya salio del flujo operativo visible

## 3. Pendiente funcional real

- `usuario fijo o no fijo` sigue pendiente
- falta decidir impacto en asignacion o flujo diario

## 4. Matriz de tratamiento de campos

- `role`: conservar y testear
- `username`: conservar por auth aunque no sea protagonista en UI
- `dni`: puede quedar historico si no aporta al flujo
- `legajo`: historico, no pedir en flujo operativo
- `telefono`: mostrar solo si agrega valor operativo real

## 5. Preguntas obligatorias

- que dato identifica al usuario en la operacion diaria
- que dato solo sirve para historia o auditoria
- `usuario fijo` fija contra que entidad
- quien puede cambiarlo
- que pasa con usuarios ya existentes

## 6. Riesgos tipicos

- romper autenticacion al simplificar demasiado
- ocultar un campo que seguia siendo requerido por una validacion
- confundir "dato no visible" con "dato eliminable"
- crear `usuario fijo` sin definir efecto funcional

## 7. Stop-ship funcional

- alta de usuario rota
- edicion de usuario legacy rota
- permisos cambiados por accidente
- UI mostrando menos datos pero pidiendo los mismos en POST
- checkbox `usuario fijo` sin ninguna regla observable

## 8. Que probar casi siempre

- alta
- edicion
- usuario legacy
- permisos por rol
- listas minimas y busqueda
- comportamiento real de `usuario fijo` cuando exista

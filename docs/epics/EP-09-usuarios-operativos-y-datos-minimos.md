# EP-09 Usuarios Operativos y Datos Minimos

## Objetivo

Reducir la administracion de usuarios y personal a los datos realmente necesarios para operar, evitando campos de RRHH que no agregan valor al control diario.

## Incluye

- baja funcional del legajo en usuarios
- configuracion de usuario fijo o no
- simplificacion de la vista actual de personal/P2
- preservacion de rol como dato principal operativo
- vista operativa de usuarios desde Config
- archivo, reactivacion y baja protegida de usuarios
- primer ingreso con contrasena default, cambio obligatorio y link tokenizado
- ficha de usuario con permisos efectivos basados en reglas vigentes

## No incluye todavia

- legajo historico de RRHH
- liquidacion de sueldos
- gestion avanzada de personal
- matriz granular independiente por modulo, sucursal y accion cuando el backend todavia no aplica esas reglas en cada vista

## Reglas de negocio

- el usuario operativo no debe exigir datos que no se usan en la operatoria diaria
- el rol sigue siendo el dato principal de permisos y responsabilidad
- la condicion de usuario fijo o no debe quedar explicita y administrable
- en este backlog, `usuario fijo` se interpreta como asignacion operativa preferida a una sucursal o puesto base, no como bloqueo absoluto a una excepcion manual de administracion
- ocultar datos en UI no debe borrar historial previo ni romper compatibilidad
- un usuario creado con contrasena default debe cambiarla antes de operar
- el link de primer ingreso debe permitir definir contrasena propia sin conocer la default
- archivar un usuario debe impedir login sin borrar trazabilidad historica
- eliminar un usuario solo debe permitirse cuando no rompa operaciones asociadas
- la ficha de permisos solo debe permitir gestionar reglas que el sistema aplica realmente

## User Stories

### [x] US-9.1 Baja funcional del legajo

Como administracion
Quiero dejar de usar legajo en la gestion de usuarios
Para evitar mantener un dato que no participa de la operatoria

Criterios:
- el campo legajo deja de ser visible en altas y ediciones operativas
- el listado de usuarios no depende de legajo
- los datos historicos existentes no se pierden

### [x] US-9.2 Usuario fijo o no fijo

Como administracion
Quiero definir con un checkbox si un usuario queda fijo o no
Para controlar asignaciones operativas sin texto libre

Criterios:
- checkbox visible y editable
- valor por defecto definido
- si el usuario es fijo, el sistema propone su asignacion operativa base en los flujos compatibles
- administracion puede forzar una excepcion puntual sin perder la condicion de fijo
- cambio auditado

### [x] US-9.3 Vista minima de personal/P2

Como administracion
Quiero ver solo nombre, apellido y rol en la vista actual de personal
Para dejar una pantalla simple y alineada al uso real

Criterios:
- la vista muestra nombre, apellido y rol
- se ocultan campos no usados en la operatoria actual
- la busqueda sigue funcionando con esos datos base

### [x] US-9.4 Compatibilidad con usuarios existentes

Como administracion
Quiero que la simplificacion no rompa usuarios ya cargados
Para poder limpiar la UI sin migraciones manuales urgentes

Criterios:
- usuarios existentes siguen pudiendo autenticarse
- los permisos por rol no cambian por esta simplificacion
- los formularios soportan datos historicos sin error

### [x] US-9.5 Vista operativa de usuarios desde Config

Como administracion
Quiero entrar a una vista de Usuarios desde Config
Para ver usuarios activos o archivados y gestionar altas sin depender del admin tecnico

Criterios:
- el menu Config muestra Usuarios
- la vista lista usuarios con nombre, login, rol, estado y sucursal base cuando aplica
- se puede buscar por nombre, login, rol o sucursal
- las URLs anteriores de personal siguen funcionando por compatibilidad

### [x] US-9.6 Primer ingreso con cambio obligatorio

Como administracion
Quiero crear un usuario con contrasena default y link de primer ingreso
Para entregar acceso sin que esa contrasena quede como definitiva

Criterios:
- al crear o resetear contrasena desde el flujo operativo, el usuario queda marcado para cambiar contrasena
- si ingresa con la contrasena default, el sistema lo redirige al cambio obligatorio antes de operar
- la ficha del usuario muestra un link de primer ingreso mientras el cambio este pendiente
- el link permite definir una contrasena propia sin usar la default
- luego del cambio, el flag pendiente se desactiva y el link deja de ser valido

### [x] US-9.7 Ficha de usuario con permisos efectivos

Como administracion
Quiero abrir cada usuario y ver sus accesos efectivos
Para entender que puede hacer y ajustar rol, estado o sucursal base desde un solo lugar

Criterios:
- la ficha muestra acceso de lectura/escritura efectivo para caja, configuracion, tesoreria y usuarios
- la lectura sale de reglas reales vigentes: rol, estado activo, usuario fijo y sucursal base
- desde la ficha se puede cambiar rol, estado activo, usuario fijo y sucursal base
- un administrador no puede archivarse ni quitarse su propio acceso administrador por accidente

### [x] US-9.8 Archivo, reactivacion y baja protegida

Como administracion
Quiero archivar, reactivar o eliminar usuarios
Para cortar accesos sin perder trazabilidad operativa

Criterios:
- archivar cambia el usuario a inactivo e impide login
- reactivar vuelve a habilitar el acceso
- eliminar esta disponible solo para usuarios distintos al actual
- si el usuario tiene operaciones asociadas, la baja fisica se bloquea y se recomienda archivar

### [ ] US-9.9 Matriz granular por modulo, lugar y accion

Como administracion
Quiero asignar permisos granulares de lectura, escritura y acceso por modulo o lugar
Para separar usuarios que ven, cargan o administran distintas partes del sistema sin depender solo del rol

Criterios:
- la matriz define permisos por modulo real del sistema
- cada permiso tiene efecto backend en vistas, formularios y operaciones protegidas
- lectura y escritura se validan por separado cuando el flujo lo requiere
- los accesos por sucursal o empresa no filtran solo la pantalla, tambien validan operaciones de escritura
- usuarios legacy conservan su comportamiento hasta que administracion les asigne una matriz explicita

## Dependencias

- modulo de usuarios activo
- reglas de asignacion operativa definidas para caja y tesoreria
- decision funcional de granularidad para separar permisos por modulo, sucursal, empresa y accion

## Orden tecnico sugerido

1. acordar alcance del checkbox de usuario fijo
2. simplificar formularios y vistas de usuarios
3. ocultar legajo en flujo operativo
4. validar compatibilidad con usuarios existentes
5. implementar vista Usuarios y primer ingreso seguro
6. modelar matriz granular solo cuando se definan permisos que el backend pueda aplicar

## Criterio de cierre

- la administracion de usuarios queda reducida a datos operativos reales
- la vista de personal deja de exponer campos sin uso
- la asignacion fija o no fija deja de depender de acuerdos informales
- alta, archivo, reactivacion, baja protegida y primer ingreso se pueden gestionar sin admin tecnico
- la ficha de usuario muestra permisos reales, no permisos decorativos
- queda pendiente solo la granularidad avanzada si negocio necesita permisos por modulo, lugar y accion mas finos que el rol

# EP-09 Usuarios Operativos y Datos Minimos

## Objetivo

Reducir la administracion de usuarios y personal a los datos realmente necesarios para operar, evitando campos de RRHH que no agregan valor al control diario.

## Incluye

- baja funcional del legajo en usuarios
- configuracion de usuario fijo o no
- simplificacion de la vista actual de personal/P2
- preservacion de rol como dato principal operativo

## No incluye todavia

- legajo historico de RRHH
- liquidacion de sueldos
- gestion avanzada de personal

## Reglas de negocio

- el usuario operativo no debe exigir datos que no se usan en la operatoria diaria
- el rol sigue siendo el dato principal de permisos y responsabilidad
- la condicion de usuario fijo o no debe quedar explicita y administrable
- ocultar datos en UI no debe borrar historial previo ni romper compatibilidad

## User Stories

### [x] US-9.1 Baja funcional del legajo

Como administracion
Quiero dejar de usar legajo en la gestion de usuarios
Para evitar mantener un dato que no participa de la operatoria

Criterios:
- el campo legajo deja de ser visible en altas y ediciones operativas
- el listado de usuarios no depende de legajo
- los datos historicos existentes no se pierden

### [ ] US-9.2 Usuario fijo o no fijo

Como administracion
Quiero definir con un checkbox si un usuario queda fijo o no
Para controlar asignaciones operativas sin texto libre

Criterios:
- checkbox visible y editable
- valor por defecto definido
- impacto claro en los flujos donde la asignacion de usuario importa
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

## Dependencias

- modulo de usuarios activo
- reglas de asignacion operativa definidas para caja y tesoreria

## Orden tecnico sugerido

1. acordar alcance del checkbox de usuario fijo
2. simplificar formularios y vistas de usuarios
3. ocultar legajo en flujo operativo
4. validar compatibilidad con usuarios existentes

## Criterio de cierre

- la administracion de usuarios queda reducida a datos operativos reales
- la vista de personal deja de exponer campos sin uso
- la asignacion fija o no fija deja de depender de acuerdos informales

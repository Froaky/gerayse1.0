# EP-12 Empresas, Contexto Activo y Navegacion

## Objetivo

Permitir operar varias empresas dentro del sistema, asignar sucursales a cada empresa, aislar la informacion por empresa activa y mejorar la navegacion global con un header claro.

## Incluye

- maestro de empresas
- asignacion de sucursales a empresa
- selector de empresa activa junto al usuario
- filtros y totales por empresa
- aislamiento de informacion entre empresas
- header global con acceso a home
- menu desplegable de modulos o paginas principales

## No incluye todavia

- facturacion legal por empresa
- contabilidad formal multiempresa
- consolidacion contable entre sociedades
- permisos avanzados por empresa y rol fuera de los perfiles operativos actuales
- integraciones bancarias separadas por CUIT

## Reglas de negocio

- toda sucursal debe pertenecer a una empresa activa
- una empresa puede tener una o muchas sucursales
- una sucursal no puede pertenecer a mas de una empresa al mismo tiempo
- la empresa activa define que datos ve y opera el usuario en caja, tesoreria, bancos, reportes y dashboards
- los totales por empresa se derivan de las sucursales asociadas, no de cargas manuales
- cambiar la empresa activa no debe modificar datos; solo cambia el contexto de lectura y carga
- un usuario no debe ver informacion de una empresa fuera de su alcance permitido
- los datos legacy con `razon_social` en sucursal deben migrarse o mapearse a empresas sin perder trazabilidad
- el header debe ser comun a los modulos operativos y permitir volver al inicio sin depender del boton del navegador

## User Stories

### [x] US-12.1 Maestro de empresas

Como administracion
Quiero crear y mantener empresas
Para separar la operacion de cada razon social o unidad de negocio

Criterios:
- alta, edicion y baja logica de empresas
- nombre visible obligatorio
- identificador fiscal opcional pero unico si se informa
- estado activo/inactivo
- busqueda por nombre o identificador
- no se puede desactivar una empresa si deja sucursales activas sin contexto operativo valido

### [x] US-12.2 Asignacion de sucursales a empresa

Como administracion
Quiero asignar cada sucursal a una empresa
Para que la operacion diaria y los reportes queden correctamente agrupados

Criterios:
- al crear una sucursal se debe seleccionar una empresa activa
- al editar una sucursal se puede cambiar la empresa solo si no rompe reportes historicos o queda auditado
- cada sucursal tiene una unica empresa vigente
- las sucursales legacy con `razon_social` quedan asociadas a una empresa mediante backfill o migracion asistida
- las listas y filtros de sucursal muestran la empresa asociada
- no se permite abrir caja en una sucursal sin empresa asociada

### [x] US-12.3 Selector de empresa activa

Como usuario operativo o administrativo
Quiero seleccionar la empresa activa desde el header
Para cambiar rapidamente entre contextos de trabajo

Criterios:
- el selector aparece junto al nombre de usuario
- solo muestra empresas disponibles para el usuario
- al cambiar de empresa, el sistema redirige a una vista valida dentro del nuevo contexto
- la empresa activa queda visible en todo momento
- el cambio de empresa no borra datos ni cambia estados de caja, tesoreria o reportes
- si el usuario tiene una sola empresa disponible, el selector puede quedar como etiqueta no editable

### [x] US-12.4 Aislamiento de informacion por empresa

Como administracion
Quiero que los datos de una empresa no se mezclen con los de otra
Para operar varias empresas sin errores de caja, deuda o reportes

Criterios:
- las vistas de caja muestran solo sucursales, cajas, turnos y movimientos de la empresa activa
- tesoreria muestra solo deuda, pagos, cuentas, acreditaciones y movimientos bancarios asociados a la empresa activa cuando correspondan
- los dashboards financieros, operativos y economicos calculan totales dentro de la empresa activa
- no se puede cargar un movimiento usando sucursal, caja o cuenta de otra empresa
- los accesos directos por URL validan empresa activa y permisos
- los tests deben cubrir que un usuario no ve ni opera datos de otra empresa

### [x] US-12.5 Totales y reportes por empresa

Como administracion
Quiero ver totales consolidados por empresa
Para entender el resultado de todas las sucursales que pertenecen a esa empresa

Criterios:
- los dashboards permiten leer una empresa completa sumando sus sucursales
- se puede filtrar por empresa, sucursal y periodo cuando aplique
- los totales de caja se derivan de movimientos de sucursales de esa empresa
- los totales financieros se derivan de caja fuerte, banco, pagos y acreditaciones asociados al contexto de empresa
- los totales economicos respetan la misma empresa activa que la lectura operativa
- la vista debe distinguir `empresa completa` de `sucursal puntual`

### [x] US-12.6 Header global con acceso a inicio

Como usuario del sistema
Quiero ver un header con `Gerayse` como acceso al inicio
Para volver al home o dashboard principal desde cualquier modulo

Criterios:
- el texto `Gerayse` aparece como marca principal del header
- al hacer click en `Gerayse`, el usuario vuelve al home o dashboard inicial autorizado
- el header muestra el usuario autenticado
- el header muestra la empresa activa cuando exista
- el header se mantiene consistente en caja, tesoreria, usuarios y reportes
- el header no tapa acciones principales ni rompe la vista mobile

### [x] US-12.7 Menu desplegable de modulos

Como usuario del sistema
Quiero un menu desplegable de modulos o paginas principales
Para navegar sin tener botones sueltos en todas las pantallas

Criterios:
- existe un menu principal con accesos a Caja, Tesoreria, Usuarios, Sucursales, Reportes y configuraciones segun permisos
- el menu muestra solo opciones que el usuario puede usar
- las acciones frecuentes de una pantalla quedan dentro de su contexto, no todas en el header
- el menu funciona en escritorio y celular
- la navegacion actual no se pierde: las rutas existentes siguen accesibles desde algun punto claro
- el diseño reduce ruido visual sin esconder acciones criticas de operacion diaria

## Dependencias

- EP-08 para sucursales, caja operativa y dashboard de caja
- EP-09 para usuarios operativos y alcance por usuario
- EP-10 para dashboard financiero consolidado
- EP-11 para lectura economica por periodo

## Orden tecnico sugerido

1. crear maestro de empresas en modo compatible
2. asociar sucursales a empresa con backfill desde `razon_social`
3. definir empresa activa en sesion o contexto de usuario
4. filtrar caja y sucursales por empresa activa
5. filtrar tesoreria, bancos y reportes por empresa activa
6. agregar totales por empresa
7. incorporar header global con selector de empresa
8. reemplazar botones sueltos por menu de modulos segun permisos

## Criterio de cierre

- toda sucursal activa pertenece a una empresa
- el usuario puede cambiar de empresa desde el header cuando tiene mas de una disponible
- caja, tesoreria y reportes no mezclan informacion entre empresas
- administracion puede ver totales por empresa y por sucursal
- la navegacion principal queda ordenada desde un header comun y un menu de modulos

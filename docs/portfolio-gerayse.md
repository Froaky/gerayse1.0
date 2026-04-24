# Gerayse

## TL;DR

Gerayse es un sistema web de operacion financiera interna para sucursales, pensado para reemplazar planillas de Excel en procesos de caja diaria, cierres, tesoreria, cuentas por pagar, bancos y lectura economica por periodo.

Esta implementado como un monolito en Django con frontend server-rendered, enfocado en trazabilidad, reglas de negocio auditables y uso real en escritorio y mobile.

---

## Version Corta Para Portfolio

Desarrolle un sistema operativo-financiero para sucursales y administracion central orientado a reemplazar planillas manuales. La aplicacion cubre apertura y cierre de cajas, control de diferencias, egresos clasificados por rubro, traspasos auditados entre cajas, cuentas por pagar, pagos en efectivo o banco, control de acreditaciones, dashboard financiero consolidado y una vista economica por periodo y sucursal. El proyecto esta construido con Django, PostgreSQL y templates server-rendered, con foco en reglas de negocio, consistencia de datos y trazabilidad operativa.

---

## Descripcion Completa

### Que problema resuelve

En operaciones con varias sucursales, caja diaria, pagos, deudas, acreditaciones y control gerencial suelen quedar repartidos entre Excel, apuntes informales y validaciones manuales. Eso genera:

- poca trazabilidad
- cierres dificiles de reconstruir
- mezcla entre caja fisica, banco y ventas digitales
- deuda sin seguimiento consistente
- poca visibilidad del resultado economico real

Gerayse concentra ese flujo en una sola aplicacion y modela el proceso con datos estructurados, estados, restricciones y validaciones de dominio.

### Tipo de producto

- sistema interno de gestion operativa y financiera
- SaaS/Backoffice orientado a sucursales y administracion
- no es una app de contabilidad formal
- no depende de integraciones bancarias reales para su flujo base
- prioriza control interno y lectura operativa por sobre automatizacion externa

### Usuarios objetivo

- encargados de sucursal
- administracion
- tesoreria central
- dueños o direccion

### Casos de uso principales

- abrir turnos y cajas
- registrar ingresos operativos
- registrar egresos por rubro
- cerrar cajas con ajuste automatico o justificacion obligatoria
- mover saldo entre cajas con trazabilidad
- administrar sucursales y rubros
- cargar proveedores y cuentas por pagar
- registrar pagos en efectivo, transferencia, cheque y echeq
- registrar movimientos bancarios y acreditaciones
- ver disponibilidades y situacion financiera consolidada
- analizar rentabilidad y desvio economico por rubro, periodo y sucursal

---

## Stack Tecnico

### Backend

- Python
- Django 5.2
- Django ORM
- arquitectura monolitica modular

### Base de datos

- PostgreSQL como target principal
- SQLite disponible para entorno local del repo

### Frontend

- Django Templates
- HTMX en flujos puntuales
- HTML/CSS server-rendered
- enfoque mobile-first y operativo

### Infra y despliegue

- `gunicorn`
- `whitenoise`
- configuracion por entorno con `django-environ`

### Dependencias visibles en el repo

- `django==5.2.6`
- `django-environ==0.12.0`
- `psycopg[binary]==3.2.9`
- `whitenoise==6.9.0`
- `gunicorn`

---

## Arquitectura Del Proyecto

### Apps principales

- `cashops`
  - caja operativa
  - turnos
  - movimientos
  - cierres
  - alertas
  - sucursales
  - rubros y limites operativos

- `treasury`
  - proveedores
  - cuentas por pagar
  - pagos
  - caja central
  - movimientos bancarios
  - acreditaciones
  - snapshots financieros y economicos

- `users`
  - usuario custom
  - rol operativo
  - administracion simplificada de personal
  - usuario fijo / sucursal base

- `core`
  - shell base
  - home
  - navegacion general

### Decisiones de diseño relevantes

- user model custom (`users.User`)
- logica de negocio concentrada en servicios, no en formularios solamente
- snapshots de lectura para dashboards financieros y economicos
- validaciones de permisos y ownership a nivel dominio
- migraciones incrementales para endurecer reglas sin romper legacy

---

## Funcionalidad Implementada

### Caja operativa

- apertura de caja por usuario, turno y sucursal
- cierre con validacion de saldo esperado vs saldo fisico
- ajuste automatico para diferencias menores
- justificacion obligatoria y alerta para diferencias graves
- ingresos operativos y ventas por canal
- egresos clasificados por rubro
- traspasos auditados entre cajas
- arrastre/unificacion entre turnos o dias dentro de la misma sucursal
- bloqueo de transferencias entre sucursales nuevas

### Sucursales y operacion

- maestro de sucursales con codigo y razon social
- activacion/desactivacion controlada
- busqueda por codigo, nombre y razon social
- totales por sucursal y por rango de fechas
- saldo neto operativo visible

### Usuarios

- login con usuario custom
- roles operativos
- vista minima de personal
- legajo fuera del flujo operativo
- `usuario_fijo` y `sucursal_base`
- efecto real de esa asignacion en la apertura de caja

### Tesoreria

- alta y mantenimiento de proveedores
- cuentas por pagar con saldo pendiente derivado
- pagos por transferencia, efectivo, cheque y echeq
- movimientos de caja central para pagos en efectivo
- movimientos bancarios tipados
- acreditaciones diarias o agrupadas
- control de deuda vencida, por vencer y pendiente
- dashboard financiero consolidado por periodo y sucursal

### Lectura economica

- relacion entre ventas, gasto operativo y deuda del periodo
- imputacion economica por `periodo_referencia`
- mapeo de categoria de deuda a rubro operativo
- dashboard de rentabilidad por sucursal y periodo
- objetivos economicos por rubro sobre ventas
- comparacion objetivo vs real vs desvio

---

## Reglas De Negocio Importantes

- caja fisica, banco y ventas digitales no se mezclan conceptualmente
- una cuenta por pagar define el estado de deuda
- el estado pagada/parcial/pendiente se deriva de pagos validos registrados
- pagos en efectivo salen de caja central, no de una cuenta bancaria
- un egreso operativo requiere rubro
- no se permite arrastre entre cajas de distintas sucursales
- `usuario fijo` representa asignacion operativa preferida, no bloqueo absoluto para un admin
- deuda legacy sin rubro puede seguir existiendo, pero queda fuera de comparaciones economicas por objetivo

---

## Lo Mas Interesante Para Mostrar En Portfolio

### 1. Reemplazo real de Excel por modelo de dominio

El proyecto no copia planillas celda por celda: traduce procesos manuales a entidades, estados, relaciones y validaciones persistidas.

### 2. Separacion de lecturas financiera y economica

Se distingue entre:

- caja fisica
- bancos
- acreditaciones pendientes
- deuda operativa
- rentabilidad por rubro

Esa separacion evita doble conteo y hace que el dashboard sea defendible desde negocio.

### 3. Fuerte foco en trazabilidad

Cierres, pagos, movimientos y excepciones quedan auditables. El sistema privilegia reconstruccion operativa y explicabilidad.

### 4. UI operativa

La interfaz no esta pensada como CRM o ERP generico, sino como herramienta de trabajo diaria para personal operativo y administracion.

### 5. Endurecimiento progresivo del sistema

El repo muestra una evolucion clara:

- primero flujo base
- despues limites y alertas
- luego tesoreria y disponibilidades
- despues lectura financiera consolidada
- finalmente rentabilidad y objetivos por rubro

---

## Estado Actual Del Backlog

### Epicas implementadas

- `EP-01` caja operativa, movimientos y cierres
- `EP-02` alertas y semaforos operativos
- `EP-03` tesoreria central base
- `EP-04` bancos y conciliacion
- `EP-05` flujo de disponibilidades
- `EP-08` ajustes operativos de caja y sucursales
- `EP-09` usuarios operativos y datos minimos
- `EP-10` situacion financiera y alertas consolidadas

### Epicas iniciadas

- `EP-11` rentabilidad y situacion economica

### Epicas pendientes

- `EP-06` control de gestion y alertas
- `EP-07` impuestos, planes y autorizaciones

### Pendientes concretos hoy

- cerrar `US-11.5` y `US-11.6`
- limpiar imports y ruido de encoding en algunas vistas viejas de tesoreria
- decidir si el rubro pasa a ser obligatorio en todo el flujo economico legacy

---

## Calidad, Testing Y Seguridad De Cambios

El proyecto ya incluye evidencia de testing enfocada en:

- servicios de caja
- vistas operativas
- migraciones sensibles
- usuarios y admin
- servicios y vistas de tesoreria
- dashboards financieros y economicos

Ejemplos de chequeos usados en el repo:

- tests de dominio para pagos, cierres, arrastres y restricciones por sucursal
- tests de vista para dashboards y formularios
- `makemigrations --check`
- `compileall`

Eso es relevante para portfolio porque el proyecto no es solo UI: tiene una capa de reglas de negocio con cobertura.

---

## Como Correr El Proyecto

```bash
pip install -r requirements.txt
cp .env.example .env
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Configuracion visible:

- `LANGUAGE_CODE = "es-ar"`
- `TIME_ZONE = "America/Argentina/Buenos_Aires"`
- `AUTH_USER_MODEL = "users.User"`

Rutas principales:

- `/` home publica
- `/admin/` Django admin
- rutas operativas de usuarios y caja en raiz
- `/tesoreria/` modulo financiero

---

## Mi Aporte Tecnico Que Se Puede Destacar

Si queres presentar este proyecto como experiencia tuya, estos son buenos ejes para remarcar:

- modelado de dominio para operaciones de caja y tesoreria
- diseño de dashboards financieros y economicos basados en snapshots
- hardening de reglas operativas con migraciones y tests
- simplificacion de flujos operativos sin perder trazabilidad
- separación clara entre operatoria de sucursal y tesoreria central
- backlog funcional traducido a epicas y user stories implementables

---

## Texto Sugerido Para Portfolio

### Version breve

> Gerayse es un sistema de gestion operativa y financiera para sucursales, construido con Django y PostgreSQL para reemplazar planillas de Excel en caja diaria, tesoreria, pagos, bancos y control economico. Incluye reglas de negocio auditables, dashboards por periodo y una arquitectura monolitica modular orientada a procesos reales de administracion interna.

### Version media

> Desarrolle un sistema interno para centralizar caja operativa, cierres, tesoreria, cuentas por pagar, movimientos bancarios y dashboards de situacion financiera y rentabilidad. El foco estuvo en transformar flujos manuales en un modelo de dominio consistente, con validaciones, migraciones y tests que aseguran trazabilidad y evitan mezclar caja fisica, banco y deuda. El stack principal es Django, PostgreSQL, templates server-rendered y HTMX para interacciones puntuales.

---

## Contexto Listo Para Otro Agente

### Resumen estructurado

- nombre: `Gerayse`
- tipo: `sistema web interno de gestion operativa y financiera`
- stack: `Django, PostgreSQL, Django ORM, Django Templates, HTMX`
- arquitectura: `monolito modular`
- dominio: `caja diaria, cierres, tesoreria, proveedores, deudas, pagos, bancos, dashboards financieros y economicos`
- usuario final: `encargados, administracion, tesoreria, direccion`
- foco tecnico: `reglas de negocio, trazabilidad, reemplazo de Excel, reporting operativo`
- estado actual:
  - implementado: `EP-01, EP-02, EP-03, EP-04, EP-05, EP-08, EP-09, EP-10`
  - iniciado: `EP-11`
  - pendiente: `EP-06, EP-07`
- puntos diferenciales:
  - `servicios de dominio para reglas sensibles`
  - `dashboards por snapshot financiero/economico`
  - `validaciones fuertes en pagos y cierres`
  - `arrastres auditados entre cajas`
  - `objetivos economicos por rubro`

### Instruccion sugerida para otro agente

> Usa este proyecto como caso de estudio de software operativo-financiero hecho en Django. No lo presentes como ERP generalista ni como integracion bancaria full. El diferencial real esta en el modelado de caja y tesoreria, la trazabilidad de movimientos, la separacion entre lectura financiera y economica, y la traduccion de procesos manuales a reglas de negocio auditables.


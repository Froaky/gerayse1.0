# Backlog Funcional

Este directorio baja a backlog ejecutable el relevo operativo, los Excel historicos y el estado actual del sistema.

Objetivo:
- reemplazar los Excel operativos y financieros sin perder control ni trazabilidad
- separar caja operativa de tesoreria, bancos y control de gestion
- permitir operar varias empresas sin mezclar sucursales, caja, tesoreria ni reportes
- ordenar la implementacion por epicas y user stories concretas

Estado actual:
- `EP-01`: caja operativa, movimientos y cierres. Implementada.
- `EP-02`: alertas y semaforos operativos. Implementada.
- `EP-03`: tesoreria central base. Implementada.
- `EP-04`: bancos y conciliacion. Implementada.
- `EP-05`: flujo de disponibilidades. Implementada. Cerrada 2026-05-04 tras completar US-5.9 (formulario de egreso administrativo con rubro, sucursal y periodo).
- `EP-06`: control de gestion y alertas. Implementada.
- `EP-07`: impuestos, planes y autorizaciones. Implementada.
- `EP-08`: ajustes operativos de caja y sucursales. Implementada. Cerrada 2026-04-28 tras completar US-8.8 a US-8.13.
- `EP-09`: usuarios operativos y datos minimos. Implementada.
- `EP-10`: situacion financiera y alertas consolidadas. Implementada.
- `EP-11`: rentabilidad y situacion economica. Implementada.
- `EP-12`: empresas, contexto activo y navegacion. Implementada. Cerrada 2026-04-28.

Especialista sugerido por epica:
- `analista-funcional-backlog` para crear, partir o completar backlog en cualquier epic.
- `tesoreria-financiera-consolidada` para `EP-03`, `EP-04`, `EP-05` y `EP-10`.
- `control-gestion-rentabilidad` para `EP-06`, `EP-07` y `EP-11`.
- `caja-sucursales-operativa` para `EP-08`.
- `usuarios-operativos-admin` para `EP-09`.
- `analista-funcional-backlog` para `EP-12`, coordinando con `caja-sucursales-operativa`, `tesoreria-financiera-consolidada` y `usuarios-operativos-admin` por el alcance transversal.
- `testing-riguroso-extremo` para cerrar historias con dinero, permisos, migraciones o reglas operativas sensibles.

Archivos de referencia de negocio:
- [matriz-sistema-vs-relevo.md](/C:/code/gerayse1.0/docs/epics/matriz-sistema-vs-relevo.md)
- Excel de matriz diaria, transferencias/deudas y flujo de disponibilidades
- relevo de procesos del encargado

Orden recomendado de implementacion:
1. [EP-03-tesoreria-central.md](/C:/code/gerayse1.0/docs/epics/EP-03-tesoreria-central.md)
2. [EP-04-bancos-y-conciliacion.md](/C:/code/gerayse1.0/docs/epics/EP-04-bancos-y-conciliacion.md)
3. [EP-05-flujo-de-disponibilidades.md](/C:/code/gerayse1.0/docs/epics/EP-05-flujo-de-disponibilidades.md)
4. [EP-06-control-de-gestion-y-alertas.md](/C:/code/gerayse1.0/docs/epics/EP-06-control-de-gestion-y-alertas.md)
5. [EP-07-impuestos-planes-y-autorizaciones.md](/C:/code/gerayse1.0/docs/epics/EP-07-impuestos-planes-y-autorizaciones.md)
6. [EP-08-ajustes-operativos-de-caja-y-sucursales.md](/C:/code/gerayse1.0/docs/epics/EP-08-ajustes-operativos-de-caja-y-sucursales.md)
7. [EP-09-usuarios-operativos-y-datos-minimos.md](/C:/code/gerayse1.0/docs/epics/EP-09-usuarios-operativos-y-datos-minimos.md)
8. [EP-10-situacion-financiera-y-alertas-consolidadas.md](/C:/code/gerayse1.0/docs/epics/EP-10-situacion-financiera-y-alertas-consolidadas.md)
9. [EP-11-rentabilidad-y-situacion-economica.md](/C:/code/gerayse1.0/docs/epics/EP-11-rentabilidad-y-situacion-economica.md)
10. [EP-12-empresas-contexto-y-navegacion.md](/C:/Users/MateoCoca/Documents/REPOS/gerayse/docs/epics/EP-12-empresas-contexto-y-navegacion.md)

Principios de implementacion:
- no copiar el Excel celda por celda; replicar el resultado funcional con datos estructurados
- no mezclar caja fisica con banco ni con ventas a acreditar
- no permitir pagos sin comprobante, referencia o trazabilidad minima
- no depender de campos libres para estados como `pagado`, `ok`, `NO`
- la conciliacion bancaria se considera manual asistida por el sistema; no automatizar matching ni importaciones hasta decision explicita del usuario
- toda excepcion debe quedar auditada
- toda vista con contexto de empresa debe filtrar y validar datos contra la empresa activa
- el sistema debe reconstruir cierres, deuda, pagos, flujo y dashboard sin retoques manuales

Criterio de cierre global:
- un mes completo de operacion debe poder reconstruirse desde la base sin editar planillas
- debe poder explicarse:
  - que se vendio por canal
  - que entro en efectivo
  - que entro al banco
  - que se debia
  - que se pago
  - que quedo pendiente
  - que diferencia hubo y por que

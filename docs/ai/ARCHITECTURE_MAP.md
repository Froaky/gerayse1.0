# Mapa de arquitectura de Gerayse

Este mapa ayuda a ubicar cambios sin romper separacion de responsabilidades.

## Vista general

Gerayse es un monolito Django dividido por dominios operativos:

```text
config/       configuracion Django
core/         shell, home, contexto global
users/        usuario custom, roles, permisos y contexto empresa/sucursal
cashops/      caja operativa diaria de sucursales
treasury/     tesoreria, banco, caja central, deuda, pagos, disponibilidad
reports/docs  epicas, manuales y documentacion funcional
```

## Apps y ownership

### `users`

Dueño de:

- autenticacion;
- usuario custom;
- roles;
- permisos administrativos;
- contexto de empresas permitidas;
- preferencias basicas de cuenta.

No debe decidir saldos, deuda ni reglas de caja. Solo habilita o bloquea acceso.

### `cashops`

Dueño de:

- sucursales y empresas usadas por operacion;
- turnos;
- cajas operativas;
- movimientos operativos;
- cierres de caja;
- traspasos operativos;
- alertas de caja.

Debe mantener caja operativa separada de banco y tesoreria. Cuando un cierre impacta caja central, hacerlo mediante servicio y con traza.

### `treasury`

Dueño de:

- proveedores;
- cuentas por pagar;
- pagos;
- cuentas bancarias;
- movimientos bancarios;
- acreditaciones de tarjeta/POS;
- caja central;
- disponibilidades;
- snapshots financieros/economicos.

Debe ser la fuente de verdad para deuda y pagos. No debe leer valores editados manualmente en dashboards si pueden derivarse de movimientos persistidos.

### `core`

Dueño de:

- landing/dashboard shell;
- contexto transversal de navegacion;
- context processors.

No debe contener reglas de dinero ni permisos finos.

## Capas esperadas por app

```text
models.py       entidades, relaciones, constraints, invariantes
permissions.py  autorizacion reutilizable
services.py     casos de uso, transacciones, formulas canonicas
forms.py        validacion de entrada y adaptacion a servicios
views.py        request/response, permisos, mensajes, render
templates/      presentacion
admin.py        administracion tecnica
urls.py         routing
migrations/     evolucion de esquema
```

## Fuentes de verdad criticas

| Concepto | Fuente de verdad |
| --- | --- |
| Estado de deuda | `CuentaPorPagar` + pagos no anulados |
| Pago registrado | `PagoTesoreria` |
| Caja operativa | `Caja` + `MovimientoCaja` |
| Cierre operativo | Servicio de cierre en `cashops.services` |
| Caja central | `MovimientoCajaCentral` |
| Banco | `MovimientoBancario` |
| Acreditaciones tarjeta | `AcreditacionTarjeta` + `LotePOS` |
| Disponibilidad consolidada | snapshot derivado, no editable a mano |
| Situacion economica | snapshot economico derivado por periodo |
| Permisos de usuario | `users` + `permissions.py` |
| Contexto empresa/sucursal | `users` + context processor + filtros en servicios/vistas |

## Reglas de dependencia

Permitido:

- `views` llama `forms`, `permissions` y `services`.
- `forms` valida input y puede recibir querysets filtrados.
- `services` usa modelos y helpers internos.
- `templates` renderizan datos ya calculados.

Evitar:

- `templates` calculando saldos o formulas.
- `views` creando movimientos financieros a mano.
- `forms` decidiendo estados de deuda o cierres.
- `models.save()` ejecutando procesos multipaso.
- dependencias circulares entre `cashops` y `treasury` sin servicio claro.

## Patrones recomendados

### Caso de uso con dinero

1. Vista valida permisos y form.
2. Form limpia datos de entrada.
3. Vista llama servicio.
4. Servicio abre transaccion.
5. Servicio bloquea registros sensibles si hace falta.
6. Servicio crea/anula/reversa movimientos.
7. Servicio recalcula estado derivado si aplica.
8. Vista muestra resultado.

### Dashboard o snapshot

1. Definir periodo y scope de empresa/sucursal.
2. Elegir fuentes persistidas.
3. Excluir anulados/inactivos segun regla canonica.
4. Calcular en servicio/helper unico.
5. Pasar componentes explicables al template.
6. Testear componentes, no solo HTML.

### UI operativa

1. No cambiar reglas de negocio en template.
2. Boton visible debe tener permiso backend.
3. Accion peligrosa debe tener confirmacion.
4. Filtro visible debe filtrar en query real.
5. Mensajes deben usar lenguaje del negocio.

## Señales de mal diseño

- Un mismo total se calcula distinto en dos vistas.
- Un boton se oculta pero la URL sigue permitida.
- Un movimiento se borra en vez de anularse.
- Un formulario ofrece sucursales de otra empresa.
- Un fix toca diez templates sin necesidad.
- Un `if` nuevo copia logica ya existente.
- Un dashboard suma registros anulados.
- Una migracion vuelve obligatorio un campo sin backfill.

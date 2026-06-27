# Contrato para convertir prompts informales en implementacion precisa

Este documento sirve para que un agente no interprete pedidos vagos como permiso para tocar cualquier cosa.

## Principio

El usuario puede pedir en lenguaje informal. El agente debe responder con criterio de producto, dominio y seguridad de datos.

Un prompt informal debe transformarse en una instruccion implementable con:

- objetivo observable;
- alcance cerrado;
- fuente de verdad;
- opcion optima;
- riesgos;
- tests.

## Plantilla corta para usar en cada tarea

```md
## Interpretacion del pedido

Pedido literal:
Resultado esperado:
Tipo de cambio:
Dominio:
Fuente de verdad:
Opcion recomendada:
Clasificacion: OPTIMA / ACEPTABLE / NO OPTIMA
Alcance incluido:
Alcance excluido:
Riesgo principal:
Criterios de aceptacion:
Tests:
```

## Diccionario de interpretacion

### "Arregla el dashboard"

No significa tocar HTML primero.

Interpretar como:

1. identificar KPI incorrecto;
2. ubicar servicio/snapshot que lo calcula;
3. confirmar periodo, sucursal y empresa;
4. revisar exclusion de anulados/inactivos;
5. testear formula;
6. tocar template solo si el dato correcto ya llega mal presentado.

### "Agrega un boton"

No significa solo agregar `<a>` en template.

Interpretar como:

1. definir accion real;
2. validar permiso backend;
3. agregar URL/vista si no existe;
4. confirmar metodo seguro (`POST` para mutaciones);
5. agregar confirmacion si es destructiva;
6. testear acceso permitido y denegado.

### "Que se pueda eliminar"

En datos financieros/operativos casi nunca significa delete fisico.

Interpretar como:

1. anulacion auditada;
2. reversa si ya impacto saldos;
3. motivo obligatorio cuando aplique;
4. excluir anulados de lecturas;
5. mantener traza del registro original.

### "No se mezcle"

Interpretar como problema de scope.

Revisar:

- empresa;
- sucursal;
- periodo;
- estado;
- tipo de movimiento;
- fuente de verdad;
- permisos.

### "Hacelo mas prolijo"

No significa refactor global.

Pedir o inferir si se refiere a:

- UI/copy;
- orden de codigo;
- arquitectura;
- nombres;
- extraccion de servicio;
- tests.

Si no hay riesgo de datos, aplicar mejora chica. Si hay riesgo, proponer slice.

### "Esto esta mal"

Interpretar como bug.

1. reproducir mentalmente o con test;
2. encontrar causa raiz;
3. agregar test que fallaria sin fix;
4. corregir minimo;
5. validar regresion.

## Reglas para decidir fuente de verdad

| Si el pedido habla de... | Buscar primero en... |
| --- | --- |
| saldo de caja | `cashops.services`, `Caja`, `MovimientoCaja` |
| cierre de caja | servicio de cierre en `cashops.services` |
| caja fuerte/general/central | `treasury.services`, `MovimientoCajaCentral` |
| deuda/proveedores | `CuentaPorPagar`, `PagoTesoreria`, `treasury.services` |
| banco | `MovimientoBancario`, `CuentaBancaria`, servicios bancarios |
| acreditaciones | `AcreditacionTarjeta`, `LotePOS` |
| resultado economico | snapshot economico en `treasury.services` |
| permisos | `users`, `permissions.py`, decorators/mixins de vistas |
| empresas/sucursales | contexto de usuario + filtros en querysets |
| interfaz | templates + CSS, sin cambiar reglas de negocio |

## Como informar si la opcion no es optima

Formato:

```md
La opcion rapida seria X, pero no es optima porque Y.
La opcion optima es Z porque centraliza la regla en A, evita B y permite testear C.
Propongo implementar solo el slice Z1 ahora y dejar Z2 como pendiente.
```

## Criterios de aceptacion bien escritos

Mal:

- "Que ande".
- "Que se vea bien".
- "Que calcule correcto".

Bien:

- "El snapshot economico excluye movimientos anulados y canales marcados `excluir_de_totales`."
- "Un usuario sin permiso no ve el boton y tampoco puede acceder por URL directa."
- "Anular una caja cerrada crea una reversa en caja central y no elimina el movimiento original."
- "El filtro de sucursal no muestra cuentas de otra empresa en form, listado ni dashboard."

## Checklist antes de tocar codigo

- [ ] Lei `context.md`.
- [ ] Lei `docs/engineering-guidelines.md`.
- [ ] Lei la epica o skill relevante.
- [ ] Ubique fuente de verdad.
- [ ] Defini alcance incluido/excluido.
- [ ] Clasifique la opcion como optima/aceptable/no optima.
- [ ] Identifique tests necesarios.
- [ ] Evite refactor colateral.

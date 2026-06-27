# Matriz de testing y seguridad

Usar esta matriz para decidir pruebas minimas antes de cerrar un cambio.

## Regla base

A mayor riesgo de datos, menos aceptable es validar solo por pantalla.

## Matriz por tipo de cambio

| Cambio | Tests minimos | Riesgo a cubrir |
| --- | --- | --- |
| Copy, labels, textos | test simple si hay cobertura existente o revision manual documentada | no romper template ni ruta |
| CSS/layout | render de vista critica si existe test, revision visual/manual documentada | no ocultar acciones ni romper responsive |
| Template con botones/acciones | test de vista | boton visible segun permiso, URL protegida |
| Formulario | test de form + vista si hay POST | validacion, querysets filtrados, mensajes |
| Permisos | test de vista/servicio por rol | acceso directo bloqueado, no solo boton oculto |
| Servicio con dinero | test de servicio obligatorio | montos Decimal, estados, efectos secundarios |
| Cierre/anulacion/reversa | test de servicio + vista si hay accion UI | trazabilidad, no duplicar saldo, idempotencia |
| Dashboard/snapshot | test de servicio con componentes | formula, filtros, anulados, periodo |
| Empresa/sucursal | test cross-company | no fuga por listado, form ni URL |
| Migracion | test de migracion o caso legacy | backfill, compatibilidad, constraints |
| Management command | test de dry-run y apply | no mutar sin confirmacion, resumen correcto |
| Concurrencia | `TransactionTestCase` si aplica | doble pago/cierre/anulacion |

## Tests por app

### `cashops`

Usar cuando toca caja operativa:

```bash
python manage.py test cashops.tests.CashopsServiceTests -v 2
python manage.py test cashops.tests.CashopsViewTests -v 2
python manage.py test cashops.tests_commands -v 2
```

Cubrir:

- apertura/cierre;
- saldo esperado;
- movimientos anulados;
- traspasos;
- rubros obligatorios;
- empresa/sucursal;
- alertas.

### `treasury`

Usar cuando toca deuda, pagos, banco, caja central o dashboards:

```bash
python manage.py test treasury.tests.TreasuryServiceTests -v 2
python manage.py test treasury.tests.TreasuryViewTests -v 2
python manage.py test treasury.tests.TreasuryConcurrencyTests -v 2
```

Cubrir:

- `CuentaPorPagar` como fuente de deuda;
- pagos no anulados;
- movimientos bancarios;
- caja central;
- disponibilidades;
- snapshot financiero/economico;
- filtros por sucursal/empresa;
- registros anulados.

### `users`

Usar cuando toca usuarios, roles o permisos:

```bash
python manage.py test users.tests -v 1
```

Cubrir:

- login;
- roles;
- permisos;
- cambio de password;
- empresas permitidas;
- formularios de usuario.

### `core`

Usar cuando toca shell/contexto global:

```bash
python manage.py test core.tests -v 1
```

Cubrir:

- context processor;
- navegacion;
- scope global seleccionado.

## Comandos de cierre

Cuando corresponda:

```bash
python manage.py makemigrations --check --dry-run
python -m compileall cashops treasury users core
```

En entorno Windows del proyecto:

```powershell
$env:PYTHONPATH=".venv\Lib\site-packages"
py -3.13 manage.py makemigrations --check --dry-run
py -3.13 -m compileall cashops treasury users core
```

## Como informar validacion

Formato recomendado:

```md
Validacion:
- Test focalizado: OK / fallo / no ejecutado
- Regresion modulo: OK / fallo / no ejecutado
- Makemigrations check: OK / no aplica / fallo
- Compileall: OK / no aplica / fallo
- Riesgo pendiente:
```

No decir "validado" si no se corrio nada.

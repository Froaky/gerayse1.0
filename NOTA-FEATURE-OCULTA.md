# NOTA — Funcionalidad pendiente de activar

> Para el proximo agente / la proxima sesion (incluso desde otra PC o otro chat):
> leer esto ANTES de tocar ramas, y NO forzar `git branch -f staging main`.

Ultima actualizacion: 2026-07-29

---

## Que esta pendiente de activar

**Vincular transferencias bancarias a deudas** (pagar una deuda desde el
movimiento bancario ya cargado: elegir proveedor -> ver sus facturas impagas ->
vincular, y que eso descuente la deuda).

**Estado:** NO implementado todavia. Queda fuera del alcance actual porque el
cliente todavia no acepto el presupuesto de ESA funcionalidad puntual. Todo el
resto del pedido (ver mas abajo) SI esta hecho y vive en `main`.

---

## Como esta organizado (IMPORTANTE)

| Rama | Contiene |
|---|---|
| `main` | TODO lo aprobado y funcionando (produccion). |
| `staging` | Lo mismo que `main` **+ la funcionalidad de vincular transferencias**, cuando se construya. |

**Regla de oro del flujo:** mientras exista trabajo solo-en-staging, **NO** usar
`git branch -f staging main` (era el flujo historico del repo). Eso BORRARIA el
commit de la funcionalidad oculta. En su lugar, cuando `main` avance:

```bash
git checkout staging
git merge main        # staging queda = main + la feature
git push origin staging
git checkout main
```

## Como ACTIVARLO cuando el cliente acepte

```bash
git checkout main
git merge staging     # trae la feature a main
git push origin main
```

Railway deploya `main` -> queda activo. Si la feature trae migraciones, se
aplican solas con el `migrate` del startCommand.

Al activarlo, borrar o vaciar este archivo (o dejar solo el historial).

---

## Decisiones ya tomadas sobre esa funcionalidad (para no re-investigar)

Investigado a fondo (mapeo completo del subsistema de bancos y pagos). Lo que hay
que saber antes de escribir una linea:

1. **"Vincular" significa PAGAR.** No se puede "restar de la deuda" a mano:
   `_recalculate_payable_locked` (treasury/services.py) recalcula el saldo como
   `importe_total - suma de pagos REGISTRADO` y pisa cualquier valor escrito
   directamente. O sea: hay que crear un `PagoTesoreria` real.

2. **El movimiento deja de ser "manual" al vincularse.** Pasa a
   `origen=PAGO_TESORERIA`, y eso es IMPRESCINDIBLE: los debitos con origen
   MANUAL cuentan como gasto economico por si mismos, y la deuda YA conto ese
   gasto al cargarse. Si quedara MANUAL, el mismo dinero se contaria DOS VECES.
   Ya esta confirmado con el cliente que este cambio de comportamiento es
   aceptable (queda como "pago de la deuda X" en lugar de gasto suelto).

3. **BLOQUEANTE de esquema para el caso "una transferencia paga varias
   facturas":** `MovimientoBancario.pago_tesoreria` es un `OneToOneField`
   (treasury/models.py ~1034) y `link_payment_to_bank_movement` exige
   `payment.monto == bank_movement.monto` (services.py ~1250). Por lo tanto HOY:
   - transferencia -> 1 factura con monto exacto: se puede;
   - transferencia -> varias facturas: **imposible sin migracion** (hay que
     relajar el OneToOne a FK);
   - pago parcial (monto del movimiento != monto del pago): **imposible** hoy.
   Esto se le informo al cliente como item aparte del presupuesto.

4. **Punto exacto de enganche en la UI:** `treasury/views.py`,
   `bank_movements_detail`, la lista `actions` (el boton "Vincular a pago" ya
   existe ahi, condicionado a REGISTRADO + sin pago + DEBITO). Agregar el boton
   nuevo ahi deja la UI byte-identica mientras no se agregue: el template
   `detail_page.html` solo itera `actions`, no hay otra superficie.

5. **Si se prefiere ocultarlo por flag en vez de por rama** (alternativa ya
   evaluada): el repo tiene el patron probado `ENABLE_DANGER_RESET`
   (`config/settings.py`, con `env.bool(..., default=...)`; consumido con
   `if not settings.X: raise Http404(...)`; testeado con `@override_settings`).
   Para este caso el default DEBE ser `False` literal (no `DEBUG`), porque el CI
   corre con `DEBUG=True` y quedaria encendido.

6. **Toda vista nueva** debe sumarse a `TREASURY_WRITE_VIEW_NAMES`
   (treasury/views.py) o el GET pasa con permiso de solo lectura.

7. **Riesgo de la estrategia de ramas:** si la feature en staging trae una
   migracion y despues se crea otra migracion en main, puede haber choque de
   numeracion. Al mergear, verificar con
   `python manage.py makemigrations --check --dry-run`.

---

## Lo que YA esta hecho y vive en main (para no rehacerlo)

Del pedido de la administracion:

- **Efectivo validado al mes correcto:** no se puede cerrar el mes si hay cajas
  abiertas o pendientes de validar, y no se pueden abrir (ni mover) cajas con
  fecha de un mes ya cerrado. Valvula: `CierreMensualTesoreria` esta en el admin
  de Django para poder destildar "cerrado" en una emergencia.
- **Pago por proveedor:** pantalla nueva (boton "Pagar por proveedor" en Pagos)
  que lista las facturas impagas del proveedor elegido y permite tildar 1 o
  varias, con importe editable por linea. Registra UN pago por factura.
- **Desglose de deuda:** comando de solo lectura
  `python manage.py desglose_deuda` (para diagnosticar totales).
- **Permiso "Eliminar movimientos de caja"** (`cashops_mov_del`): anular con
  auditoria movimientos y gastos-como-deuda, en cajas abiertas y cerradas.
- **Arreglos:** fuga de empresa en los desplegables de pago; anular un pago ya no
  deja el movimiento bancario colgado; el historial por proveedor no cuenta las
  anuladas; etiquetas de las tarjetas de deuda del dashboard aclaradas
  (acumulada vs del periodo); eliminado codigo muerto con fuga de empresa.

Detalle tecnico completo de cada uno: ver `context.md`.

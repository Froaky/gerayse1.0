# Notas para puesta en produccion

## Boton "Reiniciar datos" — ahora gateado por entorno (no requiere borrar codigo)

El reinicio destructivo de datos operativos (`cashops:reset_operational_data`) borra TODO
el estado operativo/financiero (cajas, movimientos, cierres, cuentas por pagar, efectivo
central, etc.) sin scope de empresa. Es una herramienta de testing.

Desde 2026-07-03 esta protegido por el setting `ENABLE_DANGER_RESET`:

- Por defecto sigue a `DEBUG` (`ENABLE_DANGER_RESET = env.bool("ENABLE_DANGER_RESET", default=DEBUG)`).
- En produccion, con `DEBUG=False`, la vista responde **404** y el boton no se renderiza
  en ningun template (menu Config, Empresas, Disponibilidades).
- Ya **no** hace falta borrar codigo a mano antes del deploy.

### Como habilitarlo puntualmente en un entorno controlado

Setear la variable de entorno `ENABLE_DANGER_RESET=True`. Volver a `False` (o quitarla)
para dejarlo inaccesible.

### Verificacion en produccion

Confirmar en el entorno productivo que:

- `DEBUG=False`.
- `ENABLE_DANGER_RESET` NO esta seteada en `True`.
- `GET /config/reiniciar/` responde 404.

---

_Nota actualizada el 2026-07-03: el boton dejo de depender de una eliminacion manual de codigo
y pasa a estar controlado por entorno (`ENABLE_DANGER_RESET`)._

---

## Aviso de vencimiento del servicio (cartel al administrador)

El alojamiento vence el **9 de cada mes**. Del 2 al 9 aparece arriba de todas las
pantallas un cartel que informa la fecha y pide regularizar el saldo del mes:

| Dias | Tono | Ejemplo |
|---|---|---|
| del 2 al 5 (faltan 7 a 4) | ambar | "El servicio de alojamiento de Gerayse vence el 9 de septiembre de 2026. Quedan 6 dias. Para evitar la interrupcion del sistema, regularice el saldo del mes antes de esa fecha." |
| del 6 al 9 (faltan 3 a 0) | rojo | mismo texto; el dia 9 dice "vence hoy, 9 de septiembre de 2026". |
| del 10 al 1 | nada | No se afirma que "vencio": no sabemos si el saldo se regularizo. |

Lo ve SOLO el administrador (superusuario o rol `ADMIN`/`ADMINISTRADOR`): cajeros y
tesoreria no lo ven. Trato de usted, sin nombres propios. Habla del "servicio de
alojamiento", nunca de "la base de datos": es un aviso de continuidad del servicio, no
una amenaza sobre los datos del cliente.

**No hay nada que configurar.** Se activa solo, todos los meses, con el deploy. La
variable `SERVICE_NOTICE_DUE_DAY` (default `9`) existe unicamente para dos casos:

- apagarlo (por ejemplo si pagan varios meses por adelantado): `SERVICE_NOTICE_DUE_DAY=0`;
- correr el dia si cambia el vencimiento: `SERVICE_NOTICE_DUE_DAY=15`.

Railway reinicia el servicio al cambiar variables; no hace falta deploy. Un dia que el
mes no tiene (31 en febrero) se corre al ultimo dia del mes.

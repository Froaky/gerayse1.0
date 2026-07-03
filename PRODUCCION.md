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

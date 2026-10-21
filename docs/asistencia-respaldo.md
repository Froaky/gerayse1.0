# Respaldo diario de asistencia (EP-14 US-14.7)

Procedimiento para el chat de servidor (Railway). El chat de desarrollo deja los comandos y los
tests; no toca Railway. Este repo es publico: nombres de bucket, claves y URLs de aviso van solo
en las variables de Railway, nunca aca.

## Que se respalda

Empleados, carnets anulados y marcas (todo lo de la app `asistencia`), en un JSON comprimido
`asistencia-AAAAMMDD-HHMMSS.json.gz`. Las empresas, locales y usuarios a los que apuntan no van:
son de la base principal y se respaldan con ella.

## Variables

| Variable | Valor |
| --- | --- |
| `RESPALDO_DESTINO` | `s3://<bucket>/<prefijo>` (almacenamiento compatible con S3) o una carpeta |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | credenciales con permiso de subir, listar, bajar y borrar en ese prefijo |
| `AWS_DEFAULT_REGION` | la region del bucket (`auto` en Cloudflare R2) |
| `AWS_ENDPOINT_URL` | solo si no es Amazon S3 (por ejemplo R2 o Backblaze B2) |
| `RESPALDO_AVISO_URL` | webhook que recibe un POST JSON `{"text": "..."}` cuando falla (Slack, Discord con `/slack`, etc.) |

El almacenamiento tiene que estar fuera de Railway: la idea es no perder nada si se cae el servidor
o la base.

## Cron

Una vez por dia, de madrugada (por ejemplo 03:30 hora Argentina = 06:30 UTC), con el mismo
codigo y variables que el servicio web:

```
python manage.py respaldar_asistencia
```

- deja los ultimos 30 respaldos y borra los mas viejos (solo archivos `asistencia-*.json.gz`);
- si falla, termina con error (codigo distinto de 0) y manda el aviso a `RESPALDO_AVISO_URL`.

## Restaurar

```
python manage.py restaurar_asistencia                          # lista los respaldos
python manage.py restaurar_asistencia asistencia-20261021-033000.json.gz
python manage.py restaurar_asistencia ruta/al/archivo.json.gz  # un archivo bajado a mano
```

- si ya hay datos de asistencia no hace nada; para pisarlos agregar `--reemplazar` (borra los
  actuales y carga los del respaldo, todo en una transaccion);
- las empresas, locales y usuarios del respaldo tienen que existir en la base;
- despues de restaurar se puede seguir fichando: los ids siguen desde el ultimo restaurado.

## Prueba antes de dar por cerrada la US-14.7

1. Configurar las variables en staging y correr `respaldar_asistencia` a mano: tiene que aparecer
   el archivo en el bucket.
2. Restaurarlo en una base de prueba (o en staging con `--reemplazar`) y comparar la cantidad de
   empleados, carnets anulados y marcas.
3. Forzar una falla (por ejemplo un bucket inexistente) y confirmar que llega el aviso.

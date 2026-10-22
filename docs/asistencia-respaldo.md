# Respaldo diario de asistencia (EP-14 US-14.7)

Procedimiento para el chat de servidor (Railway). El chat de desarrollo deja los comandos y los
tests; no toca Railway. Este repo es publico: nombres de bucket, claves y URLs de aviso van solo
en las variables de Railway, nunca aca.

## Que se respalda

Empleados, carnets anulados y marcas (todo lo de la app `asistencia`), en un JSON comprimido
`asistencia-<entorno>-AAAAMMDD-HHMMSS.json.gz`. El entorno sale de `RAILWAY_ENVIRONMENT_NAME`
(Railway la pone sola): asi los respaldos de staging y de produccion no se mezclan ni se borran
entre si aunque compartan el bucket. Igual conviene un prefijo distinto por entorno.

Las empresas, locales y usuarios a los que apuntan no van: son de la base principal y se respaldan
con ella. Para restaurar asistencia, la base principal tiene que tener esas empresas y locales.

## Variables

| Variable | Valor |
| --- | --- |
| `RESPALDO_DESTINO` | `s3://<bucket>/<prefijo-del-entorno>` (compatible con S3) o una carpeta con ruta absoluta. Cualquier otra cosa da error: un valor mal escrito no puede terminar en el disco del contenedor. |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | credenciales con permiso de subir, listar, bajar y borrar en ese prefijo |
| `AWS_DEFAULT_REGION` | la region del bucket (`auto` en Cloudflare R2) |
| `AWS_ENDPOINT_URL` | solo si no es Amazon S3 (por ejemplo R2 o Backblaze B2) |
| `AWS_REQUEST_CHECKSUM_CALCULATION`, `AWS_RESPONSE_CHECKSUM_VALIDATION` | `when_required` si el proveedor no acepta los checksums que boto3 manda por defecto (algunos compatibles con S3 los rechazan) |
| `RESPALDO_AVISO_URL` | webhook que recibe un POST JSON `{"text": "..."}` cuando falla (Slack, Discord con `/slack`, etc.). Sin esta variable el comando lo advierte en cada corrida. |
| `RESPALDO_OK_URL` | opcional pero recomendado: URL de un monitor de "latido" (por ejemplo Healthchecks.io o Better Stack) que recibe un GET cada vez que sale bien y alerta si un dia no llega. Cubre lo que el aviso de falla no ve: el cron que no corrio o el proceso que se murio. |

El almacenamiento tiene que estar fuera de Railway: la idea es no perder nada si se cae el servidor
o la base.

## Cron

Una vez por dia, de madrugada (por ejemplo 03:30 hora Argentina = 06:30 UTC), con el mismo
codigo y variables que el servicio web:

```
python manage.py respaldar_asistencia
```

- imprime donde quedo el respaldo (`s3://bucket/prefijo/asistencia-...`);
- borra los respaldos de este entorno con mas de 30 dias, pero deja siempre los ultimos 30 (si el
  cron estuvo parado no se queda sin respaldos); no toca otros archivos ni otros entornos;
- si falla, termina con error (codigo distinto de 0) y manda el aviso a `RESPALDO_AVISO_URL`; si
  el respaldo se subio pero fallo la limpieza, el aviso lo dice asi.

## Restaurar

```
python manage.py restaurar_asistencia                          # lista los respaldos de este entorno
python manage.py restaurar_asistencia asistencia-production-20261021-033000.json.gz
python manage.py restaurar_asistencia ruta/al/archivo.json.gz  # un archivo bajado a mano
```

- si ya hay datos de asistencia no hace nada; para pisarlos agregar `--reemplazar` (borra los
  actuales y carga los del respaldo, todo en una transaccion: si algo falla no cambia nada);
- antes de cargar revisa que existan las empresas y locales del respaldo; si falta alguno, no
  restaura nada y dice cuales faltan (primero hay que restaurar la base principal);
- un usuario que ya no existe (por ejemplo un cajero borrado) queda vacio en esas marcas, igual que
  en la base viva, y el comando dice cuantas referencias quedaron asi;
- despues de restaurar se puede seguir fichando: los ids siguen desde el ultimo restaurado;
- con unas 60.000 marcas (un ano) tarda alrededor de un minuto.

## Prueba antes de dar por cerrada la US-14.7

1. Configurar las variables en staging y correr `respaldar_asistencia` a mano: tiene que aparecer
   el archivo en el bucket con `-staging-` en el nombre.
2. Restaurarlo en staging con `--reemplazar` y comparar la cantidad de empleados, carnets anulados
   y marcas.
3. Forzar una falla (por ejemplo un bucket inexistente) y confirmar que llega el aviso.
4. Si se configura `RESPALDO_OK_URL`, confirmar que el monitor recibe el latido.
5. Repetir 1 en produccion con su propio prefijo y dejar el cron andando.

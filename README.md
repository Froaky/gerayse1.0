# Gerayse

Sistema web para gestion de cajas, turnos y movimientos financieros pensado para operacion real en celular y escritorio.

## Stack

- Django
- PostgreSQL
- Django ORM
- Django Templates + HTMX

## MVP EP-01

- Apertura de cajas multiples por turno
- Traspaso entre cajas
- Carga rapida de gastos
- Registro de ventas por tarjeta
- Cierre con ajuste automatico si la diferencia es menor a 10.000
- Justificacion obligatoria y alerta para diferencias graves
- Transferencias entre sucursales de dinero o mercaderia

## Arranque local

1. Crear un entorno virtual e instalar dependencias con `pip install -r requirements.txt`
2. Copiar `.env.example` a `.env` y ajustar `DATABASE_URL`
3. Ejecutar `python manage.py makemigrations`
4. Ejecutar `python manage.py migrate`
5. Crear superusuario con `python manage.py createsuperuser`
6. Levantar el servidor con `python manage.py runserver`


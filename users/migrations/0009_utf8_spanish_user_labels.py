from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0008_user_empresa_principal_y_permitidas"),
    ]

    operations = [
        migrations.AlterField(
            model_name="rolepermission",
            name="module",
            field=models.CharField(
                choices=[
                    ("cashops", "Caja operativa"),
                    ("config", "Configuración"),
                    ("treasury", "Tesorería"),
                    ("users", "Usuarios"),
                ],
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="userpermission",
            name="module",
            field=models.CharField(
                choices=[
                    ("cashops", "Caja operativa"),
                    ("config", "Configuración"),
                    ("treasury", "Tesorería"),
                    ("users", "Usuarios"),
                ],
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="user",
            name="must_change_password",
            field=models.BooleanField(default=False, verbose_name="Debe cambiar contraseña"),
        ),
    ]

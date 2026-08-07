"""Modo claro / modo oscuro: garantias que no se ven mirando una pantalla.

El tema es una capa de presentacion, asi que no hay reglas de negocio que
testear. Lo que si se puede romper en silencio, y por eso se fija aca:

1. Que las TRES shells (base, cashops, treasury) traigan el arranque de tema.
   Si una se queda afuera, el usuario cambia a claro, navega de caja a
   tesoreria y esa pantalla vuelve a oscuro sin explicacion.
2. Que los dos temas definan exactamente el mismo set de tokens. Agregar un
   color al oscuro y olvidarse del claro no rompe nada visible en el momento:
   el token simplemente hereda el valor del otro tema y queda un gris raro en
   una sola pantalla, meses despues.
3. Que no vuelvan a aparecer colores hardcodeados en las hojas del sistema.
   Un `#0e1315` suelto no lo alcanza ningun tema.
"""
import re
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from cashops.models import Empresa
from users.models import Role

User = get_user_model()

BASE_DIR = Path(settings.BASE_DIR)
TOKENS = BASE_DIR / "templates" / "partials" / "theme_tokens.html"

# Las tres hojas de estilo del sistema. Cualquier color de estas tiene que
# venir de un token, porque son las que se pintan en los dos temas.
HOJAS = [
    BASE_DIR / "static" / "css" / "gerayse.css",
    BASE_DIR / "templates" / "cashops" / "layout.html",
    BASE_DIR / "templates" / "treasury" / "layout.html",
]

COLOR = re.compile(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(")


def _solo_css(texto):
    """Deja el CSS y borra todo lo demas, conservando los numeros de linea.

    Hace falta porque dos de las tres hojas viven dentro de un .html: si se
    escanea el archivo entero saltan falsos positivos que no son colores CSS
    (la entidad `&#9662;` del chevron, el `content` del meta theme-color, que
    obligatoriamente es un literal porque un <meta> no resuelve var()).
    """
    lineas = texto.splitlines()
    if not texto.lstrip().startswith("{%") and "<style>" not in texto:
        dentro = [True] * len(lineas)  # archivo .css puro
    else:
        dentro, abierto = [], False
        for linea in lineas:
            if "<style>" in linea:
                abierto = True
                dentro.append(False)
                continue
            if "</style>" in linea:
                abierto = False
                dentro.append(False)
                continue
            dentro.append(abierto)

    css = [linea if activa else "" for linea, activa in zip(lineas, dentro)]
    # Los comentarios /* */ pueden citar un color al explicar por que se saco.
    sin_comentarios = re.sub(
        r"/\*.*?\*/",
        lambda m: re.sub(r"[^\n]", " ", m.group(0)),
        "\n".join(css),
        flags=re.S,
    )
    return sin_comentarios.splitlines()


def _bloques(css):
    """[(selector, {token: valor})] de cada regla, ignorando comentarios Django."""
    css = re.sub(r"\{%\s*comment\s*%\}.*?\{%\s*endcomment\s*%\}", "", css, flags=re.S)
    salida = []
    for regla in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        tokens = dict(re.findall(r"(--[\w-]+)\s*:\s*([^;]+)\s*;", regla.group(2)))
        if tokens:
            salida.append((" ".join(regla.group(1).split()), tokens))
    return salida


def _paleta(css, tema):
    """Tokens efectivos de un tema: los sin-tema mas los propios del tema."""
    otro = "light" if tema == "dark" else "dark"
    efectivos = {}
    for selector, tokens in _bloques(css):
        if ":root" not in selector or 'data-theme="%s"' % otro in selector:
            continue
        efectivos.update(tokens)
    return efectivos


class PaletaTests(TestCase):
    def setUp(self):
        self.css = TOKENS.read_text(encoding="utf-8")

    def test_los_dos_temas_definen_el_mismo_set_de_tokens(self):
        oscuro = set(_paleta(self.css, "dark"))
        claro = set(_paleta(self.css, "light"))

        self.assertEqual(
            oscuro - claro,
            set(),
            "Estos tokens existen en oscuro y no en claro: el tema claro se los "
            "queda con el valor oscuro y aparece un color fuera de paleta.",
        )
        self.assertEqual(
            claro - oscuro,
            set(),
            "Estos tokens existen solo en claro: en oscuro quedan sin valor y "
            "la propiedad que los use no se aplica.",
        )

    def test_la_tinta_sobre_relleno_se_invierte_entre_temas(self):
        """--on-accent no es "un color claro": es la tinta que va sobre el verde.

        En oscuro el relleno de marca es verde vivo y la tinta es casi negra;
        en claro el relleno es verde ingles profundo y la tinta es papel. Si
        alguien lo "corrige" para que sea el mismo valor en los dos temas, el
        boton primario de uno de los dos queda sin contraste.
        """
        oscuro = _paleta(self.css, "dark")
        claro = _paleta(self.css, "light")

        self.assertNotEqual(
            oscuro["--on-accent"].lower(),
            claro["--on-accent"].lower(),
            "--on-accent tiene que ser distinto en cada tema: el relleno verde "
            "cambia de luminosidad, asi que la tinta encima tambien.",
        )


class ColoresHardcodeadosTests(TestCase):
    def test_las_hojas_del_sistema_no_traen_colores_fuera_de_paleta(self):
        """Un color literal en una hoja de estilos no lo alcanza ningun tema.

        Es exactamente lo que pasaba con `.input { background: #0e1315 }`: en
        claro dejaba todos los campos en negro con tinta oscura encima.
        """
        sueltos = []
        for hoja in HOJAS:
            for numero, linea in enumerate(_solo_css(hoja.read_text(encoding="utf-8")), 1):
                if COLOR.search(linea):
                    sueltos.append(
                        "%s:%s  %s" % (hoja.relative_to(BASE_DIR), numero, linea.strip()[:110])
                    )

        self.assertEqual(
            sueltos,
            [],
            "Colores literales fuera de la paleta (usa un token de "
            "templates/partials/theme_tokens.html):\n" + "\n".join(sueltos),
        )


class ShellsTests(TestCase):
    """Las tres shells tienen que traer el tema, o el usuario lo pierde al navegar."""

    def setUp(self):
        rol = Role.objects.create(code="ADMIN", name="Administrador")
        self.usuario = User.objects.create_superuser(
            username="tema", password="test", email="tema@example.com", role=rol
        )
        self.empresa = Empresa.objects.create(nombre="Empresa Tema SA")
        self.usuario.empresas_permitidas.set([self.empresa])

    def _assert_shell_con_tema(self, respuesta, donde):
        self.assertEqual(respuesta.status_code, 200, donde)
        html = respuesta.content.decode()

        self.assertIn(
            "gerayse-theme", html, f"{donde}: falta el arranque de tema (theme_boot)."
        )
        self.assertIn(
            'data-theme="light"', html, f"{donde}: falta la paleta clara (theme_tokens)."
        )
        self.assertIn(
            "data-theme-toggle", html, f"{donde}: falta el boton para cambiar de tema."
        )
        # Con el manifest de estaticos activo la URL sale versionada
        # (js/theme.2d21fbbcb530.js), asi que buscar el nombre literal daria un
        # falso negativo. Se matchea el nombre con hash opcional.
        self.assertRegex(
            html,
            r"js/theme(\.[0-9a-f]{8,})?\.js",
            f"{donde}: falta el runtime del tema.",
        )

    def test_shell_publica(self):
        self._assert_shell_con_tema(self.client.get(reverse("home")), "landing (base.html)")

    def test_shell_de_cajas(self):
        self.client.force_login(self.usuario)
        self._assert_shell_con_tema(
            self.client.get(reverse("cashops:dashboard")), "cajas (cashops/layout.html)"
        )

    def test_shell_de_tesoreria(self):
        self.client.force_login(self.usuario)
        self._assert_shell_con_tema(
            self.client.get(reverse("treasury:dashboard")), "tesoreria (treasury/layout.html)"
        )

    def test_el_arranque_va_antes_de_los_estilos(self):
        """Si el script corre despues del CSS, el usuario ve un flash del tema equivocado.

        La landing se pide anonima a proposito: logueado redirige al dashboard.
        """
        paginas = [("landing", reverse("home"), False)]
        paginas += [
            ("cajas", reverse("cashops:dashboard"), True),
            ("tesoreria", reverse("treasury:dashboard"), True),
        ]

        for nombre, url, requiere_login in paginas:
            with self.subTest(shell=nombre):
                self.client.logout()
                if requiere_login:
                    self.client.force_login(self.usuario)
                respuesta = self.client.get(url)
                self.assertEqual(respuesta.status_code, 200, f"{nombre}: no devolvio 200.")
                html = respuesta.content.decode()
                self.assertLess(
                    html.index("gerayse-theme"),
                    html.index('data-theme="light"'),
                    f"{nombre}: el arranque de tema quedo despues de la paleta.",
                )

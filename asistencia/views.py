import secrets
from datetime import date, datetime, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError
from django.db.models import Prefetch, Q
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.http import require_http_methods

from .alcance import empresas_en_alcance, locales_de_horarios, sucursales_en_alcance
from .carnets import (
    generar_carnet_celular_pdf,
    generar_carnets_pdf,
    generar_zip_carnets_celular,
    nombre_carnet_celular,
)
from .excel import CONTENT_TYPE_XLSX, generar_excel, jornadas_del_mes, ventana_del_mes
from .fichaje import Fichaje, Resultado, fichar
from .horarios import DIAS, Celda, CeldaInvalida, dia_editable, dias_de_la_semana, leer_celda, lunes_de
from .forms import (
    AnularCarnetForm,
    AnularMarcaForm,
    CarnetsFiltroForm,
    EmpleadoFiltroForm,
    EmpleadoForm,
    ExcelForm,
    ImportarEmpleadosForm,
    MarcaFiltroForm,
    MarcaManualForm,
    PorLocalForm,
    ReglasForm,
)
from .importacion import Estado, FilaListado, ListadoIlegible, leer_listado, procesar_listado
from .models import CarnetAnulado, Empleado, Horario, Marca, ReglasAsistencia
from .resumen import calcular_meses
from .permissions import (
    can_administrar,
    can_cargar_horarios,
    can_fichar,
    ensure_acceder,
    ensure_administrar,
    ensure_cargar_horarios,
    ensure_fichar,
)
from .por_local import asistencia_del_dia
from .services import (
    actualizar_empleado,
    agregar_marca,
    anular_carnet,
    anular_marca,
    copiar_semana_anterior,
    crear_empleado,
    dar_de_baja_empleado,
    empleados_de_la_grilla,
    guardar_reglas,
    GrillaDesactualizada,
    guardar_semana,
    reactivar_empleado,
)

SESION_LISTADO = "asistencia_listado"


@login_required
def inicio(request):
    ensure_acceder(request.user)
    return render(
        request,
        "asistencia/inicio.html",
        {
            "title": "Asistencia",
            "puede_fichar": can_fichar(request.user),
            "puede_administrar": can_administrar(request.user),
            "puede_cargar_horarios": can_cargar_horarios(request.user),
        },
    )


def _empleados_en_alcance(request):
    return Empleado.objects.filter(empresa__in=empresas_en_alcance(request)).select_related(
        "empresa", "sucursal", "dado_de_baja_por"
    )


def _empleado_en_alcance_or_404(request, empleado_id: int) -> Empleado:
    # Un empleado de otra empresa responde 404, como en tesoreria: para este
    # usuario no existe.
    return get_object_or_404(_empleados_en_alcance(request), pk=empleado_id)


def _volcar_error(form, error) -> None:
    if isinstance(error, IntegrityError):
        form.add_error("dni", "Ya hay un empleado activo con ese DNI.")
    elif hasattr(error, "message_dict"):
        for campo, mensajes in error.message_dict.items():
            destino = campo if campo in form.fields else None
            for mensaje in mensajes:
                form.add_error(destino, mensaje)
    else:
        for mensaje in error.messages:
            form.add_error(None, mensaje)


def _mensaje_de_error(error) -> str:
    return " ".join(error.messages) if isinstance(error, ValidationError) else str(error)


@login_required
def empleado_list(request):
    ensure_administrar(request.user)
    empresas = empresas_en_alcance(request)
    sucursales = sucursales_en_alcance(request)
    filtro = EmpleadoFiltroForm(request.GET or None, empresas=empresas, sucursales=sucursales)
    base = _empleados_en_alcance(request)
    empleados = base
    estado = "activos"
    if filtro.is_valid():
        datos = filtro.cleaned_data
        estado = datos.get("estado") or "activos"
        q = (datos.get("q") or "").strip()
        if q:
            empleados = empleados.filter(Q(nombre__icontains=q) | Q(dni__icontains=q.replace(".", "")))
        if datos.get("empresa"):
            empleados = empleados.filter(empresa=datos["empresa"])
        if datos.get("sucursal"):
            empleados = empleados.filter(sucursal=datos["sucursal"])
    if estado == "baja":
        empleados = empleados.filter(activo=False)
    elif estado != "todos":
        empleados = empleados.filter(activo=True)
    empleados = empleados.prefetch_related(
        Prefetch(
            "carnets_anulados",
            queryset=CarnetAnulado.objects.select_related("anulado_por"),
            to_attr="anulaciones",
        )
    )
    return render(
        request,
        "asistencia/empleado_list.html",
        {
            "title": "Empleados",
            "filtro": filtro,
            "empleados": empleados,
            # Evalua el queryset una vez: la plantilla recorre el mismo cache.
            "hay_activos": any(empleado.activo for empleado in empleados),
            "activos_count": base.filter(activo=True).count(),
            "baja_count": base.filter(activo=False).count(),
        },
    )


def _render_form(request, form, *, title, submit_label, form_action, status=200):
    return render(
        request,
        "cashops/form_page.html",
        {
            "title": title,
            "subtitle": "La empresa y el local salen de tus empresas seleccionadas.",
            "form": form,
            "submit_label": submit_label,
            "back_url": reverse("asistencia:empleado_list"),
            "form_action": form_action,
            "disable_htmx": True,
        },
        status=status,
    )


@login_required
@require_http_methods(["GET", "POST"])
def empleado_create(request):
    ensure_administrar(request.user)
    form = EmpleadoForm(request.POST or None, empresas=empresas_en_alcance(request))
    if request.method == "POST" and form.is_valid():
        try:
            empleado = crear_empleado(actor=request.user, **form.cleaned_data)
        except (ValidationError, IntegrityError) as error:
            _volcar_error(form, error)
        else:
            messages.success(request, f"{empleado.nombre} quedó cargado en el padrón.")
            return redirect("asistencia:empleado_list")
    return _render_form(
        request,
        form,
        title="Nuevo empleado",
        submit_label="Guardar",
        form_action=reverse("asistencia:empleado_create"),
        status=400 if request.method == "POST" else 200,
    )


@login_required
@require_http_methods(["GET", "POST"])
def empleado_update(request, empleado_id: int):
    ensure_administrar(request.user)
    empleado = _empleado_en_alcance_or_404(request, empleado_id)
    form = EmpleadoForm(request.POST or None, instance=empleado, empresas=empresas_en_alcance(request))
    if request.method == "POST" and form.is_valid():
        try:
            empleado = actualizar_empleado(actor=request.user, empleado=empleado, **form.cleaned_data)
        except (ValidationError, IntegrityError) as error:
            _volcar_error(form, error)
        else:
            messages.success(request, f"{empleado.nombre} quedó actualizado.")
            return redirect("asistencia:empleado_list")
    return _render_form(
        request,
        form,
        title=f"Editar {empleado.nombre}",
        submit_label="Guardar cambios",
        form_action=reverse("asistencia:empleado_update", args=[empleado.pk]),
        status=400 if request.method == "POST" else 200,
    )


@login_required
@require_http_methods(["POST"])
def empleado_baja(request, empleado_id: int):
    ensure_administrar(request.user)
    empleado = _empleado_en_alcance_or_404(request, empleado_id)
    try:
        empleado = dar_de_baja_empleado(actor=request.user, empleado=empleado)
    except ValidationError as error:
        messages.error(request, _mensaje_de_error(error))
    else:
        messages.success(request, f"{empleado.nombre} quedó dado de baja: ya no puede fichar.")
    return redirect("asistencia:empleado_list")


@login_required
@require_http_methods(["POST"])
def empleado_reactivar(request, empleado_id: int):
    ensure_administrar(request.user)
    empleado = _empleado_en_alcance_or_404(request, empleado_id)
    try:
        empleado = reactivar_empleado(actor=request.user, empleado=empleado)
    except (ValidationError, IntegrityError) as error:
        mensaje = "Ya hay un empleado activo con ese DNI." if isinstance(error, IntegrityError) else _mensaje_de_error(error)
        messages.error(request, mensaje)
    else:
        messages.success(request, f"{empleado.nombre} quedó activo otra vez.")
    return redirect("asistencia:empleado_list")


def _render_importacion(request, form, filas=None, *, confirmado=False, token="", status=200):
    filas = filas or []
    return render(
        request,
        "asistencia/empleado_importar.html",
        {
            "title": "Cargar empleados desde un listado",
            "form": form,
            "filas": filas,
            "confirmado": confirmado,
            "token": token,
            "listas": sum(1 for f in filas if f.estado == Estado.LISTA),
            "cargadas": sum(1 for f in filas if f.estado == Estado.CARGADA),
            "rechazadas": sum(1 for f in filas if f.estado == Estado.RECHAZADA),
        },
        status=status,
    )


@login_required
@require_http_methods(["GET", "POST"])
def empleado_importar(request):
    ensure_administrar(request.user)
    form = ImportarEmpleadosForm(request.POST or None, request.FILES or None)
    if request.method != "POST":
        # No se borra la revision pendiente: puede estar abierta en otra pestana,
        # y el token ya impide confirmar una que no sea la ultima.
        return _render_importacion(request, form)
    if not form.is_valid():
        return _render_importacion(request, form, status=400)
    try:
        filas = leer_listado(form.cleaned_data["archivo"])
    except ListadoIlegible as error:
        form.add_error("archivo", str(error))
        return _render_importacion(request, form, status=400)
    filas = procesar_listado(filas, actor=request.user, sucursales=sucursales_en_alcance(request), guardar=False)
    token = ""
    if any(f.estado == Estado.LISTA for f in filas):
        # El token ata el boton de confirmar a esta revision: si en otra pestana
        # se reviso otro listado, confirmar este no carga aquel.
        token = secrets.token_urlsafe(16)
        request.session[SESION_LISTADO] = {"token": token, "filas": [f.datos() for f in filas]}
    else:
        request.session.pop(SESION_LISTADO, None)
    return _render_importacion(request, ImportarEmpleadosForm(), filas, token=token)


@login_required
@require_http_methods(["POST"])
def empleado_importar_confirmar(request):
    ensure_administrar(request.user)
    datos = request.session.get(SESION_LISTADO)
    # En bytes: compare_digest con str rechaza (500) un token con caracteres no ASCII.
    token = str(request.POST.get("token", "")).encode()
    if not datos or not secrets.compare_digest(token, str(datos.get("token", "")).encode()):
        messages.error(request, "Ese listado ya no es el último que revisaste. Subilo de nuevo.")
        return redirect("asistencia:empleado_importar")
    del request.session[SESION_LISTADO]
    filas = procesar_listado(
        [FilaListado(**fila) for fila in datos["filas"]],
        actor=request.user,
        sucursales=sucursales_en_alcance(request),
        guardar=True,
    )
    cargadas = sum(1 for f in filas if f.estado == Estado.CARGADA)
    messages.success(request, f"Se cargaron {cargadas} empleado{'s' if cargadas != 1 else ''}.")
    return _render_importacion(request, ImportarEmpleadosForm(), filas, confirmado=True)


def _respuesta_pdf(contenido: bytes, nombre_archivo: str) -> HttpResponse:
    response = HttpResponse(contenido, content_type="application/pdf")
    # inline: se abre en el navegador, listo para imprimir.
    response["Content-Disposition"] = f'inline; filename="{nombre_archivo}"'
    return response


@login_required
def carnets(request):
    ensure_administrar(request.user)
    empresas = empresas_en_alcance(request)
    sucursales = sucursales_en_alcance(request)
    filtro = CarnetsFiltroForm(request.GET or None, empresas=empresas, sucursales=sucursales)
    activos = _empleados_en_alcance(request).filter(activo=True)
    if filtro.is_valid():
        if filtro.cleaned_data.get("empresa"):
            activos = activos.filter(empresa=filtro.cleaned_data["empresa"])
        if filtro.cleaned_data.get("sucursal"):
            activos = activos.filter(sucursal=filtro.cleaned_data["sucursal"])
    if request.GET.get("pdf"):
        if not filtro.is_valid() and request.GET.get("empresa", "") + request.GET.get("sucursal", ""):
            messages.error(request, "Elegí una empresa y un local de tus empresas seleccionadas.")
            return redirect("asistencia:carnets")
        lista = list(activos)
        if not lista:
            messages.error(request, "No hay empleados activos para imprimir con ese filtro.")
            return redirect("asistencia:carnets")
        return _respuesta_pdf(generar_carnets_pdf(lista), "carnets-asistencia.pdf")
    return render(
        request,
        "asistencia/carnets.html",
        {"title": "Carnets", "filtro": filtro, "cantidad": activos.count()},
    )


@login_required
def carnet_empleado(request, empleado_id: int):
    ensure_administrar(request.user)
    empleado = _empleado_en_alcance_or_404(request, empleado_id)
    if not empleado.activo:
        messages.error(request, f"{empleado.nombre} está dado de baja: no se imprime su carnet.")
        return redirect("asistencia:empleado_list")
    return _respuesta_pdf(generar_carnets_pdf([empleado]), f"carnet-{slugify(empleado.nombre) or empleado.pk}.pdf")


@login_required
def carnet_celular(request, empleado_id: int):
    """El carnet de un empleado para mandar por WhatsApp y mostrar en el celular."""
    ensure_administrar(request.user)
    empleado = _empleado_en_alcance_or_404(request, empleado_id)
    if not empleado.activo:
        messages.error(request, f"{empleado.nombre} está dado de baja: no se genera su carnet.")
        return redirect("asistencia:empleado_list")
    return _respuesta_pdf(generar_carnet_celular_pdf(empleado), nombre_carnet_celular(empleado))


@login_required
@require_http_methods(["POST"])
def carnets_celular(request):
    """Los carnets del celular de los marcados en el padron: un PDF por persona, en un ZIP."""
    ensure_administrar(request.user)
    ids = [valor for valor in request.POST.getlist("empleado") if valor.isdigit()]
    # Solo activos y de las empresas del usuario: un id ajeno o de baja no suma.
    empleados = list(_empleados_en_alcance(request).filter(activo=True, pk__in=ids).order_by("nombre", "pk"))
    if not empleados:
        messages.error(request, "Marcá al menos un empleado activo para bajar sus carnets.")
        return redirect("asistencia:empleado_list")
    response = HttpResponse(generar_zip_carnets_celular(empleados), content_type="application/zip")
    response["Content-Disposition"] = 'attachment; filename="carnets-celular.zip"'
    return response


@login_required
@require_http_methods(["GET", "POST"])
def carnet_anular(request, empleado_id: int):
    ensure_administrar(request.user)
    empleado = _empleado_en_alcance_or_404(request, empleado_id)
    form = AnularCarnetForm(request.POST or None, initial={"anulaciones_previas": empleado.carnets_anulados.count()})
    if request.method == "POST" and form.is_valid():
        try:
            empleado = anular_carnet(
                actor=request.user,
                empleado=empleado,
                motivo=form.cleaned_data["motivo"],
                anulaciones_previas=form.cleaned_data["anulaciones_previas"],
            )
        except ValidationError as error:
            _volcar_error(form, error)
        else:
            messages.success(
                request,
                f"El carnet anterior de {empleado.nombre} ya no sirve para fichar. Imprimí el nuevo con «Carnet».",
            )
            return redirect("asistencia:empleado_list")
    return render(
        request,
        "cashops/form_page.html",
        {
            "title": f"Anular el carnet de {empleado.nombre}",
            "subtitle": "El carnet actual deja de servir para fichar y se genera uno nuevo para imprimir. No se puede deshacer.",
            "form": form,
            "submit_label": "Anular carnet",
            "back_url": reverse("asistencia:empleado_list"),
            "form_action": reverse("asistencia:carnet_anular", args=[empleado.pk]),
            "disable_htmx": True,
        },
        status=400 if request.method == "POST" else 200,
    )


SESION_LOCAL_FICHAJE = "asistencia_local_fichaje"


def _local_de_fichaje(request):
    """(local, locales que puede elegir; None = local fijo, sin selector).
    - Quien administra asistencia elige entre todos los locales de su alcance.
    - El cajero con un solo local (su sucursal base) ficha ahi, fijo.
    - El cajero con otros locales asignados en su ficha elige entre los suyos.
    Lo elegido queda en la sesion; si no eligio, arranca en su sucursal base."""
    if can_administrar(request.user):
        locales = sucursales_en_alcance(request).select_related("empresa")
    else:
        propios = request.user.locales_de_fichaje().filter(empresa__in=empresas_en_alcance(request))
        if propios.count() <= 1:
            return propios.first() or request.user.sucursal_base, None
        locales = propios.select_related("empresa")
    elegido = request.session.get(SESION_LOCAL_FICHAJE)
    local = locales.filter(pk=elegido).first() if elegido else None
    if local is None and request.user.sucursal_base_id:
        local = locales.filter(pk=request.user.sucursal_base_id).first()
    return local, locales


@login_required
@require_http_methods(["GET", "POST"])
def fichar_view(request):
    ensure_fichar(request.user)
    local, locales = _local_de_fichaje(request)
    if request.method == "POST" and "elegir_local" in request.POST:
        if locales is None:
            raise PermissionDenied("Tu usuario ficha siempre en su local.")
        pedido = request.POST.get("local", "")
        elegido = locales.filter(pk=int(pedido)).first() if pedido.isascii() and pedido.isdigit() else None
        if elegido is None:
            messages.error(request, "Elegí un local de la lista.")
        else:
            request.session[SESION_LOCAL_FICHAJE] = elegido.pk
            messages.success(request, f"Fichando en {elegido.nombre}.")
        return redirect("asistencia:fichar")
    fichaje = None
    if request.method == "POST" and locales is not None and local is None:
        # Quien administra y todavia no eligio local: no se cae a una sucursal
        # base que puede estar fuera del alcance.
        fichaje = Fichaje(Resultado.SIN_LOCAL, "Elegí el local donde estás fichando.")
    elif request.method == "POST":
        fichaje = fichar(actor=request.user, codigo=request.POST.get("codigo", ""), sucursal=local)
        if request.headers.get("Accept", "").startswith("application/json"):
            # Para la lectura con la camara: la pantalla no se recarga.
            return JsonResponse(
                {
                    "resultado": fichaje.resultado,
                    "registrada": fichaje.registrada,
                    "mensaje": fichaje.mensaje,
                    "nombre": fichaje.empleado.nombre if fichaje.empleado else "",
                    "tipo": fichaje.marca.get_tipo_display() if fichaje.marca else "",
                    "hora": timezone.localtime(fichaje.marca.momento).strftime("%H:%M") if fichaje.marca else "",
                }
            )
    return render(
        request,
        "asistencia/fichar.html",
        {"title": "Fichar", "fichaje": fichaje, "sucursal": local, "locales": locales},
    )


@login_required
def por_local(request):
    """Como va la asistencia de un dia en cada local (EP-14 D15)."""
    ensure_administrar(request.user)
    locales = sucursales_en_alcance(request).select_related("empresa")
    hoy = timezone.localdate()
    form = PorLocalForm(request.GET or None, locales=locales, initial={"fecha": hoy})
    fecha, elegidos = hoy, locales
    if form.is_bound and form.is_valid():
        fecha = form.cleaned_data.get("fecha") or hoy
        if form.cleaned_data.get("local"):
            elegidos = locales.filter(pk=form.cleaned_data["local"].pk)
    resumenes = asistencia_del_dia(fecha, elegidos, empresas_en_alcance(request).values_list("pk", flat=True))
    return render(
        request,
        "asistencia/por_local.html",
        {
            "title": "Asistencia por local",
            "form": form,
            "fecha": fecha,
            "es_hoy": fecha == hoy,
            "resumenes": resumenes,
            "trabajando": sum(r.trabajando for r in resumenes),
            "ficharon": sum(len(r.filas) for r in resumenes),
        },
    )


@login_required
def excel(request):
    ensure_administrar(request.user)
    empleados = _empleados_en_alcance(request)
    form = ExcelForm(
        request.GET or None,
        initial={"mes": timezone.localdate().strftime("%Y-%m")},
        empresas=empresas_en_alcance(request),
        empleados=empleados,
    )
    if not request.GET.get("descargar"):
        return render(request, "asistencia/excel.html", {"title": "Excel del mes", "form": form})
    if not form.is_valid():
        return render(request, "asistencia/excel.html", {"title": "Excel del mes", "form": form}, status=400)
    anio, mes = form.cleaned_data["mes"]
    desde, hasta = ventana_del_mes(anio, mes)
    inicio_mes = timezone.make_aware(datetime(anio, mes, 1))
    fin_mes = timezone.make_aware(datetime(anio + (mes == 12), mes % 12 + 1, 1))
    seleccion = empleados
    if form.cleaned_data.get("empresa"):
        seleccion = seleccion.filter(empresa=form.cleaned_data["empresa"])
    if form.cleaned_data.get("empleado"):
        seleccion = seleccion.filter(pk=form.cleaned_data["empleado"].pk)
    else:
        # Todo activo, y los dados de baja solo si tienen marcas ese mes o un
        # horario de trabajo (pueden haber faltado antes de irse, EP-14 D18).
        con_marcas = Marca.objects.filter(anulada=False, momento__gte=inicio_mes, momento__lt=fin_mes)
        con_horario = Horario.objects.filter(franco=False, fecha__gte=inicio_mes.date(), fecha__lt=fin_mes.date())
        seleccion = seleccion.filter(
            Q(activo=True) | Q(pk__in=con_marcas.values("empleado_id")) | Q(pk__in=con_horario.values("empleado_id"))
        )
    seleccion = seleccion.prefetch_related(
        Prefetch(
            "marcas",
            queryset=Marca.objects.filter(anulada=False, momento__gte=desde, momento__lt=hasta).order_by("momento", "pk"),
            to_attr="marcas_mes",
        )
    ).order_by("nombre", "pk")
    lista = list(seleccion)
    if not form.cleaned_data.get("empleado"):
        # Un dado de baja cuya unica marca es la salida del dia 1 que cierra el mes
        # anterior no tiene nada en este mes: no lleva hoja. Si no fichó nada pero
        # tenia horario antes de la baja, lleva hoja por sus faltas.
        trabajaron = {e.pk for e in lista if any(jornadas_del_mes(e.marcas_mes, anio, mes).values())}
        sin_marcas = [e for e in lista if not e.activo and e.pk not in trabajaron]
        faltaron = {r.empleado.pk for r in calcular_meses([(e, e.marcas_mes) for e in sin_marcas], anio, mes) if r.faltas}
        lista = [e for e in lista if e.activo or e.pk in trabajaron or e.pk in faltaron]
    if not lista:
        messages.error(request, "No hay empleados para ese mes con ese filtro.")
        return redirect("asistencia:excel")
    contenido = generar_excel([(empleado, empleado.marcas_mes) for empleado in lista], anio, mes)
    nombre = f"asistencia-{anio}-{mes:02d}"
    if form.cleaned_data.get("empleado"):
        nombre += f"-{slugify(lista[0].nombre) or lista[0].pk}"
    response = HttpResponse(contenido, content_type=CONTENT_TYPE_XLSX)
    response["Content-Disposition"] = f'attachment; filename="{nombre}.xlsx"'
    return response



def _form_reglas(reglas: ReglasAsistencia, datos=None) -> ReglasForm:
    return ReglasForm(
        datos,
        prefix=f"empresa-{reglas.empresa_id}",
        initial={"jornada": reglas.jornada_texto, "tolerancia": reglas.tolerancia_mensual_minutos},
    )


@login_required
@require_http_methods(["GET", "POST"])
def reglas(request):
    """Jornada diaria y tolerancia del mes de cada empresa del alcance (US-14.10)."""
    ensure_administrar(request.user)
    empresas = list(empresas_en_alcance(request))
    actuales = ReglasAsistencia.de_empresas(empresas)
    formularios = {pk: _form_reglas(regla) for pk, regla in actuales.items()}
    status = 200
    if request.method == "POST":
        empresa_id = _id_pedido(request.POST.get("empresa", ""))
        # Una empresa fuera del alcance (o un id tocado a mano) no existe para este usuario.
        if empresa_id not in actuales:
            raise Http404
        form = _form_reglas(actuales[empresa_id], request.POST)
        if form.is_valid():
            empresa = next(e for e in empresas if e.pk == empresa_id)
            try:
                guardar_reglas(
                    actor=request.user,
                    empresa=empresa,
                    jornada_minutos=form.cleaned_data["jornada"],
                    tolerancia_mensual_minutos=form.cleaned_data["tolerancia"],
                )
            except ValidationError as error:
                form.add_error(None, _mensaje_de_error(error))
            else:
                messages.success(request, f"Reglas de {empresa.nombre} guardadas.")
                return redirect("asistencia:reglas")
        formularios[empresa_id] = form
        status = 400
    filas = [(empresa, actuales[empresa.pk], formularios[empresa.pk]) for empresa in empresas]
    return render(request, "asistencia/reglas.html", {"title": "Reglas", "filas": filas}, status=status)



def _semana_pedida(texto) -> date:
    """El lunes de la semana pedida. Una fecha que no se entiende o fuera de
    rango (un 0001-01-01 tocado a mano) vuelve a la semana de hoy."""
    try:
        dia = date.fromisoformat(texto or "")
    except ValueError:
        dia = None
    if dia is None or not date(2020, 1, 1) <= dia <= date(2099, 12, 31):
        dia = timezone.localdate()
    return lunes_de(dia)


def _id_pedido(valor: str) -> int | None:
    """Un id que llega en la URL o en el form: digitos ASCII y un largo
    razonable (int() de miles de digitos da error)."""
    if valor and len(valor) <= 18 and valor.isascii() and valor.isdigit():
        return int(valor)
    return None


def _misma_celda(texto_a: str, texto_b: str) -> bool:
    """Si dos textos de casilla dicen lo mismo ("8" y "8:00"); si alguno no se
    entiende, se comparan tal cual."""
    try:
        return leer_celda(texto_a) == leer_celda(texto_b)
    except CeldaInvalida:
        return texto_a.strip() == texto_b.strip()


def _nombre_celda(empleado_id, dia) -> str:
    return f"c-{empleado_id}-{dia:%Y%m%d}"


def _url_horarios(local, lunes) -> str:
    return f"{reverse('asistencia:horarios')}?local={local.pk}&semana={lunes.isoformat()}"


@login_required
@require_http_methods(["GET", "POST"])
def horarios(request):
    """Grilla semanal de horarios de un local (US-14.13). La carga el encargado
    (hasta el dia anterior) o quien administra.

    Cada fila viaja con lo que mostraban sus casillas al abrir la pantalla
    ("o-<empleado>", los 7 dias separados por "|": un solo campo por fila, asi
    un local grande no pasa el limite de campos de Django). Solo se guardan las
    casillas que el usuario cambio, y si otra persona cambio una de esas en el
    medio, no se pisa: se avisa en la casilla.
    """
    ensure_cargar_horarios(request.user)
    locales = list(locales_de_horarios(request))
    if not locales:
        return render(request, "asistencia/horarios.html", {"title": "Horarios", "locales": []})
    fuente = request.POST if request.method == "POST" else request.GET
    pedido = fuente.get("local", "")
    if pedido:
        # Un local que no es suyo (o un id tocado a mano) no existe para este usuario.
        local_id = _id_pedido(pedido)
        local = next((opcion for opcion in locales if opcion.pk == local_id), None)
        if local is None:
            raise Http404
    else:
        local = locales[0]
    lunes = _semana_pedida(fuente.get("semana"))
    dias = dias_de_la_semana(lunes)
    administra = can_administrar(request.user)
    hoy = timezone.localdate()
    empleados = list(empleados_de_la_grilla(local))
    valores, vistos, errores, avisos = {}, {}, {}, []
    status = 200

    if request.method == "POST" and request.POST.get("accion") == "copiar":
        try:
            copiadas = copiar_semana_anterior(actor=request.user, sucursal=local, lunes=lunes)
        except ValidationError:
            messages.error(request, "Otra persona cargó esta semana mientras copiabas. Revisala y volvé a copiar si hace falta.")
        else:
            if copiadas:
                messages.success(request, f"Se copiaron {copiadas} casilla{'s' if copiadas != 1 else ''} de la semana anterior.")
            else:
                messages.info(request, "No había nada para copiar: la semana anterior está vacía o estas casillas ya estaban cargadas.")
        return redirect(_url_horarios(local, lunes))

    if request.method == "POST":
        celdas, originales, vieja = {}, {}, False
        for empleado in empleados:
            fila = request.POST.get(f"o-{empleado.pk}")
            partes = fila.split("|") if fila is not None else []
            for indice, dia in enumerate(dias):
                nombre = _nombre_celda(empleado.pk, dia)
                if nombre not in request.POST:
                    continue  # casilla cerrada (no se envia): no se toca
                valores[nombre] = request.POST[nombre]
                if len(partes) != len(dias):
                    vieja = True
                    continue
                vistos[nombre] = partes[indice]
                try:
                    nueva = leer_celda(valores[nombre])
                except CeldaInvalida as error:
                    errores[nombre] = str(error)
                    continue
                try:
                    original = leer_celda(partes[indice])
                except CeldaInvalida:
                    vieja = True
                    continue
                if nueva != original:
                    celdas[(empleado.pk, dia)] = nueva
                    originales[(empleado.pk, dia)] = original
        if vieja:
            # Sin lo que mostraba la pantalla no se puede saber que cambio el usuario:
            # se vuelve a abrir con lo guardado, sin dibujar lo recibido (que pisaria al reenviarse).
            messages.error(
                request,
                "Esta pantalla había quedado vieja: la abrimos de nuevo con lo guardado. No se guardó nada; volvé a escribir lo que cambiaste.",
            )
            return redirect(_url_horarios(local, lunes))
        if errores:
            avisos.append("Hay casillas que no se entienden: están marcadas en rojo. No se guardó nada de la semana.")
        else:
            try:
                cambios = guardar_semana(
                    actor=request.user, sucursal=local, lunes=lunes, celdas=celdas, originales=originales
                )
            except GrillaDesactualizada as error:
                for (empleado_id, dia), actual in error.conflictos.items():
                    nombre = _nombre_celda(empleado_id, dia)
                    errores[nombre] = (
                        f"Otra persona la cambió mientras la cargabas: ahora dice «{actual.texto or 'vacía'}». "
                        "Si querés dejar lo tuyo, volvé a guardar."
                    )
                    # El proximo guardado ya sabe lo que hay ahora: pisa a sabiendas.
                    vistos[nombre] = actual.texto
                avisos.append("Otra persona cambió esta semana mientras la cargabas. No se guardó nada: revisá las casillas en rojo.")
            except PermissionDenied as error:
                avisos.append(str(error))
                status = 403
            except ValidationError as error:
                avisos.append(_mensaje_de_error(error))
            else:
                if cambios:
                    messages.success(request, f"Semana guardada: {cambios} cambio{'s' if cambios != 1 else ''}.")
                else:
                    messages.info(request, "No había cambios para guardar.")
                return redirect(_url_horarios(local, lunes))
        status = status if status != 200 else 400

    existentes = {
        (h.empleado_id, h.fecha): h
        for h in Horario.objects.filter(empleado__in=empleados, fecha__in=dias).select_related("cargado_por")
    }
    filas = []
    for empleado in empleados:
        celdas_fila = []
        for dia in dias:
            nombre = _nombre_celda(empleado.pk, dia)
            horario = existentes.get((empleado.pk, dia))
            actual = Celda.de_horario(horario).texto
            bloqueada = not dia_editable(dia, administra=administra, hoy=hoy)
            valor, original = valores.get(nombre, actual), vistos.get(nombre, actual)
            if bloqueada:
                # Un dia que se cerro mientras cargaba: se muestra lo guardado y, si habia
                # escrito otra cosa, se dice que no se guardo.
                if nombre in valores and not _misma_celda(valor, actual) and nombre not in errores:
                    errores[nombre] = "Este día ya cerró: no se guardó lo que escribiste. Si hace falta, pedíselo a administración."
                valor = original = actual
            elif nombre in valores and nombre not in errores and _misma_celda(valor, original):
                # No la toco: se muestra lo que hay ahora (quizas lo cambio otro).
                valor = original = actual
            celdas_fila.append(
                {
                    "nombre": nombre,
                    "dia": dia,
                    "valor": valor,
                    "original": original,
                    "bloqueada": bloqueada,
                    "error": errores.get(nombre, ""),
                    "cargado": horario,
                }
            )
        filas.append(
            {
                "empleado": empleado,
                "celdas": celdas_fila,
                # Lo que mostraba cada casilla al abrir (o lo que hay ahora, despues de un choque).
                "originales": "|".join(celda["original"] for celda in celdas_fila),
            }
        )
    cabecera = [
        {"dia": dia, "nombre": DIAS[dia.weekday()], "bloqueado": not dia_editable(dia, administra=administra, hoy=hoy), "hoy": dia == hoy}
        for dia in dias
    ]
    return render(
        request,
        "asistencia/horarios.html",
        {
            "title": "Horarios",
            "locales": locales,
            "local": local,
            "lunes": lunes,
            "domingo": dias[-1],
            "anterior": lunes - timedelta(days=7),
            "siguiente": lunes + timedelta(days=7),
            "cabecera": cabecera,
            "filas": filas,
            "avisos": avisos,
            "errores": [(fila["empleado"], celda) for fila in filas for celda in fila["celdas"] if celda["error"]],
            "sin_guardar": status != 200,
            "administra": administra,
            "hay_cerradas": any(c["bloqueado"] for c in cabecera),
        },
        status=status,
    )

MAXIMO_MARCAS_LISTADO = 500


def _marcas_en_alcance(request):
    return Marca.objects.filter(empleado__in=_empleados_en_alcance(request)).select_related(
        "empleado", "sucursal", "registrada_por", "anulada_por"
    )


def _url_marcas(empleado_id, fecha) -> str:
    dia = fecha.isoformat()
    return f"{reverse('asistencia:marca_list')}?empleado={empleado_id}&desde={dia}&hasta={dia}&anuladas=on"


@login_required
def marca_list(request):
    ensure_administrar(request.user)
    empleados = _empleados_en_alcance(request)
    filtro = MarcaFiltroForm(request.GET or None, empleados=empleados, sucursales=sucursales_en_alcance(request))
    marcas = _marcas_en_alcance(request)
    hasta = timezone.localdate()
    desde = hasta - timedelta(days=6)
    mostrar_anuladas = False
    if filtro.is_valid():
        datos = filtro.cleaned_data
        if datos.get("empleado"):
            marcas = marcas.filter(empleado=datos["empleado"])
        if datos.get("sucursal"):
            marcas = marcas.filter(sucursal=datos["sucursal"])
        desde = datos.get("desde") or desde
        hasta = datos.get("hasta") or hasta
        mostrar_anuladas = datos.get("anuladas")
    marcas = marcas.filter(momento__date__gte=desde, momento__date__lte=hasta)
    if not mostrar_anuladas:
        marcas = marcas.filter(anulada=False)
    total = marcas.count()
    return render(
        request,
        "asistencia/marca_list.html",
        {
            "title": "Marcas",
            "filtro": filtro,
            "marcas": marcas[:MAXIMO_MARCAS_LISTADO],
            "total": total,
            "recortado": total > MAXIMO_MARCAS_LISTADO,
            "maximo": MAXIMO_MARCAS_LISTADO,
            "desde": desde,
            "hasta": hasta,
            # Un local de una empresa fuera del alcance no se nombra.
            "empresas_visibles": set(empresas_en_alcance(request).values_list("pk", flat=True)),
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def marca_create(request):
    ensure_administrar(request.user)
    form = MarcaManualForm(
        request.POST or None,
        initial={"empleado": request.GET.get("empleado")} if request.GET.get("empleado") else None,
        empleados=_empleados_en_alcance(request),
        sucursales=sucursales_en_alcance(request),
    )
    if request.method == "POST" and form.is_valid():
        datos = form.cleaned_data
        try:
            marca = agregar_marca(
                actor=request.user,
                empleado=datos["empleado"],
                momento=datos["momento"],
                tipo=datos["tipo"],
                sucursal=datos["sucursal"],
                motivo=datos["motivo"],
            )
        except ValidationError as error:
            _volcar_error(form, error)
        else:
            messages.success(request, f"Se agregó la {marca.get_tipo_display().lower()} de {marca.empleado.nombre}: queda señalada como corregida.")
            return redirect(_url_marcas(marca.empleado_id, timezone.localtime(marca.momento).date()))
    return render(
        request,
        "cashops/form_page.html",
        {
            "title": "Agregar una marca",
            "subtitle": "Para cuando alguien no fichó o llegó antes que el cajero. Queda señalada como corregida, con tu usuario y la fecha.",
            "form": form,
            "submit_label": "Agregar marca",
            "back_url": reverse("asistencia:marca_list"),
            "form_action": reverse("asistencia:marca_create"),
            "disable_htmx": True,
        },
        status=400 if request.method == "POST" else 200,
    )


@login_required
@require_http_methods(["GET", "POST"])
def marca_anular(request, marca_id: int):
    ensure_administrar(request.user)
    marca = get_object_or_404(_marcas_en_alcance(request), pk=marca_id)
    volver = _url_marcas(marca.empleado_id, timezone.localtime(marca.momento).date())
    if marca.anulada:
        messages.error(request, "Esa marca ya está anulada.")
        return redirect(volver)
    form = AnularMarcaForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            anular_marca(actor=request.user, marca=marca, motivo=form.cleaned_data["motivo"])
        except ValidationError as error:
            _volcar_error(form, error)
        else:
            messages.success(request, "La marca quedó anulada: ya no cuenta, pero no se borra.")
            return redirect(volver)
    momento = timezone.localtime(marca.momento)
    return render(
        request,
        "cashops/form_page.html",
        {
            "title": f"Anular la {marca.get_tipo_display().lower()} de {marca.empleado.nombre}",
            "subtitle": f"Del {momento:%d/%m/%Y} a las {momento:%H:%M}. Deja de contar para el fichaje y el Excel, y queda registrado quién la anuló y por qué.",
            "form": form,
            "submit_label": "Anular marca",
            "back_url": volver,
            "form_action": reverse("asistencia:marca_anular", args=[marca.pk]),
            "disable_htmx": True,
        },
        status=400 if request.method == "POST" else 200,
    )

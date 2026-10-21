/*
 * Fichaje con la camara del celular (EP-14 US-14.4).
 *
 * Lee el QR del carnet con jsQR (static/asistencia/vendor, sin CDN: Safari de
 * iPhone no trae BarcodeDetector) y lo manda al mismo POST de la pantalla, que
 * responde JSON. La pantalla no se recarga y queda lista para el proximo
 * carnet. Las reglas (hora, entrada/salida, doble escaneo) son del servidor.
 */
(function () {
  "use strict";

  var raiz = document.getElementById("fichaje-camara");
  if (!raiz || typeof window.jsQR !== "function") {
    return;
  }
  var formulario = document.getElementById("fichaje-form");
  var botonPrender = document.getElementById("camara-prender");
  var botonApagar = document.getElementById("camara-apagar");
  var marco = document.getElementById("camara-marco");
  var video = document.getElementById("camara-video");
  var aviso = document.getElementById("camara-aviso");
  var lienzo = document.createElement("canvas");
  var contexto = lienzo.getContext("2d", { willReadFrequently: true });
  var PATRON = /^GA1[A-Z2-7]{24}$/;
  var ANCHO_ANALISIS = 640;
  var PAUSA_DESPUES_DE_LEER = 2500;
  var MISMO_CODIGO_IGNORADO = 6000;
  var flujo = null;
  var temporizador = null;
  var enviando = false;
  var ultimoCodigo = "";
  var ultimoMomento = 0;

  function mostrarAviso(texto) {
    aviso.textContent = texto;
    aviso.hidden = !texto;
  }

  function mensajeDeError(error) {
    if (!window.isSecureContext) {
      return "La cámara solo funciona con la dirección segura (https).";
    }
    if (error && (error.name === "NotAllowedError" || error.name === "SecurityError")) {
      return "El navegador no dio permiso para usar la cámara. Habilitalo en los permisos del sitio y volvé a tocar «Usar la cámara».";
    }
    if (error && (error.name === "NotFoundError" || error.name === "OverconstrainedError")) {
      return "No se encontró una cámara en este equipo. Podés tipear el código del carnet.";
    }
    if (error && error.name === "NotReadableError") {
      return "La cámara está siendo usada por otra aplicación. Cerrala y volvé a intentar.";
    }
    return "No se pudo abrir la cámara. Podés tipear el código del carnet.";
  }

  function pintarResultado(datos) {
    var viejo = document.getElementById("resultado-fichaje");
    var seccion = document.createElement("section");
    seccion.id = "resultado-fichaje";
    seccion.className = "card mt-16";
    seccion.setAttribute("data-resultado", datos.resultado || "");
    var color = datos.registrada ? "#1f6b45" : datos.resultado === "repetida" ? "#8a5f12" : "#a83232";
    seccion.style.borderLeft = "6px solid " + color;
    if (datos.nombre) {
      var nombre = document.createElement("h2");
      nombre.style.fontSize = "1.8rem";
      nombre.style.margin = "0";
      nombre.textContent = datos.nombre;
      seccion.appendChild(nombre);
    }
    if (datos.registrada) {
      var tipo = document.createElement("p");
      tipo.style.fontSize = "2.4rem";
      tipo.style.fontWeight = "700";
      tipo.style.margin = "8px 0 0";
      tipo.textContent = datos.tipo + " · " + datos.hora;
      seccion.appendChild(tipo);
    }
    var mensaje = document.createElement("p");
    mensaje.className = (datos.registrada ? "muted" : "error") + " mt-8";
    mensaje.style.fontSize = "1.1rem";
    mensaje.textContent = datos.mensaje;
    seccion.appendChild(mensaje);
    if (viejo) {
      viejo.replaceWith(seccion);
    } else {
      raiz.parentNode.insertBefore(seccion, raiz);
    }
    if (datos.registrada && navigator.vibrate) {
      navigator.vibrate(120);
    }
  }

  function enviar(codigo) {
    enviando = true;
    var cuerpo = new FormData(formulario);
    cuerpo.set("codigo", codigo);
    fetch(formulario.action, {
      method: "POST",
      body: cuerpo,
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    })
      .then(function (respuesta) {
        if (!respuesta.ok) {
          throw new Error("HTTP " + respuesta.status);
        }
        return respuesta.json();
      })
      .then(pintarResultado)
      .catch(function () {
        pintarResultado({
          resultado: "error",
          registrada: false,
          mensaje: "No se pudo registrar: revisá la conexión y volvé a escanear.",
        });
      })
      .then(function () {
        window.setTimeout(function () {
          enviando = false;
        }, PAUSA_DESPUES_DE_LEER);
      });
  }

  function analizar() {
    if (!flujo) {
      return;
    }
    if (!enviando && video.readyState === video.HAVE_ENOUGH_DATA && video.videoWidth) {
      var escala = Math.min(1, ANCHO_ANALISIS / video.videoWidth);
      lienzo.width = Math.round(video.videoWidth * escala);
      lienzo.height = Math.round(video.videoHeight * escala);
      contexto.drawImage(video, 0, 0, lienzo.width, lienzo.height);
      var imagen = contexto.getImageData(0, 0, lienzo.width, lienzo.height);
      var leido = window.jsQR(imagen.data, imagen.width, imagen.height, { inversionAttempts: "dontInvert" });
      if (leido && leido.data) {
        var codigo = leido.data.replace(/\s+/g, "").toUpperCase();
        var ahora = Date.now();
        var repetido = codigo === ultimoCodigo && ahora - ultimoMomento < MISMO_CODIGO_IGNORADO;
        if (!repetido) {
          ultimoCodigo = codigo;
          ultimoMomento = ahora;
          if (PATRON.test(codigo)) {
            enviar(codigo);
          } else {
            pintarResultado({ resultado: "desconocido", registrada: false, mensaje: "Ese QR no es un carnet de asistencia." });
          }
        }
      }
    }
    temporizador = window.setTimeout(analizar, 150);
  }

  function apagar() {
    if (temporizador) {
      window.clearTimeout(temporizador);
      temporizador = null;
    }
    if (flujo) {
      flujo.getTracks().forEach(function (pista) {
        pista.stop();
      });
      flujo = null;
    }
    video.srcObject = null;
    marco.hidden = true;
    botonApagar.hidden = true;
    botonPrender.hidden = false;
  }

  function prender() {
    mostrarAviso("");
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      mostrarAviso(mensajeDeError(null));
      return;
    }
    navigator.mediaDevices
      .getUserMedia({ video: { facingMode: { ideal: "environment" } }, audio: false })
      .then(function (nuevoFlujo) {
        flujo = nuevoFlujo;
        video.srcObject = flujo;
        video.setAttribute("playsinline", "");
        video.muted = true;
        return video.play();
      })
      .then(function () {
        marco.hidden = false;
        botonPrender.hidden = true;
        botonApagar.hidden = false;
        analizar();
      })
      .catch(function (error) {
        apagar();
        mostrarAviso(mensajeDeError(error));
      });
  }

  botonPrender.addEventListener("click", prender);
  botonApagar.addEventListener("click", apagar);
  // Liberar la camara si el celular se bloquea o se cambia de pestana.
  document.addEventListener("visibilitychange", function () {
    if (document.hidden && flujo) {
      apagar();
    }
  });
  raiz.hidden = false;
})();

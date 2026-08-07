/* ============================================================
   Gerayse - conmutador de tema claro / oscuro.

   El tema YA quedo aplicado por el script inline de
   templates/partials/theme_boot.html antes del primer pintado.
   Este archivo solo se ocupa de lo que puede pasar despues:
   el click en el boton, la sincronizacion entre pestanas y el
   seguimiento del sistema operativo mientras el usuario no
   haya elegido nada.

   La preferencia vive en localStorage: queda atada al navegador
   de esta computadora, no al usuario. Es a proposito: el mismo
   operador puede querer claro en la PC del mostrador (con luz
   directa) y oscuro en la de administracion.
   ============================================================ */
(function () {
  "use strict";

  var KEY = "gerayse-theme";
  var root = document.documentElement;

  // Debe coincidir con --bg de cada tema en gerayse-tokens.css: es el color
  // que pinta la barra del navegador en mobile y el fondo del overscroll.
  var BROWSER_CHROME = { light: "#f4f1e9", dark: "#0f1315" };

  var LABEL = {
    light: { text: "Oscuro", title: "Cambiar a modo oscuro" },
    dark: { text: "Claro", title: "Cambiar a modo claro" }
  };

  function read() {
    try {
      var v = window.localStorage.getItem(KEY);
      return v === "light" || v === "dark" ? v : null;
    } catch (e) {
      return null;
    }
  }

  function write(theme) {
    try {
      window.localStorage.setItem(KEY, theme);
      return true;
    } catch (e) {
      // Modo privado o storage bloqueado por politica: el tema igual cambia,
      // pero solo hasta que se recargue. No rompemos nada por esto.
      return false;
    }
  }

  function apply(theme, persist) {
    root.setAttribute("data-theme", theme);

    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) {
      meta.setAttribute("content", BROWSER_CHROME[theme] || BROWSER_CHROME.dark);
    }

    var copy = LABEL[theme] || LABEL.dark;
    var buttons = document.querySelectorAll("[data-theme-toggle]");
    for (var i = 0; i < buttons.length; i++) {
      var btn = buttons[i];
      // aria-pressed describe "modo claro activo", que es el estado que el
      // boton conmuta. El nombre accesible lo da el title.
      btn.setAttribute("aria-pressed", theme === "light" ? "true" : "false");
      btn.setAttribute("title", copy.title);
      btn.setAttribute("aria-label", copy.title);
      var label = btn.querySelector("[data-theme-label]");
      if (label) {
        label.textContent = copy.text;
      }
    }

    if (persist) {
      write(theme);
    }
  }

  function current() {
    return root.getAttribute("data-theme") === "light" ? "light" : "dark";
  }

  // Sincroniza el markup con el tema que ya aplico theme_boot.html.
  apply(current(), false);

  document.addEventListener("click", function (ev) {
    var target = ev.target;
    if (!target || typeof target.closest !== "function") {
      return;
    }
    var btn = target.closest("[data-theme-toggle]");
    if (!btn) {
      return;
    }
    ev.preventDefault();
    apply(current() === "light" ? "dark" : "light", true);
  });

  // Otra pestana del mismo navegador cambio el tema: la seguimos.
  window.addEventListener("storage", function (ev) {
    if (ev.key === KEY && (ev.newValue === "light" || ev.newValue === "dark")) {
      apply(ev.newValue, false);
    }
  });

  // Mientras el usuario no haya elegido explicitamente, seguimos al sistema
  // operativo en vivo (Windows cambia solo al anochecer con "hora oscura").
  if (window.matchMedia) {
    var mq = window.matchMedia("(prefers-color-scheme: light)");
    var follow = function (ev) {
      if (read() === null) {
        apply(ev.matches ? "light" : "dark", false);
      }
    };
    if (mq.addEventListener) {
      mq.addEventListener("change", follow);
    } else if (mq.addListener) {
      mq.addListener(follow); // Safari < 14
    }
  }
})();

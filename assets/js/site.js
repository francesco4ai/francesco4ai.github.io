(function () {
  "use strict";

  var root = document.documentElement;

  /* Theme: follows the system until the visitor picks one. */
  var themeButton = document.querySelector(".theme");
  function currentTheme() {
    var set = root.getAttribute("data-theme");
    if (set) return set;
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  function labelTheme() {
    if (!themeButton) return;
    var next = currentTheme() === "dark" ? "light" : "dark";
    themeButton.setAttribute("aria-label", "Switch to " + next + " theme");
    themeButton.setAttribute("title", "Switch to " + next + " theme");
  }
  if (themeButton) {
    themeButton.addEventListener("click", function () {
      var next = currentTheme() === "dark" ? "light" : "dark";
      root.setAttribute("data-theme", next);
      try { localStorage.setItem("theme", next); } catch (e) {}
      labelTheme();
    });
    labelTheme();
  }

  /* Top bar: show the name and a hairline once the hero has scrolled away. */
  var bar = document.querySelector(".topbar");
  var hero = document.querySelector(".hero h1");
  if (bar && hero && "IntersectionObserver" in window) {
    new IntersectionObserver(function (entries) {
      bar.classList.toggle("is-stuck", !entries[0].isIntersecting);
    }, { rootMargin: "-64px 0px 0px 0px" }).observe(hero);
  }

  /* Publications: filter by kind. */
  var filter = document.querySelector(".pub-filter");
  if (filter) {
    filter.hidden = false;
    var buttons = filter.querySelectorAll("button");
    filter.addEventListener("click", function (event) {
      var button = event.target.closest("button");
      if (!button) return;
      var kind = button.getAttribute("data-filter");
      buttons.forEach(function (b) {
        b.setAttribute("aria-pressed", b === button ? "true" : "false");
      });
      document.querySelectorAll(".pub-year").forEach(function (year) {
        var visible = 0;
        year.querySelectorAll(".pub").forEach(function (pub) {
          var show = kind === "all" || pub.getAttribute("data-kind") === kind;
          pub.hidden = !show;
          if (show) visible++;
        });
        year.hidden = visible === 0;
      });
    });
  }

  /* Publications: copy BibTeX, or reveal it if the clipboard is unavailable. */
  document.querySelectorAll(".pub").forEach(function (pub) {
    var button = pub.querySelector(".pub-cite");
    var bib = pub.querySelector(".pub-bib");
    if (!button || !bib) return;
    button.hidden = false;
    button.addEventListener("click", function () {
      var text = bib.textContent;
      var done = function () {
        button.textContent = "BibTeX copied";
        button.classList.add("is-done");
        setTimeout(function () {
          button.textContent = "Copy BibTeX";
          button.classList.remove("is-done");
        }, 2000);
      };
      var reveal = function () {
        bib.hidden = !bib.hidden;
        button.textContent = bib.hidden ? "Show BibTeX" : "Hide BibTeX";
      };
      if (navigator.clipboard && window.isSecureContext) {
        navigator.clipboard.writeText(text).then(done, reveal);
      } else {
        reveal();
      }
    });
  });
})();

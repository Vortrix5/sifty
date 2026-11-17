/* Sifty one-pager. Vanilla JS: hero typing animation, tabs, copy buttons, reveal. */
(function () {
  "use strict";

  var PROMPT = '<span class="c-p">PS C:\\&gt;</span> ';
  var reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* ---------- copy buttons ---------- */
  document.querySelectorAll(".copy-btn").forEach(function (btn) {
    btn.addEventListener("click", function () {
      navigator.clipboard.writeText(btn.dataset.copy).then(function () {
        btn.classList.add("copied");
        btn.textContent = "copied!";
        setTimeout(function () {
          btn.classList.remove("copied");
          btn.textContent = "copy";
        }, 1600);
      });
    });
  });

  /* ---------- demo tabs ---------- */
  var tabs = document.querySelectorAll(".tab");
  tabs.forEach(function (tab) {
    tab.addEventListener("click", function () {
      tabs.forEach(function (t) {
        t.classList.toggle("is-active", t === tab);
        t.setAttribute("aria-selected", t === tab ? "true" : "false");
      });
      document.querySelectorAll("[data-panel]").forEach(function (panel) {
        panel.hidden = panel.dataset.panel !== tab.dataset.tab;
      });
    });
  });

  /* ---------- reveal on scroll ---------- */
  var revealEls = document.querySelectorAll(".reveal");
  if ("IntersectionObserver" in window && !reducedMotion) {
    var io = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (e) {
          if (e.isIntersecting) {
            e.target.classList.add("is-visible");
            io.unobserve(e.target);
          }
        });
      },
      { rootMargin: "0px 0px -8% 0px" }
    );
    revealEls.forEach(function (el) { io.observe(el); });
  } else {
    revealEls.forEach(function (el) { el.classList.add("is-visible"); });
  }

  /* ---------- hero terminal animation ---------- */
  var term = document.getElementById("heroTerm");
  var script = document.getElementById("heroScript");
  if (!term || !script || reducedMotion) return;

  var steps = Array.prototype.slice.call(script.content.children);
  var stopped = false;

  function scrollDown() {
    term.scrollTop = term.scrollHeight;
  }

  function wait(ms) {
    return new Promise(function (res) { setTimeout(res, ms); });
  }

  /* Type `text` into `el` character by character, with a caret. */
  function typeInto(el, text, speed) {
    return new Promise(function (res) {
      var caret = document.createElement("span");
      caret.className = "type-caret";
      el.appendChild(caret);
      var i = 0;
      (function tick() {
        if (stopped) return res();
        if (i < text.length) {
          caret.insertAdjacentText("beforebegin", text.charAt(i));
          i += 1;
          scrollDown();
          setTimeout(tick, speed + Math.random() * 34);
        } else {
          caret.remove();
          res();
        }
      })();
    });
  }

  function runStep(step) {
    var kind = step.dataset.kind;

    if (kind === "pause") {
      return wait(parseInt(step.dataset.ms || "1000", 10));
    }

    if (kind === "cmd") {
      var line = document.createElement("div");
      line.innerHTML = PROMPT;
      term.appendChild(line);
      scrollDown();
      return wait(420).then(function () {
        return typeInto(line, step.textContent, 26);
      }).then(function () { return wait(260); });
    }

    if (kind === "ask") {
      var ask = document.createElement("div");
      ask.textContent = step.textContent;
      term.appendChild(ask);
      scrollDown();
      return wait(700).then(function () {
        return typeInto(ask, "y", 0);
      }).then(function () { return wait(350); });
    }

    /* kind === "out": append the block instantly, like real output. Some
       steps carry a desktop and a mobile variant; CSS shows the right one. */
    Array.prototype.forEach.call(step.children, function (child) {
      term.appendChild(child.cloneNode(true));
    });
    scrollDown();
    return wait(140);
  }

  function runLoop() {
    term.innerHTML = "";
    var chain = Promise.resolve();
    steps.forEach(function (step) {
      chain = chain.then(function () {
        if (!stopped) return runStep(step);
      });
    });
    chain.then(function () {
      return wait(9000);
    }).then(function () {
      if (!stopped) runLoop();
    });
  }

  /* Pause the loop when the tab is hidden to save cycles. */
  document.addEventListener("visibilitychange", function () {
    stopped = document.hidden;
    if (!document.hidden) runLoop();
  });

  runLoop();
})();

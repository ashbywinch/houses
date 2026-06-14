(function () {
  "use strict";

  var toggle = document.getElementById("dismissed-toggle");
  var list = document.getElementById("dismissed-list");
  if (!toggle || !list) return;

  toggle.addEventListener("click", function () {
    var visible = toggle.getAttribute("data-visible") === "true";
    if (visible) {
      list.hidden = true;
      toggle.textContent = "Show dismissed (" + toggle.getAttribute("data-count") + ")";
      toggle.setAttribute("data-visible", "false");
    } else {
      list.hidden = false;
      toggle.textContent = "Hide dismissed";
      toggle.setAttribute("data-visible", "true");
    }
  });

  // Store count from button text
  var match = toggle.textContent.match(/\d+/);
  if (match) {
    toggle.setAttribute("data-count", match[0]);
  }
})();

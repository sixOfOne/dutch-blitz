(function () {
  const addButton = document.querySelector("[data-add-player]");
  if (addButton) {
    const reveal = function () {
      const hidden = document.querySelector(".name-field.extra[hidden]");
      if (!hidden) {
        addButton.hidden = true;
        return;
      }
      hidden.hidden = false;
      const input = hidden.querySelector("input");
      if (input) {
        input.focus();
      }
      if (!document.querySelector(".name-field.extra[hidden]")) {
        addButton.hidden = true;
      }
    };
    addButton.addEventListener("click", reveal);
    if (!document.querySelector(".name-field.extra[hidden]")) {
      addButton.hidden = true;
    }
  }

  const renderScore = function (score) {
    if (score > 0) {
      return "+" + score;
    }
    if (score < 0) {
      return "\u2212" + Math.abs(score);
    }
    return "0";
  };

  document.querySelectorAll("[data-score-row]").forEach(function (row) {
    const dutch = row.querySelector("[data-dutch]");
    const blitz = row.querySelector("[data-blitz]");
    const out = row.querySelector("[data-preview]");
    if (!dutch || !blitz || !out) {
      return;
    }
    const update = function () {
      const dutchText = dutch.value.trim();
      const blitzText = blitz.value.trim();
      if (!/^[0-9]+$/.test(dutchText) || !/^[0-9]+$/.test(blitzText)) {
        out.textContent = "\u2014";
        out.className = "preview";
        return;
      }
      const score = Number(dutchText) - 2 * Number(blitzText);
      out.textContent = renderScore(score);
      out.className = "preview" + (score > 0 ? " pos" : score < 0 ? " neg" : "");
    };
    dutch.addEventListener("input", update);
    blitz.addEventListener("input", update);
    update();
  });

  document.querySelectorAll('input[type="number"]').forEach(function (input) {
    input.addEventListener("wheel", function (event) {
      event.preventDefault();
    }, { passive: false });
  });
})();

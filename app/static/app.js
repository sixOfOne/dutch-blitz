(function () {
  const addButton = document.querySelector("[data-add-player]");
  const deckForm = document.querySelector("#new-game form");

  const checkedIn = function (card, prefix) {
    return card.querySelector('input[type="radio"][name^="' + prefix + '"]:checked');
  };

  const labelOf = function (input) {
    if (!input) {
      return "";
    }
    const face = input.parentElement && input.parentElement.querySelector(".choice-text");
    return face ? face.textContent.trim() : "";
  };

  const cardCounts = function (card) {
    if (!card.hidden) {
      return true;
    }
    const name = card.querySelector('input[name="player_name"]');
    return Boolean(name && name.value.trim());
  };

  const takenColors = function (except) {
    const taken = new Set();
    document.querySelectorAll("[data-player-card]").forEach(function (card) {
      if (card === except || !cardCounts(card)) {
        return;
      }
      const color = checkedIn(card, "player_color_");
      if (color) {
        taken.add(color.value);
      }
    });
    return taken;
  };

  const applyDefaultIcon = function (card, colorInput) {
    const iconId = colorInput.getAttribute("data-default-icon");
    if (!iconId || !window.CSS || !CSS.escape) {
      return;
    }
    const icon = card.querySelector(
      'input[name^="player_icon_"][value="' + CSS.escape(iconId) + '"]'
    );
    if (icon) {
      icon.checked = true;
    }
  };

  const updateSummary = function (card) {
    const summary = card.querySelector("[data-deck-summary]");
    if (!summary) {
      return;
    }
    const parts = [];
    const colorText = labelOf(checkedIn(card, "player_color_"));
    const iconText = labelOf(checkedIn(card, "player_icon_"));
    if (colorText) {
      parts.push(colorText);
    }
    if (iconText) {
      parts.push(iconText);
    }
    summary.textContent = parts.length ? parts.join(", ") : "No deck yet";
  };

  const refreshDeckWarning = function () {
    const groups = new Map();
    document.querySelectorAll("[data-player-card]").forEach(function (card) {
      card.classList.remove("deck-clash");
      if (!cardCounts(card)) {
        return;
      }
      const color = checkedIn(card, "player_color_");
      if (!color) {
        return;
      }
      const list = groups.get(color.value) || [];
      list.push(card);
      groups.set(color.value, list);
    });
    let message = "";
    groups.forEach(function (list) {
      if (list.length < 2) {
        return;
      }
      list.forEach(function (card) {
        card.classList.add("deck-clash");
      });
      if (!message) {
        const label = labelOf(checkedIn(list[0], "player_color_"));
        message = label + " is already chosen. Each player needs a different deck color.";
      }
    });
    const warning = document.querySelector("[data-deck-warning]");
    if (warning) {
      warning.hidden = !message;
      warning.textContent = message;
    }
    if (deckForm) {
      const submit = deckForm.querySelector('button[type="submit"]');
      if (submit) {
        submit.disabled = Boolean(message);
      }
    }
  };

  document.querySelectorAll("[data-player-card]").forEach(function (card) {
    updateSummary(card);
  });
  refreshDeckWarning();

  if (deckForm) {
    deckForm.addEventListener("change", function (event) {
      const input = event.target;
      if (!(input instanceof HTMLInputElement)) {
        return;
      }
      const card = input.closest("[data-player-card]");
      if (!card) {
        return;
      }
      if (input.name.indexOf("player_color_") === 0) {
        applyDefaultIcon(card, input);
      }
      updateSummary(card);
      refreshDeckWarning();
    });
    deckForm.addEventListener("input", function (event) {
      const input = event.target;
      if (input instanceof HTMLInputElement && input.name === "player_name") {
        refreshDeckWarning();
      }
    });
  }

  if (addButton) {
    const reveal = function () {
      const hidden = document.querySelector(".player-card.extra[hidden]");
      if (!hidden) {
        addButton.hidden = true;
        return;
      }
      hidden.hidden = false;
      const taken = takenColors(hidden);
      let color = checkedIn(hidden, "player_color_");
      if (!color || taken.has(color.value)) {
        const options = hidden.querySelectorAll('input[name^="player_color_"]');
        for (let index = 0; index < options.length; index += 1) {
          if (!taken.has(options[index].value)) {
            options[index].checked = true;
            applyDefaultIcon(hidden, options[index]);
            break;
          }
        }
      }
      updateSummary(hidden);
      refreshDeckWarning();
      const input = hidden.querySelector('input[name="player_name"]');
      if (input) {
        input.focus();
      }
      if (!document.querySelector(".player-card.extra[hidden]")) {
        addButton.hidden = true;
      }
    };
    addButton.addEventListener("click", reveal);
    if (!document.querySelector(".player-card.extra[hidden]")) {
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

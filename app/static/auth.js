(function () {
  var config = window.__DUTCH_BLITZ_CLERK__ || {};
  var COOKIE = "__session";

  function writeCookie(token) {
    var secure = location.protocol === "https:" ? "; Secure" : "";
    if (!token) {
      document.cookie = COOKIE + "=; Max-Age=0; Path=/; SameSite=Lax" + secure;
      return;
    }
    document.cookie = COOKIE + "=" + encodeURIComponent(token) + "; Max-Age=60; Path=/; SameSite=Lax" + secure;
  }

  function refresh(clerk) {
    if (!clerk || !clerk.session) {
      return Promise.resolve(null);
    }
    return clerk.session.getToken().then(function (token) {
      if (token) {
        writeCookie(token);
      }
      return token;
    });
  }

  document.addEventListener("submit", function (event) {
    var form = event.target;
    var clerk = window.Clerk;
    if (!(form instanceof HTMLFormElement) || !clerk || !clerk.session) {
      return;
    }
    event.preventDefault();
    refresh(clerk).finally(function () {
      form.submit();
    });
  }, true);

  document.addEventListener("click", function (event) {
    var clerk = window.Clerk;
    if (!clerk || !clerk.session || event.defaultPrevented || event.button !== 0) {
      return;
    }
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
      return;
    }
    var link = event.target.closest("a[href]");
    if (!link || link.target === "_blank" || link.hasAttribute("download")) {
      return;
    }
    if (link.closest("[data-clerk-user-button], [data-clerk-sign-in]")) {
      return;
    }
    var url;
    try {
      url = new URL(link.href, location.href);
    } catch (error) {
      return;
    }
    if (url.origin !== location.origin) {
      return;
    }
    event.preventDefault();
    refresh(clerk).finally(function () {
      location.href = link.href;
    });
  }, true);

  if (!config.ready || !config.publishableKey || !config.frontendApi) {
    return;
  }

  function boot() {
    var clerk = window.Clerk;
    if (!clerk || !clerk.load) {
      return;
    }
    var here = location.pathname + location.search || "/";
    clerk.load().then(function () {
      var signedOut = document.body.hasAttribute("data-signed-out");
      if (clerk.session) {
        return refresh(clerk).then(function () {
          if (signedOut && !sessionStorage.getItem("clerk-reload")) {
            sessionStorage.setItem("clerk-reload", "1");
            location.reload();
            return;
          }
          if (!signedOut) {
            sessionStorage.removeItem("clerk-reload");
          }
          mount(clerk, here, signedOut);
        });
      }
      sessionStorage.removeItem("clerk-reload");
      mount(clerk, here, true);
    }).catch(function () {
      /* Leave the sign-in message on the page if Clerk cannot load. */
    });
  }

  function mount(clerk, here, signedOut) {
    var signIn = document.querySelector("[data-clerk-sign-in]");
    if (signedOut && signIn && clerk.mountSignIn) {
      clerk.mountSignIn(signIn, {
        afterSignInUrl: here,
        afterSignUpUrl: here,
        fallbackRedirectUrl: here,
        forceRedirectUrl: here
      });
    }
    var button = document.querySelector("[data-clerk-user-button]");
    if (button && clerk.user && clerk.mountUserButton) {
      clerk.mountUserButton(button);
    }
    if (clerk.addListener) {
      clerk.addListener(function (emission) {
        var signedOutNow = !emission.user && !(emission.session);
        if (!signedOutNow) {
          return;
        }
        writeCookie("");
        if (!document.body.hasAttribute("data-signed-out")) {
          location.href = "/";
        }
      });
    }
  }

  var script = document.createElement("script");
  script.async = true;
  script.crossOrigin = "anonymous";
  script.dataset.clerkPublishableKey = config.publishableKey;
  script.src = "https://" + config.frontendApi + "/npm/@clerk/clerk-js@5/dist/clerk.browser.js";
  script.onload = boot;
  script.onerror = function () {
    var fallback = document.createElement("script");
    fallback.async = true;
    fallback.crossOrigin = "anonymous";
    fallback.dataset.clerkPublishableKey = config.publishableKey;
    fallback.src = "https://cdn.jsdelivr.net/npm/@clerk/clerk-js@5/dist/clerk.browser.js";
    fallback.onload = boot;
    document.head.appendChild(fallback);
  };
  document.head.appendChild(script);
})();

/* docker/ctfd/plugins/instance-launcher/assets/challenge-launch.js
 *
 * Injects a live "Launch Environment" control into CTFd's own challenge
 * modal, instead of requiring a player to find/follow a separate link.
 *
 * Why a MutationObserver, not a CTFd/Alpine event hook: confirmed by
 * reading the live theme's compiled JS and challenge.html template (not
 * guessed) that CTFd's core theme (Alpine.js) renders the ENTIRE modal
 * body via `x-html="$store.challenge.data.view"` on `#challenge-window`
 * -- the whole subtree is replaced wholesale every time a challenge is
 * opened, including re-opening the same challenge. There's a `load-
 * challenge` window event, but the most common path (clicking a challenge
 * button on the board) calls the Alpine component method directly and
 * never dispatches it, so it isn't a reliable hook. A MutationObserver on
 * the always-present `#challenge-window` container is the only mechanism
 * that reliably fires on every single render, regardless of path.
 *
 * `#challenge-id` (a hidden input CTFd itself renders inside every
 * challenge's body) is used as the per-render anchor/id source instead of
 * reaching into `Alpine.store('challenge').data` directly, so this stays
 * decoupled from Alpine internals -- only the DOM contract matters.
 *
 * CSRF: uses `CTFd.fetch()` (confirmed via the live theme bundle: it's
 * CTFd's own fetch wrapper, automatically attaches the same `CSRF-Token`
 * header CTFd's bundled frontend sends on every authenticated request) so
 * this doesn't need to scrape a nonce itself.
 */
(function () {
  "use strict";

  var PANEL_ID = "instance-launcher-panel";
  var POLL_INTERVAL_MS = 3000;
  var POLL_MAX_ATTEMPTS = 15; // ~45s of polling while access info is missing

  function escapeHtml(str) {
    var div = document.createElement("div");
    div.textContent = String(str == null ? "" : str);
    return div.innerHTML;
  }

  function escapeAttr(str) {
    return escapeHtml(str).replace(/"/g, "&quot;");
  }

  function renderAccess(access) {
    if (!access) return "";
    var html = "";

    if (access.url) {
      html +=
        '<p class="mb-2"><a class="btn btn-primary btn-sm" href="' +
        escapeAttr(access.url) +
        '" target="_blank" rel="noopener">Open Environment</a></p>';
    }

    if (access.attacker_url) {
      html +=
        '<p class="mb-2"><a class="btn btn-primary btn-sm" href="' +
        escapeAttr(access.attacker_url) +
        '" target="_blank" rel="noopener">Open Attacker Workstation</a></p>';
      if (access.novnc_url) {
        html +=
          '<p class="mb-2"><a class="btn btn-outline-primary btn-sm" href="' +
          escapeAttr(access.novnc_url) +
          '" target="_blank" rel="noopener">Open Attacker Workstation (direct, no DNS)</a></p>';
        if (access.novnc_note) {
          html += '<p class="text-muted mb-1"><small>' + escapeHtml(access.novnc_note) + "</small></p>";
        }
      }
      if (access.target_hostname) {
        html +=
          '<p class="mb-1">Your target is reachable only from inside that workstation, at hostname:<br><code>' +
          escapeHtml(access.target_hostname) +
          "</code></p>";
        if (access.target_note) {
          html +=
            '<p class="text-muted mb-1"><small>' + escapeHtml(access.target_note) + "</small></p>";
        }
      }
    }

    if (access.connect_port) {
      var protocol = access.protocol || "ssh";
      html += '<p class="mb-1">Connect via ' + escapeHtml(protocol.toUpperCase()) + ":</p>";
      html +=
        '<pre class="mb-1">' +
        escapeHtml(protocol) +
        " operator@" +
        escapeHtml(access.connect_host) +
        " -p " +
        escapeHtml(access.connect_port) +
        "</pre>";
      if (access.note) {
        html += '<p class="text-muted mb-1"><small>' + escapeHtml(access.note) + "</small></p>";
      }
    }

    return html;
  }

  function renderShutdownCountdown(panel, shutdownAt, challengeId) {
    var el = panel.querySelector(".instance-launcher-countdown");
    if (!el) return;
    var shutdownAtMs = shutdownAt * 1000;

    function tick() {
      // Panel may have been torn down (challenge modal closed/changed) --
      // stop touching detached DOM.
      if (!document.body.contains(el)) return;
      var remaining = Math.max(0, Math.round((shutdownAtMs - Date.now()) / 1000));
      el.textContent = remaining + "s";
      if (remaining <= 0) {
        loadStatus(panel, challengeId);
      } else {
        setTimeout(tick, 1000);
      }
    }
    tick();
  }

  function renderPanel(panel, challengeId, data) {
    panel.dataset.challengeId = String(challengeId);

    if (!data || !data.has_environment) {
      panel.style.display = "none";
      panel.innerHTML = "";
      return;
    }

    panel.style.display = "";
    panel.dataset.instanceGroup = data.instance_group || "";

    var html = "";
    var status = data.status;

    if (data.error) {
      html += '<p class="text-danger mb-2"><small>Could not reach your environment: ' + escapeHtml(data.error) + "</small></p>";
    }

    if (status && status.access) {
      html += renderAccess(status.access);

      if (data.instance_group) {
        html +=
          '<p class="text-muted mb-2"><small>Shared environment: <code>' +
          escapeHtml(data.instance_group) +
          "</code> — launching from any level in this track reuses the same box.</small></p>";
      }

      if (status.shutdown_at) {
        html +=
          '<p class="text-warning mb-2">Environment shuts down in <span class="instance-launcher-countdown">--</span>. ' +
          '<button type="button" class="btn btn-outline-secondary btn-sm" data-action="extend">+5 more minutes</button></p>';
      }

      html +=
        '<div class="mt-2">' +
        '<button type="button" class="btn btn-outline-secondary btn-sm me-1" data-action="reboot">Reboot Host</button>' +
        '<button type="button" class="btn btn-outline-warning btn-sm" data-action="relaunch" data-confirm="This destroys and recreates your environment from scratch. Continue?">Relaunch Environment</button>' +
        "</div>";
    } else {
      html +=
        '<p class="mb-2 text-muted"><small><i class="fas fa-circle-notch fa-spin"></i> Starting your environment&hellip; this can take up to a minute for larger images.</small></p>' +
        '<button type="button" class="btn btn-outline-secondary btn-sm" data-action="launch">Check again</button>';
    }

    panel.innerHTML = html;

    panel.querySelectorAll("[data-action]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var confirmMsg = btn.getAttribute("data-confirm");
        if (confirmMsg && !window.confirm(confirmMsg)) return;
        runAction(panel, challengeId, btn.dataset.action);
      });
    });

    if (status && status.shutdown_at) {
      renderShutdownCountdown(panel, status.shutdown_at, challengeId);
    }
  }

  function pollWhileStarting(panel, challengeId, attemptsLeft) {
    if (!document.body.contains(panel)) return; // modal moved on, stop
    if (panel.dataset.challengeId !== String(challengeId)) return; // superseded by a new render
    if (attemptsLeft <= 0) return;

    setTimeout(function () {
      if (!document.body.contains(panel) || panel.dataset.challengeId !== String(challengeId)) return;
      loadStatus(panel, challengeId, attemptsLeft - 1);
    }, POLL_INTERVAL_MS);
  }

  function loadStatus(panel, challengeId, attemptsLeft) {
    if (attemptsLeft === undefined) attemptsLeft = POLL_MAX_ATTEMPTS;

    return window
      .CTFd.fetch("/plugins/instance-launcher/api/status/" + challengeId)
      .then(function (resp) {
        return resp.json();
      })
      .then(function (data) {
        if (!document.body.contains(panel)) return data; // modal moved on mid-request
        renderPanel(panel, challengeId, data);
        if (data && data.has_environment && (!data.status || !data.status.access)) {
          pollWhileStarting(panel, challengeId, attemptsLeft);
        }
        return data;
      })
      .catch(function () {
        panel.innerHTML = '<p class="text-danger mb-0"><small>Could not reach environment status.</small></p>';
      });
  }

  function runAction(panel, challengeId, action) {
    panel.innerHTML =
      '<p class="mb-0 text-muted"><small><i class="fas fa-circle-notch fa-spin"></i> Working&hellip;</small></p>';

    return window
      .CTFd.fetch("/plugins/instance-launcher/api/launch/" + challengeId, {
        method: "POST",
        body: JSON.stringify({ action: action === "launch" ? null : action }),
      })
      .then(function (resp) {
        return resp.json();
      })
      .then(function (data) {
        if (!data.success) {
          panel.innerHTML = '<p class="text-danger mb-2"><small>' + escapeHtml(data.error) + "</small></p>";
          var retry = document.createElement("button");
          retry.type = "button";
          retry.className = "btn btn-outline-secondary btn-sm";
          retry.textContent = "Retry";
          retry.addEventListener("click", function () {
            loadStatus(panel, challengeId);
          });
          panel.appendChild(retry);
          return;
        }
        renderPanel(panel, challengeId, {
          has_environment: true,
          instance_group: panel.dataset.instanceGroup,
          status: data.status,
        });
        if (!data.status || !data.status.access) {
          pollWhileStarting(panel, challengeId, POLL_MAX_ATTEMPTS);
        }
      })
      .catch(function () {
        panel.innerHTML = '<p class="text-danger mb-0"><small>Request failed.</small></p>';
      });
  }

  function injectPanel(modal, challengeId) {
    var desc = modal.querySelector(".challenge-desc");
    if (!desc) return null;

    var panel = document.createElement("div");
    panel.id = PANEL_ID;
    panel.className = "instance-launcher-panel mb-3 mt-3 p-3 border rounded";
    panel.style.display = "none";
    panel.innerHTML =
      '<p class="mb-0 text-muted"><small><i class="fas fa-circle-notch fa-spin"></i> Checking environment&hellip;</small></p>';
    desc.insertAdjacentElement("afterend", panel);
    return panel;
  }

  function handleModalChange() {
    var modal = document.getElementById("challenge-window");
    if (!modal) return;

    var idInput = modal.querySelector("#challenge-id");
    if (!idInput || !idInput.value) return;
    var challengeId = idInput.value;

    var existing = modal.querySelector("#" + PANEL_ID);
    if (existing && existing.dataset.challengeId === String(challengeId)) {
      return; // already injected for this exact render
    }

    var panel = injectPanel(modal, challengeId);
    if (!panel) return;
    panel.dataset.challengeId = String(challengeId);

    loadStatus(panel, challengeId);
  }

  document.addEventListener("DOMContentLoaded", function () {
    if (!window.CTFd || typeof window.CTFd.fetch !== "function") return;

    var modal = document.getElementById("challenge-window");
    if (!modal) return; // not on the challenge board page

    var observer = new MutationObserver(function () {
      handleModalChange();
    });
    observer.observe(modal, { childList: true, subtree: false });
  });
})();

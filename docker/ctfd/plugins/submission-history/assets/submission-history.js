/* docker/ctfd/plugins/submission-history/assets/submission-history.js
 *
 * Closes the gap tracked in cei-labs-event#6: adds a "Show my submitted
 * flag" toggle to CTFd's own challenge modal, backed by routes.py's
 * GET /api/solve/<challenge_id>. Deliberately an explicit, on-demand
 * reveal -- the flag text is never fetched or displayed until a player
 * expands the toggle, even for a challenge they've already solved (per the
 * scoping decision in cei-labs-event's docs/view-submitted-flag-gap-*.md:
 * some organizers want this hidden by default to discourage flag-sharing
 * screenshots during a live event, so nothing here auto-reveals on open).
 *
 * Deliberately inline, not a popup/new page: this is a plain expand/collapse
 * toggle rendered directly inside the existing challenge modal, showing only
 * the answer for the specific question currently open -- no separate window,
 * no new browser tab. (An earlier version also linked out to a standalone
 * "view all my submitted flags" page in a new tab; that's been dropped here
 * so the ONLY way to see a submitted flag is this inline toggle, scoped to
 * whichever challenge you're already looking at. The /solves and /api/solves
 * routes still exist server-side as a direct-URL fallback, just with no link
 * to them from the modal.)
 *
 * Modeled structurally on ../../hint-wallet/assets/hint-wallet.js and
 * ../../instance-launcher/assets/challenge-launch.js: same
 * MutationObserver-on-"#challenge-window" pattern (see challenge-launch.js's
 * header comment for why -- Alpine replaces the modal's entire subtree via
 * x-html on every open, including re-opening the same challenge, so a
 * MutationObserver is the only reliable hook), same `#challenge-id`
 * hidden-input-as-anchor convention, same `CTFd.fetch()` CSRF handling,
 * same inline-Bootstrap-classes styling.
 *
 * This panel does not try to know up front whether the current challenge
 * has been solved by this account -- expanding the toggle calls the API
 * directly and handles a 404 ("not solved by you") as a normal, expected
 * outcome, rather than making an extra fetch on every single modal open
 * just to decide whether to show the toggle. The result is cached per
 * challenge for the lifetime of the panel, so collapsing and re-expanding
 * doesn't re-fetch.
 */
(function () {
  "use strict";

  var PANEL_ID = "submission-history-panel";

  function escapeHtml(str) {
    var div = document.createElement("div");
    div.textContent = str === null || str === undefined ? "" : String(str);
    return div.innerHTML;
  }

  function setBody(panel, html) {
    panel.querySelector(".submission-history-body").innerHTML = html;
  }

  function fetchAndRender(panel, challengeId) {
    setBody(panel, '<p class="mb-0 text-muted"><small>Checking…</small></p>');

    window.CTFd.fetch("/plugins/submission-history/api/solve/" + challengeId)
      .then(function (resp) {
        return resp.json().then(function (body) {
          return { ok: resp.ok, status: resp.status, body: body };
        });
      })
      .then(function (result) {
        if (!document.body.contains(panel)) return; // modal moved on mid-request

        if (result.status === 404) {
          panel.dataset.loaded = "true";
          setBody(panel, '<p class="mb-0 text-muted"><small>You haven\'t solved this challenge yet.</small></p>');
          return;
        }
        if (!result.ok) {
          panel.dataset.loaded = ""; // allow retry on next expand
          setBody(panel, '<p class="text-danger mb-0"><small>Could not load your submitted flag.</small></p>');
          return;
        }

        panel.dataset.loaded = "true";
        setBody(panel, '<p class="mb-0">Your submitted flag: <code>' + escapeHtml(result.body.provided) + "</code></p>");
      })
      .catch(function () {
        if (!document.body.contains(panel)) return;
        panel.dataset.loaded = ""; // allow retry on next expand
        setBody(panel, '<p class="text-danger mb-0"><small>Request failed.</small></p>');
      });
  }

  function injectPanel(modal, challengeId) {
    var desc = modal.querySelector(".challenge-desc");
    if (!desc) return null;

    // Sits after instance-launcher's/hint-wallet's own panels when present,
    // so the toggle is the last thing before the flag-submission form --
    // falls back to right after the description when neither is there.
    var anchor =
      modal.querySelector("#hint-wallet-panel") || modal.querySelector("#instance-launcher-panel") || desc;

    var panel = document.createElement("div");
    panel.id = PANEL_ID;
    panel.className = "submission-history-panel mb-3 mt-3";
    // A plain expand/collapse toggle, inline in the modal -- not a link, not
    // a new page, not a second popup. Chevron flips via CSS on aria-expanded
    // so no extra JS is needed to keep the icon in sync with the state.
    panel.innerHTML =
      '<a href="#" class="submission-history-toggle" data-action="toggle" aria-expanded="false">' +
      '<small>Show my submitted flag <span class="submission-history-chevron">&#9656;</span></small>' +
      "</a>" +
      '<div class="submission-history-body mt-1" style="display: none;"></div>';
    anchor.insertAdjacentElement("afterend", panel);

    panel.querySelector('[data-action="toggle"]').addEventListener("click", function (evt) {
      evt.preventDefault();
      var link = evt.currentTarget;
      var body = panel.querySelector(".submission-history-body");
      var expanded = link.getAttribute("aria-expanded") === "true";

      var chevron = link.querySelector(".submission-history-chevron");

      if (expanded) {
        link.setAttribute("aria-expanded", "false");
        body.style.display = "none";
        if (chevron) chevron.innerHTML = "&#9656;"; // ▶ collapsed
        return;
      }

      link.setAttribute("aria-expanded", "true");
      body.style.display = "";
      if (chevron) chevron.innerHTML = "&#9662;"; // ▼ expanded
      if (panel.dataset.loaded !== "true") {
        fetchAndRender(panel, challengeId);
      }
    });

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

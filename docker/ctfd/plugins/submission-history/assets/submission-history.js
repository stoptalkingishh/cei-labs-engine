/* docker/ctfd/plugins/submission-history/assets/submission-history.js
 *
 * Closes the gap tracked in cei-labs-event#6: adds a "Show my submitted
 * flag" control to CTFd's own challenge modal, backed by routes.py's
 * GET /api/solve/<challenge_id>. Deliberately an explicit, on-demand
 * reveal -- the flag text is never fetched or displayed until a player
 * clicks the button, even for a challenge they've already solved (per the
 * scoping decision in cei-labs-event's docs/view-submitted-flag-gap-*.md:
 * some organizers want this hidden by default to discourage flag-sharing
 * screenshots during a live event, so nothing here auto-reveals on open).
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
 * has been solved by this account -- clicking "Show my submitted flag"
 * calls the API directly and handles a 404 ("not solved by you") as a
 * normal, expected outcome, rather than making an extra fetch on every
 * single modal open just to decide whether to show the button.
 */
(function () {
  "use strict";

  var PANEL_ID = "submission-history-panel";

  function escapeHtml(str) {
    var div = document.createElement("div");
    div.textContent = str === null || str === undefined ? "" : String(str);
    return div.innerHTML;
  }

  function revealSubmittedFlag(panel, challengeId, btn) {
    btn.disabled = true;
    var originalLabel = btn.textContent;
    btn.textContent = "Checking…";

    window.CTFd.fetch("/plugins/submission-history/api/solve/" + challengeId)
      .then(function (resp) {
        return resp.json().then(function (body) {
          return { ok: resp.ok, status: resp.status, body: body };
        });
      })
      .then(function (result) {
        if (!document.body.contains(panel)) return; // modal moved on mid-request

        if (result.status === 404) {
          btn.remove();
          panel.querySelector(".submission-history-result").innerHTML =
            '<p class="mb-0 text-muted"><small>You haven\'t solved this challenge yet.</small></p>';
          return;
        }
        if (!result.ok) {
          btn.disabled = false;
          btn.textContent = originalLabel;
          panel.querySelector(".submission-history-result").innerHTML =
            '<p class="text-danger mb-0"><small>Could not load your submitted flag.</small></p>';
          return;
        }

        btn.remove();
        panel.querySelector(".submission-history-result").innerHTML =
          '<p class="mb-0">Your submitted flag: <code>' + escapeHtml(result.body.provided) + "</code></p>";
      })
      .catch(function () {
        if (!document.body.contains(panel)) return;
        btn.disabled = false;
        btn.textContent = originalLabel;
        panel.querySelector(".submission-history-result").innerHTML =
          '<p class="text-danger mb-0"><small>Request failed.</small></p>';
      });
  }

  function injectPanel(modal, challengeId) {
    var desc = modal.querySelector(".challenge-desc");
    if (!desc) return null;

    // Sits after instance-launcher's/hint-wallet's own panels when present,
    // so the reveal control is the last thing before the flag-submission
    // form -- falls back to right after the description when neither is
    // there.
    var anchor =
      modal.querySelector("#hint-wallet-panel") || modal.querySelector("#instance-launcher-panel") || desc;

    var panel = document.createElement("div");
    panel.id = PANEL_ID;
    panel.className = "submission-history-panel mb-3 mt-3";
    panel.innerHTML =
      '<button type="button" class="btn btn-outline-secondary btn-sm" data-action="reveal">Show my submitted flag</button>' +
      '<div class="submission-history-result mt-1"></div>' +
      '<p class="mb-0 mt-1"><small><a href="/plugins/submission-history/solves" target="_blank">View all my submitted flags &rarr;</a></small></p>';
    anchor.insertAdjacentElement("afterend", panel);

    panel.querySelector('[data-action="reveal"]').addEventListener("click", function (evt) {
      revealSubmittedFlag(panel, challengeId, evt.currentTarget);
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

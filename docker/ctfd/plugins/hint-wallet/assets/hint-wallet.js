/* docker/ctfd/plugins/hint-wallet/assets/hint-wallet.js
 *
 * Injects a hint panel (per-tier "Reveal hint" buttons, priced as a percent
 * of THIS challenge's own point value) into CTFd's own challenge modal, for
 * the currently-open challenge. Backend: routes.py's /machine/sync,
 * /api/tiers/<track>/<entry_name>, /api/unlock.
 *
 * cei-labs-event#7: there is no shared team-currency wallet anymore -- a
 * tier's cost is a percentage of the challenge's own value, applied as a
 * reduction of that challenge's own score award at solve time
 * (solve_hook.py), not a spend from a pool. Opening a hint is always free
 * up front; the cost is realized later, only if/when this challenge is
 * solved. /api/unlock can also reject with 409 progression_locked (this
 * challenge is outside the player's current unlock window for its track --
 * see progression.py) instead of the old 402 insufficient-balance case.
 *
 * Modeled structurally on
 * ../../instance-launcher/assets/challenge-launch.js -- same
 * MutationObserver-on-"#challenge-window" pattern (that file's header
 * comment explains, from reading the live theme, why a MutationObserver is
 * the only reliable hook: Alpine replaces the modal's entire subtree via
 * `x-html="$store.challenge.data.view"` on every open, including
 * re-opening the same challenge, and the common "click a challenge card"
 * path never dispatches CTFd's `load-challenge` window event), same
 * `#challenge-id` hidden-input-as-anchor convention, same `CTFd.fetch()`
 * CSRF handling, same inline-Bootstrap-classes styling (no separate CSS
 * file in this codebase's convention -- see instance-launcher's
 * templates/instance_launcher/launch.html and this file's own markup
 * below).
 *
 * Where this deliberately differs from challenge-launch.js:
 *
 * 1. Track lookup. The wallet API is keyed by (track, entry_name), where
 *    track is one of 'bandit' | 'krypton' | 'natas' (docker/orchestrator
 *    /app/wallet.py's REQUIRED_TRACKS) -- but nothing CTFd-side stores that
 *    mapping. entry_name is free: it's exactly each challenge's own `name`
 *    field, already visible to a player, and exactly what
 *    CEI-Labs-Wargames/scripts/build_{bandit,krypton,natas}.py write into
 *    both the CTFd challenge.yml's `name:` and the wallet manifest's
 *    `entries[].name` (both come from the same `ch['name']`) -- so no new
 *    plumbing is needed for entry_name. track has no such source, so
 *    CATEGORY_TRACK_MAP below hardcodes the category -> track mapping,
 *    read directly out of those same three build scripts (search each for
 *    where it sets the CTFd `category:` field and the literal track string
 *    passed into its hint-wallet manifest's `"track"` key):
 *      build_bandit.py  -> category: "Linux Basics"  -> track "bandit"
 *      build_krypton.py -> category: "Cryptography"  -> track "krypton"
 *      build_natas.py   -> category: "Web Security"  -> track "natas"
 *    A challenge whose category isn't one of these three (anything outside
 *    the three wargame tracks) has no hint-wallet entry at all, so the
 *    panel stays hidden for it.
 *
 * 2. Where category/name come from. CTFd's own challenge-modal template
 *    does not render the category into the DOM at all -- confirmed by
 *    ../../modal-theme/assets/challenge-modal.css's own header comment
 *    ("Per-category accent coloring ... would need extra JS to fetch
 *    challenge category data that CTFd's own modal template doesn't
 *    render"). So this reads `name`/`category` from CTFd's own public
 *    `GET /api/v1/challenges/<id>` endpoint (the same v1 API this repo's
 *    instance-launcher/solve_hook.py already documents as CTFd's flag-
 *    submission endpoint, `/api/v1/challenges/attempt`) via `CTFd.fetch()`,
 *    rather than trying to scrape it out of the modal DOM.
 *
 * 3. Response handling. /api/unlock returns a bare JSON body plus a real
 *    HTTP status code (200 success -- including a free idempotent re-reveal
 *    of an already-unlocked tier, 404 hint_not_found, 409 no_active_catalog
 *    or progression_locked, 502 orchestrator_unreachable -- see
 *    routes.py/orchestrator_client.py), NOT challenge-launch.js's
 *    "always-200, branch on data.success" shape. So this branches on
 *    resp.status/resp.ok instead, and 409 progression_locked specifically
 *    gets its own clear "not available yet" message rather than a generic
 *    error.
 *
 * 4. No "is this tier already unlocked" state is fetched up front --
 *    /api/tiers deliberately only ever returns {tier, cost}, never content
 *    or an unlocked flag (models.py's HintWalletCatalog cache strips
 *    `content` at write time specifically so a browse-only call can never
 *    leak it). Instead, every tier always renders a "Reveal hint" button;
 *    clicking it calls the real POST /api/unlock, which is itself
 *    idempotent server-side (store.py's WalletStore.unlock_hint:
 *    "already_unlocked" on a repeat, no re-charge) -- so re-revealing a
 *    tier a player already paid for is always free and always shows the
 *    same content, without this file needing its own separate "already
 *    unlocked" bookkeeping.
 */
(function () {
  "use strict";

  var PANEL_ID = "hint-wallet-panel";

  // See this file's header comment (#1) for where each of these three
  // values was confirmed, straight out of the CEI-Labs-Wargames build
  // scripts that set both CTFd's `category:` field and the wallet
  // manifest's `"track"` key.
  var CATEGORY_TRACK_MAP = {
    "Linux Basics": "bandit",
    "Cryptography": "krypton",
    "Web Security": "natas",
  };

  function escapeHtml(str) {
    var div = document.createElement("div");
    div.textContent = str === null || str === undefined ? "" : String(str);
    return div.innerHTML;
  }

  function escapeAttr(str) {
    return escapeHtml(str).replace(/"/g, "&quot;");
  }

  function parseJsonWithStatus(resp) {
    return resp
      .json()
      .catch(function () {
        return {};
      })
      .then(function (body) {
        return { ok: resp.ok, status: resp.status, body: body };
      });
  }

  function hidePanel(panel) {
    panel.style.display = "none";
    panel.innerHTML = "";
  }

  // ── Rendering ─────────────────────────────────────────────────────────

  function renderHintPanel(panel, tiers) {
    panel.style.display = "";

    var html = '<h6 class="mb-2">Hints</h6>';

    if (!tiers.length) {
      html += '<p class="mb-0 text-muted"><small>No hints available for this challenge.</small></p>';
      panel.innerHTML = html;
      return;
    }

    html += '<ul class="list-unstyled mb-0">';
    tiers.forEach(function (t) {
      html +=
        '<li class="mb-2" data-tier="' +
        escapeAttr(t.tier) +
        '">' +
        "<span>Tier " +
        escapeHtml(t.tier) +
        " - will cost " +
        escapeHtml(t.cost) +
        "% of the points</span> " +
        '<button type="button" class="btn btn-outline-secondary btn-sm me-1" data-action="reveal" data-tier="' +
        escapeAttr(t.tier) +
        '">Reveal hint</button>' +
        '<div class="hint-wallet-content mt-1"></div>' +
        "</li>";
    });
    html += "</ul>";

    panel.innerHTML = html;

    panel.querySelectorAll('[data-action="reveal"]').forEach(function (btn) {
      btn.addEventListener("click", function () {
        revealTier(panel, parseInt(btn.dataset.tier, 10), btn);
      });
    });
  }

  // ── Networking ────────────────────────────────────────────────────────

  function revealTier(panel, tier, btn) {
    var track = panel.dataset.track;
    var entryName = panel.dataset.entryName;
    var li = btn.closest("li");
    var contentDiv = li ? li.querySelector(".hint-wallet-content") : null;

    btn.disabled = true;
    var originalLabel = btn.textContent;
    btn.textContent = "Unlocking…";

    window.CTFd.fetch("/plugins/hint-wallet/api/unlock", {
      method: "POST",
      body: JSON.stringify({ track: track, entry_name: entryName, tier: tier }),
    })
      .then(parseJsonWithStatus)
      .then(function (result) {
        if (!document.body.contains(panel)) return; // modal moved on mid-request
        var body = result.body || {};

        if (result.status === 409 && body.error === "progression_locked") {
          btn.disabled = false;
          btn.textContent = originalLabel;
          if (contentDiv) {
            contentDiv.innerHTML =
              '<p class="text-danger mb-0"><small>This challenge\'s hints aren\'t available yet -- ' +
              "solve your way closer to it first.</small></p>";
          }
          return;
        }

        if (!result.ok) {
          btn.disabled = false;
          btn.textContent = originalLabel;
          if (contentDiv) {
            contentDiv.innerHTML =
              '<p class="text-danger mb-0"><small>' +
              escapeHtml(body.error || "Could not unlock this hint.") +
              "</small></p>";
          }
          return;
        }

        // 200: "unlocked" (first open) or "already_unlocked" (idempotent
        // re-reveal, same content) -- both carry the real hint content.
        // routes.py now renders this server-side through CTFd's own
        // cmark-gfm Markdown pipeline (same one challenge descriptions go
        // through) before it ever reaches this response, so it's already
        // safe HTML -- set directly, not escaped, or code fences/backticks/
        // bold would show up as literal text instead of rendering.
        if (contentDiv) {
          contentDiv.innerHTML = body.content;
        }
        btn.remove();
      })
      .catch(function () {
        if (!document.body.contains(panel)) return;
        btn.disabled = false;
        btn.textContent = originalLabel;
        if (contentDiv) {
          contentDiv.innerHTML = '<p class="text-danger mb-0"><small>Request failed.</small></p>';
        }
      });
  }

  function loadTiers(panel, challengeId) {
    var track = panel.dataset.track;
    var entryName = panel.dataset.entryName;
    var tiersUrl = "/plugins/hint-wallet/api/tiers/" + encodeURIComponent(track) + "/" + encodeURIComponent(entryName);

    return window.CTFd.fetch(tiersUrl)
      .then(parseJsonWithStatus)
      .then(function (tiersResult) {
        if (!document.body.contains(panel) || panel.dataset.challengeId !== String(challengeId)) return;

        if (tiersResult.status === 409) {
          // No hint-wallet catalog has ever been synced yet -- a transient
          // deployment state, not "this challenge has no hints", so say so
          // rather than hiding the panel entirely.
          panel.style.display = "";
          panel.innerHTML = '<p class="mb-0 text-muted"><small>Hint catalog is not available yet.</small></p>';
          return;
        }
        if (!tiersResult.ok) {
          // 404 hint_not_found (this specific challenge has no hint-wallet
          // entry -- normal for levels build_*.py didn't put in HINTS) or
          // any other non-2xx: nothing useful to show.
          hidePanel(panel);
          return;
        }

        renderHintPanel(panel, tiersResult.body.tiers || []);
      })
      .catch(function () {
        if (!document.body.contains(panel) || panel.dataset.challengeId !== String(challengeId)) return;
        panel.style.display = "";
        panel.innerHTML = '<p class="text-danger mb-0"><small>Could not reach the hint wallet.</small></p>';
      });
  }

  function loadHintWallet(panel, challengeId) {
    window.CTFd.fetch("/api/v1/challenges/" + challengeId)
      .then(function (resp) {
        return resp.json();
      })
      .then(function (json) {
        if (!document.body.contains(panel) || panel.dataset.challengeId !== String(challengeId)) return;
        if (!json || !json.success || !json.data) {
          hidePanel(panel);
          return;
        }

        var track = CATEGORY_TRACK_MAP[json.data.category];
        if (!track) {
          // Not a Bandit/Krypton/Natas challenge -- no hint-wallet entry
          // can exist for it, so there's nothing for this panel to show.
          hidePanel(panel);
          return;
        }

        panel.dataset.track = track;
        panel.dataset.entryName = json.data.name;
        return loadTiers(panel, challengeId);
      })
      .catch(function () {
        if (!document.body.contains(panel) || panel.dataset.challengeId !== String(challengeId)) return;
        hidePanel(panel);
      });
  }

  // ── Injection / MutationObserver plumbing (mirrors challenge-launch.js) ─

  function injectPanel(modal) {
    var desc = modal.querySelector(".challenge-desc");
    if (!desc) return null;

    // Sits right after instance-launcher's own panel when both are present
    // on a challenge (e.g. a wargame level with a launchable instance),
    // so a player sees "how do I access this" before "what will a hint
    // cost me" -- falls back to right after the description when
    // instance-launcher's panel isn't there at all.
    var anchor = modal.querySelector("#instance-launcher-panel") || desc;

    var panel = document.createElement("div");
    panel.id = PANEL_ID;
    panel.className = "hint-wallet-panel mb-3 mt-3 p-3 border rounded";
    panel.style.display = "none";
    panel.innerHTML =
      '<p class="mb-0 text-muted"><small><i class="fas fa-circle-notch fa-spin"></i> Checking hints&hellip;</small></p>';
    anchor.insertAdjacentElement("afterend", panel);
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

    var panel = injectPanel(modal);
    if (!panel) return;
    panel.dataset.challengeId = String(challengeId);

    loadHintWallet(panel, challengeId);
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

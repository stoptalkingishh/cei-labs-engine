# CEI Labs — Night Handoff (2026-07-23)

**For:** an incoming teammate with a fresh, large token budget, helping *tonight only* on
the biggest production-readiness items.
**From:** the previous session (near its token limit).
**Live host:** everything runs on `192.168.1.173`. Access: `ssh ismaelrodriguez@192.168.1.173`.

Read the "Operational context" section first — it captures gotchas that will otherwise
cost you a lot of tokens to rediscover. Then work the prioritized issue list. Each issue
has a **ready-to-dispatch sub-agent brief** you can paste more or less verbatim.

---

## 0. Operational context (READ THIS FIRST)

### Access & layout
- Box: `ssh ismaelrodriguez@192.168.1.173` (user `ismaelrodriguez`).
- Passwordless `sudo` works for `docker`. The user is **not** in the `docker` group, so
  always use `sudo docker ...`.
- Repos on the box:
  - `/opt/cei-labs/cei-labs-engine` — Swarm stack, CTFd image + plugins, orchestrator,
    operator/kali-novnc attacker image.
  - `/opt/cei-labs/CEI-Labs-Wargames` — challenge content + target images
    (bandit/krypton/natas).
  - `/opt/cei-labs/cei-labs-event` — event ops / TRACKER.
- `gh` CLI is authenticated (as `stoptalkingishh`) on the operator's *local* machine, not
  necessarily over SSH. The box has a working git credential helper (HTTPS push works) —
  never ask for or print a token.

### Deploy flow + the #1 gotcha
1. Rebuild the image: `sudo docker build -t <tag> <context>`.
2. Run tests.
3. `cd /opt/cei-labs/cei-labs-engine/docker && set -a && source .env && set +a && sudo -E docker stack deploy -c stack.yml --with-registry-auth cei-labs`
4. **CRITICAL:** `docker stack deploy` does **NOT** recreate a service when only the image
   *content* changed but the *tag string* (`:offline`) did not. You **must**
   `sudo docker service update --force cei-labs_<svc>` for each service whose code changed.
   This bit us twice tonight. It is also why the **running attacker instance is stale**
   (see Issue #2).
5. **Target images** (`bandit/krypton/natas-target:latest`) are only pulled when the
   orchestrator provisions a **new** instance. Existing instances keep their old image
   until relaunched. After rebuilding a target image, relaunch instances (or they stay
   stale).

### Secrets & credentials (reference where they live; never print values)
- CTFd admin creds + API token: `~/cei-labs-credentials.md` on the box. Read server-side,
  never echo. (Extraction pattern used tonight:
  `CTFD_TOKEN=$(sed -n 's/.*API token: \`\(.*\)\`.*/\1/p' ~/cei-labs-credentials.md)`.)
- Orchestrator/CTFd secrets: `/opt/cei-labs/cei-labs-engine/docker/secrets/*.txt`
  (`plugin_shared_secret`, `hint_wallet_sync_secret`, `credential_encryption_key`,
  `orchestrator_admin_password`, `ctf_key`, ...).
- Orchestrator API auth: header `X-Orchestrator-Auth: <plugin_shared_secret>` for
  `/wallet/*`, `/instances`, etc. Admin routes use `X-Admin-Auth:
  <orchestrator_admin_password>`. The orchestrator is **overlay-internal only** (no
  published port, no Traefik route) — reach it from inside its own container:
  `sudo docker exec <orchestrator-cid> python3 -c "..."`.
- **Fernet key gotcha:** `credential_encryption_key` MUST be a valid Fernet key
  (`python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`).
  A raw `openssl rand -base64 48` is **not** valid Fernet and crash-loops the orchestrator
  (fixed tonight, but don't regress it).

### Wargames content push
- `cd /opt/cei-labs/CEI-Labs-Wargames && ./deploy.sh` with env:
  `CTFD_URL` (`https://ctfd.ctf.local`), `CTFD_TOKEN`, `CTFD_INSECURE=true`,
  `CTFD_SYNC_SECRET` (=`plugin_shared_secret.txt`),
  `HINT_WALLET_SYNC_SECRET` (=`hint_wallet_sync_secret.txt`), `HINT_WALLET_REVISION=1`.
- `deploy.sh` writes `.ctf/config`; if `.ctf/` is root-owned (leftover), `sudo chown` it
  first or the write fails.
- `deploy.sh` already has the HMAC signature fix (payload must be serialized with
  `sort_keys=True` to match what it signs — do not regress).

### Sub-agent discipline (learned the hard way tonight)
- **Give every agent isolated git worktrees.** Multiple agents sharing
  `/opt/cei-labs/<repo>` caused a near-collision (one agent's WIP landed on another's
  branch). Use `git worktree add /home/ismaelrodriguez/wt/<name> <branch>`.
- **Put the full `ssh ismaelrodriguez@192.168.1.173` + box context in every agent
  prompt** or they flail on local paths / wrong IPs.
- Tell agents to **commit & push incrementally** (small commits) so partial work survives
  if a session is cut off.
- Have agents **open PRs, not self-merge** security/functionality-sensitive code.
- Note: the previous session's auto-mode classifier blocked `gh pr merge` when the
  assistant authored the code, forcing manual approval. You may or may not hit the same
  gate on your plan; if you do, it's expected, not a bug.

### Security follow-up (please do)
- **Rotate the CTFd admin password AND regenerate the API token.** Both were exposed in
  tool output during the previous session (a redaction command failed). Not known-abused,
  but rotate as a precaution.

---

## 1. What is already DONE and LIVE (do not redo)

All merged to `main` in each repo; **zero open PRs** across all four repos as of this
handoff.

- **P0 security/reliability (engine):** VNC/SSH password split (8-char noVNC vs SSH),
  non-destructive credential lifecycle (pause/resume), credential-at-rest encryption
  (Fernet AEAD), hint-wallet orchestrator endpoints (`/wallet/*`), hint-wallet CTFd bridge
  plugin (**backend/proxy only** — see Issue #1). All live + verified.
- **P1 (engine):** attacker browser launcher (Chromium + `www-browser` shim), image
  digest pinning + stable-endpoint docs, rotation-overlap runbook for
  `hint_wallet_sync_secret`.
- **Wargames content:** 59 challenges synced; 35 hint-text fixes (man→`--help`, reconnect
  banners) live; hint-wallet bundle synced (revision 1).
- **Theming (merged, built, deployed):** Bandit 34-level themed ASCII art + progressive
  warm palette; Krypton 6-level themed art + cool palette; Natas per-level CSS backgrounds
  + solve-state proxy; Chromium tab-theming extension (favicon/title); CTFd challenge-modal
  CSS (`modal-theme` plugin). All target images rebuilt; CTFd + orchestrator
  force-recreated and confirmed live. **Exception:** the attacker (kali-novnc) service was
  NOT force-recreated → still stale (Issue #2).
- Krypton per-level SSH account isolation was **verified working end-to-end** (separate
  accounts, home dirs, password handoff). The *complaints* about Krypton are content/UX,
  not the isolation mechanism (Issues #3, #4).

---

## 2. Prioritized issue list (the actual work for tonight)

### ISSUE #1 — Hints are completely inaccessible to players (BIGGEST)
**Symptom:** No hints show on the challenge popup. **Root cause:** the `hint-wallet` CTFd
plugin (`docker/ctfd/plugins/hint-wallet/`) ships **no frontend** — only backend routes
(`/api/tiers`, `/api/balance`, `/api/unlock`, `/machine/sync`). Native CTFd hints are
intentionally empty because hint content lives in the wallet bundle. Net effect: there is
**no UI to browse or unlock hints at all.** This was documented as an explicit scope cut
in `docs/P0-FIX-LOG-2026-07-23.md` ("No frontend JS was added... a future pass can wire a
modal control the same way instance-launcher does").

**Fix:** build the player-facing modal UI. Mirror
`docker/ctfd/plugins/instance-launcher/assets/challenge-launch.js` (it already injects a
panel into the challenge modal and is registered via `register_plugin_script`). The new
JS should, inside the challenge modal: fetch `/plugins/hint-wallet/api/tiers/<track>/<entry_name>`
to list tiers + costs, show the team balance from `/api/balance`, and POST `/api/unlock`
to spend points and reveal a tier's content. Add an `assets/` dir + register it in
`hint-wallet/__init__.py` (it currently only registers a stylesheet-less backend). Match
CTFd 3.8.2's modal DOM (see `themes/core/templates/challenge.html` — the real modal body;
`challenges.html` is just the outer SPA shell). Then rebuild CTFd image + force-update.

> **Sub-agent brief:** "Build the missing player-facing frontend for the `hint-wallet`
> CTFd plugin in `cei-labs-engine` (`ssh ismaelrodriguez@192.168.1.173`, repo
> `/opt/cei-labs/cei-labs-engine`, use an isolated worktree under
> `/home/ismaelrodriguez/wt/`). The backend routes exist
> (`/plugins/hint-wallet/api/tiers/<track>/<entry_name>`, `/api/balance`, `/api/unlock`)
> but there is no UI, so players cannot see or unlock hints. Add
> `docker/ctfd/plugins/hint-wallet/assets/hint-wallet.js` (+ minimal CSS) that injects a
> hint panel into CTFd's challenge modal, modeled exactly on
> `docker/ctfd/plugins/instance-launcher/assets/challenge-launch.js` (same injection +
> registration pattern; register it in `hint-wallet/__init__.py`). Real modal DOM is in
> the CTFd image at `themes/core/templates/challenge.html`. The panel must: show the
> team's balance, list each tier with its cost (never fetch/render locked content until
> unlocked), and let the player spend points to unlock a tier and display its content;
> propagate a 402 insufficient-balance as a clear error, never a silent success. Use CTFd's
> CSRF token header like instance-launcher does. Add tests mirroring instance-launcher's.
> Build the CTFd image and confirm the plugin loads and the asset is served. Open a PR;
> do NOT merge or redeploy live. Commit/push incrementally."

---

### ISSUE #2 — noVNC "Connect" click fails (attacker workstation)
**Symptom:** clicking Connect in noVNC fails. **What's confirmed:** server side is
**healthy** — inside the live attacker container `websockify` (6080 → `localhost:5901`)
and `vncserver :1` are both running, and `GET /vnc.html` returns `200` through the gateway
(host `32003` → gateway `16080` → attacker `6080`). So this is **not** a dead service.
**Two live leads:**
1. **The running attacker is a STALE image** — `ctf-kali-novnc@sha256:199d0f8b214c`,
   built earlier today, never force-recreated after tonight's rebuild. Force-recreate it
   to the current `:offline` build first, then re-test:
   `sudo docker service update --force chrange-1-attacker` (confirm exact service name
   with `sudo docker service ls`).
2. **Password layer** — the P0 fix split noVNC (8-char) from SSH (longer) passwords. A
   very likely cause of "connect fails" is the **8-char noVNC password** not being what's
   entered/auto-passed at the noVNC prompt (entering the SSH password will fail VNC auth,
   which truncates to 8 chars). Verify what the launcher surfaces and what the user types.
   The connect flow needs a **live browser reproduction** to pin down (WebSocket upgrade
   through the tcp-gateway, autoconnect/password params on the `vnc.html` URL). Use the
   in-app browser or Chrome MCP against the noVNC URL and watch the console/network for the
   failing WS.

> **Sub-agent brief:** "Diagnose and fix the noVNC connect-click failure on the CEI Labs
> attacker workstation (`ssh ismaelrodriguez@192.168.1.173`). Server side is already
> confirmed healthy (websockify 6080→localhost:5901 and vncserver running; vnc.html 200
> via gateway 32003). First force-recreate the stale attacker service to tonight's built
> image (`sudo docker service update --force chrange-1-attacker`; verify name via
> `sudo docker service ls`). Then reproduce the Connect click live in a browser against the
> noVNC URL and capture the actual failure (WebSocket upgrade error? VNC auth failure?).
> Prime suspect: the 8-char noVNC password (distinct from the SSH password per PR #12) not
> being the value entered/auto-passed. Fix whatever the repro shows — likely in
> `operator/kali-novnc/` start script / launcher URL params, or how the instance-launcher
> plugin surfaces the noVNC vs SSH password to the player. Open a PR; do not self-merge.
> Report the exact failing signal you observed."

---

### ISSUE #3 — Krypton level files are not in the players' home directories
**Symptom:** "Krypton does not have separate logins as the user environment" / "the folders
are [not] the right ones... all required docs should be in the home folder of the new user
accounts." **Root cause:** the entrypoint (`targets/krypton/entrypoint.sh`) writes each
level's files to `/krypton/kryptonN/`, but SSHing in as `kryptonN` lands you in
`/home/kryptonN` (which only has dotfiles). Running `ls` shows nothing → feels broken /
like there's no real per-level environment. The mechanism itself **works** (verified: ROT13
→ krypton2 password → successful krypton2 login), but the layout doesn't match the OTW /
Bandit home-directory convention. The `krypton-01` description also points at
`/krypton/krypton1/krypton2`, so "the instructions for krypton 1->2 are broken" relative to
the desired home-dir layout.

**Fix:** move each level's required files into the *home directory* of the account that
reads them (`/home/kryptonN/`), preserving the permission handoff (next-level password file
readable by current level, owned appropriately). Update the `krypton-0N/challenge.yml`
descriptions to reference the home dir (e.g., "in your home directory" / `~/krypton2`)
instead of `/krypton/...`. Mirror how Bandit lays out its files. Rebuild the krypton target
image + relaunch instances to test.

> **Sub-agent brief:** "In `CEI-Labs-Wargames` (`ssh ismaelrodriguez@192.168.1.173`, repo
> `/opt/cei-labs/CEI-Labs-Wargames`, isolated worktree under `/home/ismaelrodriguez/wt/`),
> move Krypton's per-level challenge files from `/krypton/kryptonN/` into each account's
> home directory `/home/kryptonN/`, so a player who SSHes in and runs `ls` sees the files
> the instructions reference. Edit `targets/krypton/entrypoint.sh` accordingly, preserving
> the permission-handoff model (the file holding level N+1's password must be readable by
> kryptonN, and the account isolation between levels must stay intact — level N must not be
> able to read level N+2's data). Update every `challenges/krypton-0N/challenge.yml`
> description to point at the home directory instead of `/krypton/...`. Mirror how
> `targets/bandit` lays out its files for consistency. Build the krypton target image and
> verify by launching a real instance through the orchestrator and SSHing through the
> chain. Open a PR; do not self-merge."

---

### ISSUE #4 — Krypton descriptions never tell players to reconnect as the next account
**Symptom:** part of "doesn't feel like separate logins." **Root cause:** Bandit
auto-appends an "Account progression: you are working as banditN... exit and reconnect as
banditN+1..." block to every level; Krypton's content build never got that. Every Krypton
level just says "Log in as kryptonN" with no "after solving, reconnect as krypton(N+1) with
the password you found" step. `krypton-02` is even missing its "Log in as" line entirely.

**Fix:** add the same account-progression auto-append logic Bandit's `scripts/build_bandit.py`
has to `scripts/build_krypton.py` (remember: `challenge.yml` files are **generated** build
artifacts — edit the build scripts, not the yml directly). Fix krypton-02's missing "Log in
as" line. Re-run the build + `./deploy.sh` content push. **Can be combined with Issue #3
into one Krypton content PR.**

> **Sub-agent brief:** "In `CEI-Labs-Wargames`, add per-level account-progression /
> reconnect instructions to every Krypton level, matching the pattern Bandit already has
> (see how `scripts/build_bandit.py` appends the 'Account progression' block). Krypton
> challenge.yml files are GENERATED — edit `scripts/build_krypton.py`, not the yml.
> Each level should tell the player the current account, and that after recovering the next
> password they must exit and reconnect as krypton(N+1). Also fix krypton-02's missing
> 'Log in as krypton2' line. Regenerate and verify. Combine with the home-directory-move
> task (Issue #3) into one Krypton PR if you're doing both. Do not self-merge."

---

### ISSUE #5 — Standard Debian MOTD ("programs included with Debian…") not shown
**Symptom:** the familiar Debian copyright/legal text is missing when you SSH into a target.
**Root cause (confirmed):** the target sshd config has `PrintMotd no` (and likely
`UsePAM`/pam_motd not effective for this path), so `/etc/motd` — which *does* contain the
"The programs included with the Debian GNU/Linux system are free software…" text (verified
present in the image) — is never printed. Only the custom per-user banner
(`/etc/profile.d/cei-bandit-banner.sh`, which `cat`s `/etc/cei-labs/banners/$USER`) shows.

**Fix:** enable MOTD printing so BOTH the Debian text and the custom banner appear. Simplest:
set `PrintMotd yes` in the target sshd_config (bandit + krypton, wherever sshd_config is
templated in each `targets/*/build/`), or ensure `UsePAM yes` + pam_motd prints `/etc/motd`.
Confirm ordering reads acceptably (Debian motd + themed banner). Minor but easy polish.

> **Sub-agent brief:** "In `CEI-Labs-Wargames`, make the standard Debian `/etc/motd` text
> print at SSH login again on the Bandit and Krypton target images, alongside the existing
> custom per-user CEI banner. Root cause: the sshd config sets `PrintMotd no` so
> `/etc/motd` (which contains the Debian 'programs included…' copyright text) is suppressed;
> only the `/etc/profile.d/cei-*-banner.sh` banner shows. Fix by enabling MOTD printing
> (`PrintMotd yes`, or `UsePAM yes` with pam_motd) in each target's sshd setup. Rebuild and
> verify both texts appear. Open a PR."

---

### ISSUE #6 — Bandit 2→3 ("Spaces in Places") gives away the answer
**Symptom:** the space-in-filename level lets the learner get the answer without practicing
shell quoting/escaping — "the answer can not just be given to you." **Likely causes to
check:** (a) the description/hint prints the literal solving command (e.g.
`cat 'spaces in this filename'`), defeating the lesson — the original fix-notes explicitly
warned against this; and/or (b) the file setup lets tab-completion / `cat *` trivially
solve it. **Fix:** redesign so reading the file genuinely *requires* the learner to construct
the quoting/escaping themselves; teach the concept without printing the exact command.
Inspect `challenges/bandit-02/challenge.yml`, `scripts/build_bandit.py`, and the Bandit hint
bundle (`challenges/bandit-hint-wallet.json`). Edit the build script (yml is generated).

> **Sub-agent brief:** "In `CEI-Labs-Wargames`, fix Bandit 2→3 'Spaces in Places' so it
> teaches shell quoting/escaping instead of handing the answer over. Currently the learner
> can get the password without figuring out how to quote a filename with spaces. Inspect
> `challenges/bandit-02/challenge.yml` (generated — edit `scripts/build_bandit.py`), the
> level's file setup, and the Bandit hint bundle. Ensure neither the description nor the
> lower-cost hints print the literal solving command; the learner must construct the
> quoting themselves. Keep the highest-cost hint as an intentional near-solution only.
> Regenerate, verify a fresh playthrough still requires the quoting step, open a PR."

---

### ISSUE #7 — SSH login banner ASCII art is too simple
**Symptom:** the per-level ASCII art on the Bandit/Krypton SSH banners is too basic — needs
to be noticeably better/more detailed art. **Scope:** redesign the per-level `ART` in
`targets/bandit/build/generate_banners.py` (34 levels) and
`targets/krypton/build/generate_banners.py` (6 levels). Constraints stay: ASCII-only (ord
< 128), every rendered line ≤ 80 **visible** columns (the length check already strips ANSI),
each level's art genuinely distinct and themed to its title, and color must remain a
*supplement* (banner must read fine with ANSI stripped). You MAY use more lines / more
detailed multi-line art than the current ~3-line pieces — that's the whole point. Keep the
existing progressive color palettes and the title/account/POLICY text logic untouched;
only upgrade the art. Natas is web-based (CSS backgrounds), out of scope here.

> **Sub-agent brief:** "In `CEI-Labs-Wargames`, substantially upgrade the per-level SSH
> login-banner ASCII art for Bandit (`targets/bandit/build/generate_banners.py`, 34 levels)
> and Krypton (`targets/krypton/build/generate_banners.py`, 6 levels) — the current art is
> too simple/small. Make each level's art richer and more detailed while staying ASCII-only,
> ≤80 visible columns per line (ANSI stripped before measuring), genuinely distinct per
> level, and themed to the level's actual title. More lines are fine and encouraged. Do NOT
> change the progressive color palettes, the title/account/reconnect text, or the
> acceptable-use POLICY lines — only the art. Update the existing distinctness/safety tests.
> Verify by rendering and eyeballing several levels (`cat -v`) and running the unittest
> suites. Rebuild the target images. Open a PR; do not self-merge."

---

### ISSUE #8 — CTFd modal theming appears not applied (probably stale cache)
**Symptom:** popups show no theming. **Assessment:** likely NOT a real bug. The
`modal-theme` CSS `<link>` is present in a fresh page load, and its selectors DO match the
real modal template (`themes/core/templates/challenge.html`: `.modal-content`,
`.challenge-name`, `.challenge-hints`, `.nav-tabs`, etc.). The most likely cause is a
browser tab still open from before tonight's redeploy (the `<link>` only appears on a fresh
load, not in an already-running SPA session). **Action:** ask the user to hard-refresh
(Ctrl+Shift+R) / open a new tab and recheck FIRST. Only invest real work if it persists —
in which case verify the CSS is actually applied via devtools (element inspector on
`#challenge-window`) rather than assuming.

---

## 3. Suggested order of attack
1. **#1 hint frontend** (biggest functional gap; hints are dead without it).
2. **#2 noVNC** (force-recreate stale attacker first — that alone may resolve it — then
   repro the click).
3. **#3 + #4 Krypton content** (combine into one PR: home-dir move + reconnect
   instructions + krypton-02 fix).
4. **#5 MOTD** and **#6 Bandit-2 quoting** (small, independent content fixes).
5. **#7 banner art upgrade** (largest creative task; parallelizable per track).
6. **#8** — just have the user hard-refresh; likely nothing to do.

After any image rebuild: **force-update the service** (§0 gotcha), and for target images,
relaunch instances to pick them up. Then verify live before calling it done.

---

## 4. Note from the previous session
I packaged everything as sub-agent briefs rather than launching a swarm of fix agents,
because the previous session was near its token limit and you have the fresh budget to run
them. Dispatch them with isolated worktrees (§0). Nothing here is started/in-flight — all
branches are clean and there are no open PRs to reconcile.

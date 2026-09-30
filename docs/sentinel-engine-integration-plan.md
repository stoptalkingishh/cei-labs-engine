# Sentinel Engine Integration Plan

Companion plan for [CEI-Labs-Wargames PR #67](https://github.com/stoptalkingishh/CEI-Labs-Wargames/pull/67). This is an Engine prerequisite plan only; it does not add Sentinel content or change runtime behavior.

## Proposed Track Contract

| Field | Value |
|---|---|
| Slug | `sentinel` |
| Display name | `Sentinel - Security Operations` (provisional) |
| CTFd category | `Security Operations` |
| Stage order | `4` |
| Expected challenges | `22` (`Start Here` plus 21 labs) |

The expected count is a product contract, not an incidental reconcile value. Confirm the provisional display name and count with the Wargames plan before implementation.

## Required Engine Changes

1. Add the Sentinel tuple to `docker/ctfd/plugins/wargame-stages/__init__.py` `DEFAULT_STAGES`, with the contract above. The Engine does **not** ingest `CEI-Labs-Wargames/game-stages.yml`; its own defaults must seed the matching `GameStage` row.
2. Extend `docker/orchestrator/app/wallet.py` `REQUIRED_TRACKS` with `sentinel`. Replace the three-manifest-specific completeness check with an exact required-set check: each required track occurs once, with no missing, duplicate, or extra tracks. The sync must remain atomic, so a rejected four-manifest bundle leaves the active catalog unchanged.
3. Add `sentinel` <-> `Security Operations` consistently to `docker/ctfd/plugins/hint-wallet/track_mapping.py` and `docker/ctfd/plugins/hint-wallet/assets/hint-wallet.js`. Hint-wallet route, progression, and solve hooks are generic after their category-to-track mappings resolve.
4. Do not change instance-launcher schemas or launcher routing. Its `single-target` path is track-agnostic, but prove the Sentinel integration: a pending Sentinel challenge is denied by all launcher entry points, and a visible Sentinel challenge maps to exactly one `single-target` instance configuration/key.

## Test Work

- Update `docker/orchestrator/tests/test_wallet.py` for a valid four-manifest bundle and missing, duplicate, and extra Sentinel-track cases.
- Update `docker/orchestrator/tests/test_wallet_api.py` for signed four-manifest acceptance, rejection without Sentinel, and preservation of the prior catalog after a rejected sync.
- Update `docker/ctfd/plugins/hint-wallet/tests/test_routes.py` with `Security Operations` progression/route mapping coverage.
- Update `docker/ctfd/plugins/hint-wallet/tests/test_solve_hook.py` to verify a Sentinel-category solve resolves `sentinel` and applies the normal hint penalty.
- Add default-stage seeding coverage for `DEFAULT_STAGES` and extend `docker/ctfd/plugins/wargame-stages/tests/test_reconcile.py` to map and hide pending Sentinel-category challenges.
- Extend `docker/ctfd/plugins/instance-launcher/tests/test_stage_gating.py` (or add a focused fixture there) for pending-stage denial and Sentinel's one `single-target` mapping.

## Integration And Release Sequence

1. Implement and test the Engine changes, then publish both the CTFd/plugin image and the orchestrator image.
2. Deploy both images by immutable digest reference, not a mutable tag. Record the deployed CTFd and orchestrator digests.
3. Before importing Wargames content, verify the seeded Sentinel stage row has slug `sentinel`, category `Security Operations`, order `4`, and expected count `22`.
4. Submit and verify one accepted, atomic four-manifest wallet sync containing exactly Bandit, Krypton, Natas, and Sentinel. Confirm the accepted revision/digest before content import or stage reconcile.
5. Only then import Sentinel content from Wargames and run the normal stage reconcile. Verify that the 22 imported Sentinel challenges map to the pending stage and are hidden until started.

## Blockers And Risks

- Engine defaults and Wargames configuration are duplicated and can drift; Engine does not consume the Wargames stage YAML.
- Category mapping is duplicated in Python and JavaScript and must change together.
- The count of 22 is a product contract and requires explicit agreement before release.
- `docker/stack.yml` currently references the CTFd and orchestrator images through the mutable `${IMAGE_TAG}` tag; digest pinning is required for this release.

## Out Of Scope

- No manifest-ingestion API in this phase.
- No launcher schema changes.
- No Sentinel content, challenge definitions, or Wargames import changes.

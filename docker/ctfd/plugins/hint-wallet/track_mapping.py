"""docker/ctfd/plugins/hint-wallet/track_mapping.py

CTFd's Challenges table has no "track" column -- `category` is what
actually distinguishes Bandit/Krypton/Natas/Sentinel challenges CTFd-side, and it
does NOT match the hint-wallet manifest's track string directly. Confirmed
straight out of the four Wargames build scripts (search each for where it
sets the CTFd `category:` field, alongside the literal track string it
writes into its own hint-wallet manifest's `"track"` key):

  build_bandit.py  -> category "Linux Basics" -> track "bandit"
  build_krypton.py -> category "Cryptography" -> track "krypton"
  build_natas.py   -> category "Web Security" -> track "natas"
  build_sentinel.py -> category "Security Operations" -> track "sentinel"

Single source of truth for this mapping on the CTFd side -- used by both
routes.py (progression-window gating) and solve_hook.py (resolving a
solved challenge's track to look up its hint-wallet cost), so it only has
to be kept in sync with the build scripts in one place. Deliberately has
zero CTFd imports so it's trivially unit testable.

assets/hint-wallet.js necessarily hardcodes the SAME mapping again on its
own (JS can't import this module) -- see that file's header comment for
its copy. If a track is ever added or a category renamed, both places need
updating together.
"""

TRACK_CATEGORY_MAP = {
    "bandit": "Linux Basics",
    "krypton": "Cryptography",
    "natas": "Web Security",
    "sentinel": "Security Operations",
}

CATEGORY_TRACK_MAP = {category: track for track, category in TRACK_CATEGORY_MAP.items()}


def category_for_track(track: str) -> "str | None":
    return TRACK_CATEGORY_MAP.get((track or "").lower())


def track_for_category(category: str) -> "str | None":
    return CATEGORY_TRACK_MAP.get(category or "")

# CEI Labs Natas Tab Theming (unpacked Chromium extension)

Loaded at Chromium launch via `--load-extension=/opt/cei-natas-theme-extension`
(see the `chromium` wrapper installed in `operator/kali-novnc/Dockerfile`).
No Chrome Web Store submission, no signing, no custom Chromium build --
this is the standard command-line "load an unpacked extension"
mechanism, which does not require Developer Mode to be toggled in the
UI when the flag is passed at process launch.

## What this does

- Detects the current Natas level from the page's URL port
  (`8000`-`8014`).
- Sets the browser **tab title** to `Natas <N>: <level title>`, prefixed
  with `[SOLVED] ` if the page is currently rendering its solved state.
- Sets the browser **tab favicon** to a small generated PNG: a filled
  circle colored along the same cool(recon)->hot(exploitation) hue
  sweep used by `CEI-Labs-Wargames/targets/natas/build/generate_themes.py`,
  with the level number drawn on it and a solved/locked badge (filled
  dot vs. hollow ring, so it doesn't rely on color alone).
- Solved/locked state is read from the `cei-solved` / `cei-locked` class
  that `CEI-Labs-Wargames`'s own `cei-natas-banner.php` already sets on
  `<body>` server-side -- this extension does **not** reimplement solve
  detection, to avoid two divergent definitions of "solved" living in
  two separate repos. If that class is absent, the level renders as
  locked/neutral.

## What this explicitly does NOT do, and why

The original ask was to theme "the browser window itself" per level.
Investigated honestly before building anything:

- **Native window-chrome theming (title bar color, etc.) is out of
  scope.** Chromium only supports that via either (a) a full custom
  Chromium build with the theme baked into the binary, or (b) a signed
  browser theme installed from the Chrome Web Store. Neither is
  practical here -- this image builds stock Kali `chromium` from apt,
  and there's no CI pipeline or signing identity for a Web Store theme.
  Building or faking either would mean shipping something that doesn't
  actually work as advertised, so it wasn't attempted.
- **In-page visual theming (backgrounds, colors within the Natas pages
  themselves)** is deliberately left to `CEI-Labs-Wargames`'s own
  server-side work (`cei-natas-banner.php` + `generate_themes.py`) and
  is NOT duplicated here as a competing content-script-injected
  stylesheet. This extension only reads that state back (see above) to
  keep the tab favicon/title in sync with it, rather than maintaining a
  second, possibly-diverging copy of the same theming.

What IS achievable and is what got built: tab favicon + tab title
theming via an unpacked extension's content script. That's the ceiling
of what's practical without a custom Chromium build or store
publication.

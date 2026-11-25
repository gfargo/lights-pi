# Control UI: design and mobile audit

November 2026. The audit behind the branding and mobile redesign of
`control-server/templates/index.html`. It records what the interface did
before the redesign, why each change was made, and the rules the redesign
follows, so later edits keep to them.

Evidence: screenshots of every tab at 1440×900 and 390×844 against the mock
rig (`MOCK_DMX=1`, the studio workspace), plus the Diagnostics tab on the
real Pi, and a read of the template's CSS.

## Where it stood

The foundation was right: a near-black ground, warm white text, one warm
accent, square corners, hairline rules, and the scene swatch (one colour
cell per fixture) on every scene button. The problems were scale, hierarchy
and the phone.

| # | Finding | Where | Why it matters |
|---|---|---|---|
| 1 | Most text is 9.6–12px: `0.6rem`–`0.75rem` appears about 70 times, mostly uppercase monospace with wide tracking | Section labels, tabs, status bar, card labels, slider ticks | The app is read at arm's length in a dark room. Small spaced capitals are the hardest text there is to read in that light. |
| 2 | On a 390px phone the header (logo, title, subtitle, status bar, Groups) takes about 280px, a third of the screen, before any control | `.header`, `.health-strip`, Groups button | Every tab starts a third of the way down; the scenes, the thing people came for, are below the fold. |
| 3 | Eight tabs scroll sideways at 11px; the fourth is cut to "C" at 390px | `.tabs` | Half the app is invisible and nothing says the row scrolls. |
| 4 | Blackout is a small outlined button pinned top-right | `.blackout-btn` | The one control that must always be reachable sits where a thumb reaches least. |
| 5 | Slider thumbs are 16px | `.channel-slider`, Kelvin, strobe | A finger needs about 44px. Fine adjustment on the console is a fight. |
| 6 | On desktop everything sits in a 900px column | `.container` | The console and the scene grid are cramped while half the window is empty. |
| 7 | The title is hard-coded "Lighting Control" with a generic sunburst icon; nothing about the deployment can be named without editing the template | `<title>`, `<h1>`, manifest, `/icon.svg` | Every install looks the same and the home-screen app is called "Lighting Control". |
| 8 | The accent is `#d97757`; the marketing site's is `#FF6A1F`. A comment claims they match | `--amber-tungsten` | The product and its site look like two products. |
| 9 | Scenes on the phone are text buttons with a 6px swatch strip above them | `.scene-btn` | The colour is the information; it is drawn as decoration. |
| 10 | Fonts load from Google Fonts | `@import` at the top of the style block | Without internet the type silently changes mid-show. Not fixed in this pass; see "Later". |

## Rules for the redesign

1. **Readable first.** Nothing a user must read is under 13px; body and
   control text is 15–16px. Labels are sentence case in the text face.
   Monospace is for values only: DMX levels, Kelvin, Hz, BPM, addresses,
   times.
2. **One action colour.** The accent (`--amber-tungsten`, `#FF6A1F` by
   default, set per install with `BRAND_ACCENT`) marks the primary action
   and the current selection. Red is only for Blackout and errors; green and
   amber only for status.
3. **The swatch is the mark.** The eight-cell swatch replaces the sunburst
   as the default logo and app icon, and it is the scene button on the phone:
   a tall colour tile with the name under it, not a strip over a word.
4. **Thumb reach on the phone.** A one-line header (mark, studio name,
   status dot). Tabs become a bottom bar with the four most used and a
   "More" sheet for the rest. Blackout is a full-width control the thumb can
   reach, never a corner button.
5. **Hit areas of 44px.** Buttons, tabs and slider thumbs; slider tracks are
   taller than they look.
6. **Use the width.** On desktop the container widens and scene and console
   grids add columns instead of stretching.
7. **Name the install.** `BRAND_NAME` replaces "Lighting Control" in the
   header, tab title and installed app name; a custom logo still wins over
   the swatch mark; `static/theme.css`, when present, loads after the
   built-in styles for anything else.

## Later

- Serve the fonts from the Pi (finding 10). Geist is OFL-licensed, so its
  files can ship in `control-server/static/fonts/`.
- A light theme for daylight rooms.

# Ebonwake

A Black Desert Online companion for one player: a compact tabbed dashboard and a
transparent click-through overlay, fed by a small local server.

- **Market** - Central Market prices, history and order book via the public
  arsha.io API (read-only, cached).
- **Today** - daily / weekly checklist with reset countdowns.
- **Progress, Grind, Events, Deadeye** - quest and gear tracking, grind sessions
  and buff timers, coupon and event expiries, build notes.

## What it never does

Ebonwake never touches the game client: no memory reading, no injection or
hooking, no packet access, no client file changes, and no input automation of
any kind. The overlay is an ordinary always-on-top window that ignores the mouse.
See `docs/research/0001-bdo-data-and-tos.md`.

## Run

```
python -m server.ew            # local server on 127.0.0.1:8940
npm install --prefix app       # once, installs Electron
npm start --prefix app         # dashboard + overlay (Ctrl+Alt+E toggles overlay)
```

## Test

```
python -m pytest -q
npm test --prefix app
```

## Layout

`server/ew/` server - `app/` Electron dashboard + overlay - `tools/` ETA log, lane
driver, leak sweep - `ops/fleet_kit/` vendored fleet kit (do not edit) -
`docs/` spec, plans, research, ADRs.

Not affiliated with or endorsed by Pearl Abyss. Black Desert is a trademark of
Pearl Abyss Corp.

License: Apache-2.0.

# ADR 0001 - Electron app + Python stdlib server

Date: 2026-10-04. Status: accepted (adjudicated).

Decision: one Electron process for the dashboard and overlay windows; a Python
3.11+ stdlib HTTP server on 127.0.0.1:8940 for data, cache and store.

Alternatives: (a) browser-only dashboard plus a separate overlay tool - rejected,
a browser cannot make a click-through always-on-top window; (b) Tauri - smaller,
but adds a Rust toolchain the fleet does not use; (c) a game-hooking overlay
(D3D present hook) - FORBIDDEN by the ToS floor.

Why: Electron gives transparent click-through windows and `globalShortcut`
without touching the game; the fleet already runs Python stdlib servers and
tests, so CI and conventions carry over.

Reverses if: Electron's overlay window is measured tripping XIGNCODE3 on an alt
account, or the operator orders another stack.

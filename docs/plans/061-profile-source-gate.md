# Plan 061 - Profile source robots gate: self-host base, graceful degrade

Status: open. Autonomy deep dive 2026-10-06 (research 0007 section 2.3). Lane hint: `data`.

Spec gap: the public BDO-REST-API instance moved to `api.cutepap.us`, whose
robots.txt is `Disallow: /`; the old host is dead. `server/ew/progress.py`
`DEFAULT_BASE` and `config/local.example.json` still point at it and plan
041 snapshots it hourly. EW must never call a robots-disallowed host, and a
dead profile source must go quiet instead of erroring every hour.

1. `DEFAULT_BASE` becomes empty (profile source off). The documented path
   is a self-hosted instance (`man90/bdo-rest-api`, default
   `http://127.0.0.1:8001/v1`), set as `profile.base_url` in gitignored
   `config/local.json` and the allowlisted plan 030 settings form.
   `config/local.example.json` shows the loopback example, never the
   public host.
2. Robots gate: before the first call per base (and every 24 h) fetch
   `<origin>/robots.txt` with plan 014's `coupons.robots_verdict` (both
   `*` and the Ebonwake agent); disallowed or unreachable robots on a
   non-loopback host = off with reason `robots`. Loopback hosts skip the
   gate (the operator's own instance).
3. Degrade: when off, `ProfileClient` makes no request; GET
   `/api/progress` returns `profile: {state: "off", reason}`; plan 041 /
   042 cards show one muted line "Profile source off - set a self-hosted
   base in Settings" and the hourly snapshot job is not scheduled. Three
   consecutive failures on an allowed base back off to 24 h (plan 002
   backoff) and show the last good snapshot with its age.
4. Tests: `tests/test_profile_gate.py` - default base makes zero requests;
   robots `Disallow: /` -> off, zero profile GETs; loopback base skips the
   gate; backoff after 3 failures; no literal of the public host left in
   `server/`.

Acceptance: with the shipped defaults EW performs zero requests to any
BDO-REST-API host; a loopback fixture server is called normally; gates
green; verifier PASS within 3 rounds; one push.

ToS check: unauthenticated GETs only, robots.txt honoured; no game input,
no memory read, no client file.

Depends on: 041, 030.

Dependency guard: before writing code the lane checks that `server/ew/progress.py` (plan 041) and `server/ew/settings.py` (plan 030) exist. If any is missing, the lane changes nothing, writes `"status": "blocked", "needs": ["041", "030"]` into its progress JSON (`ops/loop/control/progress/p061-build.json`) and exits 0.

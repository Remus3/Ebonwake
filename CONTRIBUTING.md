# Contributing to Ebonwake

Thanks for looking. A few rules here are stricter than usual; read the four
below before writing code. The rest is ordinary.

## Getting set up

```
git clone <this repository's URL> Ebonwake
cd Ebonwake
python tools/install_hooks.py
python -m pytest -q
npm test --prefix app
```

**Run `tools/install_hooks.py` first.** `core.hooksPath` is local git config
and is never cloned, so a fresh clone runs no hooks until you wire them. The
hooks run the leak sweep and the commit-message checks.

Python 3.11+, standard library only for `server/`, `tools/` and `tests/`.
Node 20+ for `app/`; its tests use `node --test` and need no Electron.

## The four rules

### 1. Never touch the game

No reading or writing game memory, no DLL injection, no D3D/DXGI hooks, no
packet capture or alteration, no client file changes, and no input of any kind
to the game window - no key or mouse automation, no macros, no AFK helpers, no
screen-reading that leads to input. Market calls are read-only `GET`s.

Permitted: the public arsha.io and BDO-REST-API endpoints, a tail of the
client's session log, screenshots the player takes, and what the player types.
If a feature needs more than that, the feature is rejected, not the rule. See
[`docs/research/0001-bdo-data-and-tos.md`](docs/research/0001-bdo-data-and-tos.md).

### 2. A failing test first

Write the test, watch it fail, then implement the minimum that makes it pass,
then run both suites. A guard that stays green when you delete the behaviour it
protects is not a test.

### 3. Nothing personal or machine-specific in the tree

No absolute paths, account ids, emails, family or character names, or secrets
in any tracked file. Per-host values go in gitignored `config/local.json`;
secrets live in user environment variables and are referenced, never stored.
`tools/leak_sweep.py` enforces this on commit and push.

### 4. ASCII and LF only

Every authored file is 7-bit ASCII with LF line endings - code, Markdown,
commit messages. No smart quotes, no em-dashes; use ` - `.
`tests/test_ascii_lf.py` enforces it.

## Pull requests

Fill in the template. Small and focused beats large and sweeping. No
`Co-Authored-By` trailers.

Check [`docs/adr/`](docs/adr/) before re-opening a settled question, and
[`docs/plans/ROADMAP.md`](docs/plans/ROADMAP.md) for open work.

## Licence

Contributions are accepted under the [Apache License 2.0](LICENSE), the same
licence as the project. By opening a pull request you confirm you have the
right to contribute the code under it. Do not paste in GPL or AGPL code.

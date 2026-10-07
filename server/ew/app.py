"""EW server: loopback-only stdlib HTTP server.

Routes: /api/health, /api/version (fleet P0-5), /api/state, /events (SSE),
/ (302 to the dashboard page, plan 020) and /app/* (static dashboard + overlay
assets, browser fallback),
/api/market/{watch,item,hot} (plan 002), /api/market/search (plan 028), /api/today (plan 003),
/api/progress (plan 004), /api/grind (plan 005), /api/events (plan 006; plan 014
adds its `suggested` coupon block; plan 075 its `login_days` block + `login_mark` POST),
/api/deadeye (plan 007; /api/deadeye/enhance GET plan 035; /api/deadeye/shopping GET plan 037;
/api/deadeye/calc GET plan 055), /api/game (plan 008), /api/leveling (plan 011),
/api/spots (plan 012, GET only), /api/bosses (plan 031; plan 072 adds its `drift`
block), /api/settings (plan 030), /api/pets
(plan 043), /api/inventory (plan 045), /api/mounts (plan 044), /api/summary (plan 046),
/api/onboarding (plan 051), /api/crafting (plan 054), /api/imperial (plan 053),
/api/overlay/context (plan 067; SSE `overlay_context` on change),
/api/whatnow (plan 069; SSE `whatnow` carries the full view on change),
/api/signals (plan 073, GET only; plan 079 adds its `overrides` section),
/api/overrides (plan 079, GET only; POST /api/settings {"clear": key} retires one),
/api/portraits + /api/portraits/img/<id> (plan 082, GET only; SSE `portraits`), /api/prompts (plan 070: quiet gate, alert ladder,
live prompts; stale suggestions / review rows / pending stops are left out of their own
payloads), /api/maint/digest (plan 074: before-maintenance digest), and POST
/api/market/watch + /api/today + /api/progress + /api/grind + /api/events + /api/deadeye +
/api/ocr (plan 009) + /api/leveling + /api/bosses + /api/settings + /api/pets +
/api/inventory + /api/mounts + /api/onboarding + /api/crafting + /api/imperial +
/api/maint/digest behind one shared guard.
"""

import datetime as _dt
import hashlib
import json
import mimetypes
import os
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from urllib.parse import parse_qs

from . import (__version__, autotick, autowatch, bossdrift, bosses, context, coupons, crafting, deadeye,
               derived, detect, enhance, eventnotices, events, gamewatch, grind, imperial, inventory, itemnames,
               leveling, logindays, maint, maintdigest, market, mounts, ocr, ocrauto, onboarding, overrides,
               pets,
               ports,
               playsession, portraits, progress, prompts, settings, shopping, signals, single,
               spots, summary,
               today, weekly, whatnow, xpbooks)
from .store import Store

REPO_ROOT = Path(__file__).resolve().parents[2]
APP_DIR = REPO_ROOT / "app"
KIT_TOKENS = REPO_ROOT / "ops" / "fleet_kit" / "tokens.css"
RUNTIME = REPO_ROOT / "ops" / "runtime"
CONFIG_PATH = REPO_ROOT / "config" / "local.json"
DASHBOARD_PAGE = "/app/dashboard/index.html"
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "[::1]"}
MAX_POST_BYTES = 4096
# A 20000-char deadeye note JSON-escaped at 6 bytes/char (\uXXXX) is 120000 bytes;
# 128 KiB covers it plus the envelope. Raised for this route only (plan 007).
MAX_DEADEYE_POST_BYTES = 131072
# An over-cap body up to this size is read and dropped before the 413 so the
# close is not a TCP reset (a 64 KiB bound reset deadeye's 128 KiB+ bodies and
# made test_route_cap_is_per_route flaky with ConnectionAborted on Windows).
DRAIN_MAX_BYTES = 1 << 20
POST_CAPS = {"/api/deadeye": MAX_DEADEYE_POST_BYTES}
SSE_TICK_S = 0.25  # SSE wakes this often to notice a leveling change (plan 011)
# Plan 069: each SSE stream re-checks What now at most this often (the result is
# shared across streams) and at once after any domain change.
WHATNOW_CHECK_S = 10.0
# Plan 049: a 200 POST on these routes pushes `event: <domain>` (data: the JSON
# domain name); clients re-GET. /api/leveling keeps its plan 011 full-view event.
POST_DOMAINS = {"/api/today": "today", "/api/grind": "grind", "/api/market/watch": "market",
                "/api/progress": "progress", "/api/events": "events"}
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0

TABS = [
    {"id": "home", "title": "Home", "plan": "025"},
    {"id": "today", "title": "Today", "plan": "003"},
    {"id": "market", "title": "Market", "plan": "002"},
    {"id": "progress", "title": "Progress", "plan": "004"},
    {"id": "grind", "title": "Grind", "plan": "005"},
    {"id": "events", "title": "Events", "plan": "006"},
    {"id": "deadeye", "title": "Deadeye", "plan": "007"},
    {"id": "system", "title": "System", "plan": "001"},
    {"id": "settings", "title": "Settings", "plan": "030"},
]


def _now_iso():
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


class DomainBus:
    """Per-domain change counters (plan 049); each SSE stream diffs a snapshot."""

    def __init__(self):
        self._lock = threading.Lock()
        self._seq = {}

    def bump(self, domain):
        with self._lock:
            self._seq[domain] = self._seq.get(domain, 0) + 1

    def snapshot(self):
        with self._lock:
            return dict(self._seq)


def read_commit(root=REPO_ROOT):
    """HEAD commit at bind time, or None. git is resolved from PATH, never cwd."""
    git = shutil.which("git")
    if not git:
        return None
    try:
        out = subprocess.run([git, "-C", str(root), "rev-parse", "HEAD"], capture_output=True,
                             text=True, timeout=5, creationflags=_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return None
    sha = out.stdout.strip()
    return sha if out.returncode == 0 and len(sha) == 40 else None


def config_hash(root=REPO_ROOT):
    p = Path(root) / "config" / "local.json"
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()
    except OSError:
        return None


def config_market_watch(root=REPO_ROOT):
    """`market_watch` ids from gitignored config/local.json, or []."""
    try:
        doc = json.loads((Path(root) / "config" / "local.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    ids = doc.get("market_watch") if isinstance(doc, dict) else None
    return ids if isinstance(ids, list) else []


def config_market(root=REPO_ROOT):
    """`market` {vp, fame_pct} from gitignored config/local.json (plan 027)."""
    try:
        doc = json.loads((Path(root) / "config" / "local.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        doc = None
    return market.settings_from(doc)


class EWServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, addr, store_root=None, commit=None, sse_interval=15.0,
                 market_client=None, market_seed=None, today_clock=None,
                 profile_client=None, profile_cfg=None, grind_clock=None, events_clock=None,
                 deadeye_clock=None, game_watch=None, game_cfg=None, game_poll=False,
                 ocr_runner=None, ocr_cache_dir=None, leveling_clock=None,
                 coupon_client=None, coupon_spawn=None, bosses_clock=None,
                 config_path=None, notice_client=None, notice_spawn=None, detector=None,
                 context_clock=None, drift_client=None, drift_spawn=None):
        super().__init__(addr, Handler)
        # Plan 065: read-only BDO folder auto-detect; only main() passes one, so
        # no test ever probes the real disk or registry.
        self.detector = detector
        detected = detector.paths if detector is not None else None
        self.started = _now_iso()
        self.commit = commit
        self.cfg_hash = config_hash()
        self.store = Store(store_root or RUNTIME / "store")
        # Plan 030: the only writer of config/local.json (allowlisted keys only).
        self.settings = settings.Settings(config_path or CONFIG_PATH, detected=detected)
        # Plan 079: source / set-at / expiry for operator-set values; a live
        # signal (VP timer, maintenance notice) supersedes an override.
        self.overrides = overrides.OverrideLedger(self.store, clock=today_clock or time.time,
                                                  maint_end=self._maint_end_after)
        self.sse_interval = sse_interval
        self.bus = DomainBus()
        seed = config_market_watch() if market_seed is None else market_seed
        self.market = market.MarketService(market_client or market.ArshaClient(),
                                           market.Watchlist(self.store, seed=seed),
                                           settings=config_market())
        self.market.effective = self.tax
        # Plan 028: name index over the seed, util/db and this client's cache;
        # util/db rides the market client's fetch so a test client stays offline.
        mc = self.market.client
        self.names = itemnames.NameIndex(
            seed=itemnames.load_seed(), market_cache_dir=mc.cache_dir,
            index_path=(Path(store_root).parent if store_root else RUNTIME) / "market_names.json",
            utildb=itemnames.UtilDb(fetch=mc.fetch, clock=mc.clock,
                                    cache_dir=mc.cache_dir / "utildb"),
            clock=mc.clock)
        self.today = today.TodayService(self.store, clock=today_clock or time.time)
        cfg = profile_cfg
        if cfg is None:  # an injected client (tests) never reads config/local.json
            cfg = progress.config_profile(REPO_ROOT) if profile_client is None else {}
        if profile_client is None:
            family = progress.family_from_config(cfg)
            if family is not None:
                profile_client = progress.ProfileClient(family, base_url=cfg.get("base_url"))
        # Season level objectives auto-tick from the newest XP sample (plan 013);
        # plan 023: the newest started XP epoch flags bracket tables to re-verify.
        # Plan 041: each profile refresh appends to the history beside the store;
        # a main-level rise adds a plan 011 marker unless profile.auto_level is false.
        self.progress = progress.ProgressService(
            self.store, profile_client, level=lambda: self.leveling.current_level(),
            epoch=lambda: self.leveling.active_epoch(),
            history=progress.ProfileHistory(
                (Path(store_root).parent if store_root else RUNTIME) / "profile_history.jsonl",
                clock=getattr(profile_client, "clock", time.time)),
            on_level=lambda lvl: self.leveling.profile_marker(lvl),
            auto_level=cfg.get("auto_level", True))
        # Plan 018: buff presets follow the newest started XP epoch.
        # Plan 039: loot priced from the cached arsha sublist, taxed per plan 027.
        self.grind = grind.GrindService(
            self.store, clock=grind_clock or time.time,
            epoch=lambda: self.leveling.active_epoch(),
            prices=lambda iid: market.price_of(self.market.client.sublist(iid)["data"]),
            tax=self.tax)
        # XP stack counts armed grind buffs that carry an xp_pct (plan 011).
        # Plan 060: Combat Secret Book ledger; Black Shrine books follow plan 033 ticks.
        # Plan 081: a typed Hot Time window ends at the next maintenance end.
        self.leveling = leveling.LevelingService(
            self.store, clock=leveling_clock or time.time,
            buffs=lambda: self.grind.view()["buffs"],
            books=xpbooks.XpBooksService(self.store, clock=leveling_clock or time.time,
                                         weekly=lambda: self.weekly.counts()),
            maint_end=self._hot_until)
        # Plan 012: plan 004's character + plan 005's per-spot silver/h; plan
        # 018: the newest started XP epoch flags rows to re-verify.
        self.spots = spots.SpotsService.from_file(
            character=lambda: self.progress.view(refresh=False)["character"],
            grind=self.grind.view, epoch=self.leveling.active_epoch)
        # Plan 033: weekly content gated by plan 004's level + gs and plan 023's brackets.
        self.weekly = weekly.WeeklyService(
            self.store, clock=today_clock or time.time, character=self._weekly_character,
            brackets=lambda: self.progress.view(refresh=False)["brackets"])
        # Plan 024: level-gated deadlines show read-only in "Ending soon".
        self.events = events.EventsService(self.store, clock=events_clock or time.time,
                                           deadlines=self.leveling.deadline_rows)
        # Coupon suggestions (plan 014): off unless a client is passed; only main()
        # passes the live one, so no test ever reaches the network.
        # Plan 064: codes read from official notices join the suggestions.
        self.coupons = coupons.CouponService(coupon_client, self.events, spawn=coupon_spawn,
                                             extra=lambda: self.notices.coupon_extra())
        # Plan 059: official event-notice windows, gated like plan 014;
        # maintenance-relative ends follow the events.maintenance_start_utc setting.
        # Plan 064: + Notices / Updates boards; full windows auto-added (with
        # undo) while notices.auto_add is on, Hot Time into plan 011's card.
        self.notices = eventnotices.NoticeService(
            notice_client, self.events, self.store, spawn=notice_spawn,
            maint_start=self._maint_start, leveling=self.leveling,
            auto_add=lambda: self.settings.view()["settings"]["notices.auto_add"])
        # Plan 074: before-maintenance digest - loss sentences from the cached
        # maintenance notices + what ends at the next maintenance; never fetches.
        self.maintdigest = maintdigest.DigestService(
            self.store, loss_rows=self.notices.loss_rows,
            items=lambda: self.events.view()["items"],
            hot=lambda: self.leveling.view()["hot_auto"],
            weekly=lambda: self.leveling.view()["hot_windows"],
            maint_inputs=self._maint_inputs, clock=today_clock or time.time,
            multi_character=lambda: self.settings.view()["settings"]["profile.multi_character"])
        # Plan 031: NA world boss table + operator loot ticks.
        self.bosses = bosses.BossService(self.store, clock=bosses_clock or time.time)
        # Plan 072: daily robots-gated diff against a public NA table; off
        # unless a client is passed (only main() passes the live one).
        self.drift = bossdrift.DriftService(drift_client, self.bosses.table, spawn=drift_spawn)
        # Plan 043: operator-typed pet roster over the tracked pets.json rules.
        self.pets = pets.PetService(self.store)
        # Plan 045: operator-typed weight / storage planner; VP from plan 005's
        # buff timer (else the market.vp override, plan 079), sales priced per plan 027.
        # Plan 081: base LT / slots from the newest inventory screenshot read.
        self.inventory = inventory.InventoryService(
            self.store, buffs=lambda: self.grind.view()["buffs"], tax=self.tax,
            reads=self._ocr_read, fame=self._fame_row)
        # Plan 044: operator-typed mounts + sourced T10 breed rules.
        self.mounts = mounts.MountsService(self.store)
        # Plan 035: EV math on tracked rate rows; prices from the market cache only.
        self.enhance = enhance.EnhanceService(self.store, prices=self.market.cached_price)
        # Plan 036: stack advice reads the same effective rows (overrides included).
        # Plan 055: crystal / Caphras calculators price from the market cache only.
        self.deadeye = deadeye.DeadeyeService(self.store, clock=deadeye_clock or time.time,
                                              rates=self.enhance.rows,
                                              price=self.market.cached_price)
        # Plan 037: open plan steps x EV attempts x materials, cached prices only.
        self.shopping = shopping.ShoppingService(
            self.store, plan=lambda: self.deadeye.view()["plan"], enhance=self.enhance,
            quote=self.market.cached_quote, name=self.names.name,
            silver_per_h=lambda: shopping.average_silver_per_h(self.grind.view()["spots"]),
            watched=lambda: {w["id"] for w in self.market.watchlist.items() if w["sid"] == 0},
            clock=deadeye_clock or time.time,
            # Plan 081: silver on hand from the newest OCR read, hours / day from
            # the plan 062 play sessions; each supersedes the typed value.
            silver_read=lambda: self._ocr_read("silver"),
            play_hours=lambda: derived.hours_per_day(self.play.sessions(),
                                                     self.shopping.clock()))
        # Plan 054: operator recipes priced from the market cache only, taxed per plan 027.
        self.crafting = crafting.CraftingService(
            self.store, price=lambda iid: self.market.cached_price(iid),
            tax=self.tax, name=self.names.name)
        # Plan 071: auto-watch from the shopping list, the current / last spot's
        # loot and recipe inputs, curated before each watch refresh; cache prices only.
        self.autowatch = autowatch.AutoWatch(
            self.market.watchlist, shopping=self.shopping.view,
            loot=self.grind.loot_candidates, recipes=self.crafting.view,
            price=self.market.cached_price)
        self.market.curate = self.autowatch.sync
        if game_watch is None:
            # Only main() passes the real config; a bare make_server (every test)
            # never reads config/local.json (plan 008 refute round 1).
            cfg = {} if game_cfg is None else game_cfg
            game_watch = gamewatch.GameWatch.from_config(cfg)
        self.game = game_watch
        if detector is not None and isinstance(getattr(self.game, "pollers", None), list):
            detector.attach(self.game)  # detect now, again hourly while unconfigured
        # Plan 046: game exit -> play window recorded + grind.pending_stop (never a stop).
        self.summary = summary.SummaryService(self.store, self.grind,
                                              clock=grind_clock or time.time)
        # Plan 056: dice earned from logged-in minutes since the plan 021 dice reset.
        rule, grants, verified = today.dice_preset()
        self.dice = gamewatch.DiceClock(self.store, rule, grants, verified=verified,
                                        clock=today_clock or time.time)
        # Plan 062: login opens / exit (after a grace) closes an auto grind session.
        self.play = playsession.PlaySession(
            self.store, self.grind, self.summary, config=self._play_config,
            clock=grind_clock or time.time, on_change=lambda: self.bus.bump("grind"))
        self.grind.play = self.play.state
        # Plan 067: overlay widgets chosen by context (game state, clock, notices).
        self.context = context.ContextService(
            clock=context_clock or time.time, game=self.game.view,
            settings=self.eff_settings,
            boss_at=lambda now: (bosses.next_spawns(now, 1, self.bosses.table) or [{}])[0].get(
                "at_utc"),
            maint_window=lambda now: maint.next_window(
                now, maint.slot(self._maint_start()), self.notices.maint_notices()),
            reset_at=today.next_daily_reset, hot=self.leveling.hot_active)
        # Plan 075: logged-in minutes per UTC date -> qualifying login days per event.
        self.logindays = logindays.LoginDays(self.store, clock=events_clock or time.time)
        listeners = getattr(self.game, "listeners", None)
        if isinstance(listeners, list):
            listeners.append(self.summary.on_game)
            listeners.append(self.dice.on_game)
            listeners.append(self.play.on_game)
            listeners.append(self.logindays.on_game)
            listeners.append(lambda prev, new, at: self.context.invalidate())
        pollers = getattr(self.game, "pollers", None)
        if isinstance(pollers, list):
            pollers.append(self.play.tick)
            pollers.append(self.logindays.tick)
        if ocr_cache_dir is None:  # beside the store, so a test store keeps OCR in tmp too
            ocr_cache_dir = Path(store_root).parent / "ocr" if store_root else RUNTIME / "ocr"
        # Plan 040: loot import matches against the spot's grind loot list.
        self.ocr = ocr.OcrService(self.game, ocr_cache_dir, runner=ocr_runner,
                                  loot_names=self.grind.loot_names)
        # Plan 063: every shot taken while logged in is read on its own; confident
        # fields commit (undoable), the rest wait in the System review card.
        # Plan 066: + level / XP % samples, AP / AAP / DP, book-use suggestions,
        # silver/h over the plan 062 play session.
        self.ocr_auto = ocrauto.AutoOcr(
            self.store, self.game, self.ocr, self.grind,
            settings=lambda: self.settings.view()["settings"], clock=grind_clock or time.time,
            on_change=self._ocr_auto_changed, leveling=self.leveling, progress=self.progress,
            play=self.play.view, loot_target=self.grind.loot_target)
        # Plan 081: the running session's OCR loot counts prefill the loot form.
        self.grind.ocr_loot = self.ocr_auto.loot_prefill
        if isinstance(listeners, list):
            listeners.append(self.ocr_auto.on_game)
        # Plan 068: login / logged-minute rows tick themselves (undoable); a shot
        # near a boss spawn while logged in suggests that boss's loot tick.
        self.autotick = autotick.AutoTick(
            self.store, self.today, self.dice, self.game, table=self.bosses.table,
            settings=lambda: self.settings.view()["settings"], clock=today_clock or time.time,
            on_change=lambda: self.bus.bump("today"))
        if isinstance(listeners, list):
            listeners.append(self.autotick.on_game)
        if isinstance(pollers, list):
            pollers.append(self.autotick.on_poll)
        # Plan 082: FaceTexture portraits archived (read-only on the game's file),
        # characterNo from the session-log tail, bound to the Progress class.
        self.portraits = portraits.PortraitService(
            self.store, (Path(store_root).parent if store_root else RUNTIME) / "portraits",
            documents=lambda: getattr(self.game, "documents_dir", None),
            loads=lambda: self.game.view().get("char_loads") or [],
            progress_cls=lambda: self.progress._load()[0]["cls"],  # store read only, per poll
            clock=grind_clock or time.time, on_change=lambda: self.bus.bump("portraits"))
        if isinstance(pollers, list):
            pollers.append(self.portraits.poll)
        # Plan 070: prompt registry (expiry, dedupe) + game-closed quiet + ladder.
        self.prompts = prompts.PromptRegistry(
            self.store, clock=today_clock or time.time,
            settings=lambda: self.settings.view()["settings"])
        if isinstance(listeners, list):
            listeners.append(self.prompts.on_game)
        # Plan 051: first-run checklist over the same config file + store.
        # Plan 077: its folder steps read the signal digest (self.signals, below).
        self.onboarding = onboarding.OnboardingService(self.store, self.settings.path,
                                                       clock=today_clock or time.time,
                                                       detected=detected,
                                                       signals=lambda: self.signals.view())
        # Plan 053: imperial delivery planner; CP from plan 042's card, else typed.
        # Plan 081: else the newer of the typed CP and the newest CP screenshot read.
        self.imperial = imperial.ImperialService(
            self.store, clock=today_clock or time.time,
            cp=lambda: (self.progress.lifeskill_view().get("cp") or {}).get("value"),
            prices=self.market.cached_prices, name=self.names.name, reads=self._ocr_read)
        # Plan 069: next best action over the views above; never fetches (market
        # prices come from the cache only, coupons / notices from the store).
        self.whatnow = whatnow.WhatNowService({
            "bosses": self.bosses.view, "today": self.today_view, "grind": self.grind.view,
            "leveling": self.leveling.view, "maint": self._maint_inputs,
            "events": self.events.view, "maint_digest": self.maintdigest.view,
            "market": self._market_alerts,
            "ocr": lambda: self.ocr_auto_out(self.ocr_auto.view()),  # plan 070 gate
            "logins": lambda: self.login_days(self.events.view()["items"])},  # plan 075
            clock=today_clock or time.time)
        # Plan 073: per-signal liveness over local state only (never fetches or polls).
        self.signals = signals.SignalService({
            "session_log": self._sig_session_log, "screenshots": self._sig_screenshots,
            "ocr": self._sig_ocr, "notices": self._sig_notices, "market": self._sig_market,
            "profile": self._sig_profile, "boss_drift": lambda: None},
            clock=today_clock or time.time, overrides=self._sig_overrides)
        if game_poll:  # off by default so tests never probe processes; main() turns it on
            self.game.start()
            self.ocr_auto.start()

    # -- plan 079 override ledger --------------------------------------------------

    def _maint_end_after(self, at, value):
        """End (epoch) of the first maintenance ending after `at` with start `value`."""
        win = maint.next_window(_dt.datetime.fromtimestamp(at, _dt.timezone.utc),
                                maint.slot(value), self.notices.maint_notices())
        return win[1].timestamp() if win else None

    def _vp_live(self):
        """An armed plan 005 "Value Pack" timer (plan 066 OCR arms the same row)."""
        for b in self.grind.view()["buffs"]:
            left = b.get("left_s")
            if (str(b.get("name") or "").strip().lower() == inventory.VP_BUFF
                    and isinstance(left, int) and left > 0):
                return {"value": True, "signal": "vp_timer"}
        return None

    def _maint_live(self):
        """A plan 064 maintenance notice for the next maintenance date."""
        notices = self.notices.maint_notices()
        if not notices:
            return None
        now = _dt.datetime.fromtimestamp(float(self.overrides.clock()), _dt.timezone.utc)
        win = maint.next_window(now, maint.slot(""), notices)
        if win and win[0].date().isoformat() in notices:
            return {"value": "", "signal": "maint_notice"}
        return None

    def _live(self, fn):
        try:
            return fn()
        except Exception:  # noqa: BLE001 - an unreadable signal is "no live value"
            return None

    # -- plan 081 derived planner inputs ---------------------------------------

    def _ocr_read(self, kind):
        """Newest committed OCR read of `kind` {value, at, source}, or None."""
        auto = getattr(self, "ocr_auto", None)
        return auto.latest(kind) if auto is not None else None

    def _fame_live(self):
        """The newest market sell dialog fame read, when it is at least as new as
        the market.fame_pct override (typed or config) - a newer typed value wins."""
        r = self._ocr_read("fame")
        if r is None or not market._valid_fame(r.get("value")):
            return None
        e = self.overrides.doc()["live"].get("market.fame_pct")
        if e is not None:
            r_at, e_at = overrides._ts(r.get("at")), overrides._ts(e.get("set_at"))
            if r_at is None or (e_at is not None and r_at < e_at):
                return None
        return {"value": r["value"], "signal": "ocr_fame"}

    def _fame_row(self):
        """Fame bonus in force with its source and age (spec section 4)."""
        fame = self.tax()["fame_pct"]
        r = self._ocr_read("fame")
        now = float(self.overrides.clock())
        if r is not None and r.get("value") == fame and self._fame_live() is not None:
            row = derived.pick(None, r, now)
            return dict(row, value=fame, source="ocr")
        e = self.overrides.doc()["live"].get("market.fame_pct")
        if e is not None:
            return {"value": fame, "source": e["source"], "at": e["set_at"],
                    "age_s": None, "stale": False}
        return {"value": fame, "source": "default", "at": None, "age_s": None, "stale": False}

    def _hot_until(self, now):
        """Plan 081: end (utc datetime) of the next maintenance after `now`."""
        end = self._maint_end_after(now.timestamp(), self._maint_start())
        return _dt.datetime.fromtimestamp(end, _dt.timezone.utc) if end else None

    def eff_settings(self):
        """Settings with every ledger key resolved (plan 079): config imported
        as `source: config`, then live signal > unexpired override > default.
        The market keys resolve from the configured base `market.settings`."""
        view = self.settings.view()
        s, dflt = view["settings"], view["defaults"]
        base = self.market.settings
        s["market.vp"], s["market.fame_pct"] = base["vp"], base["fame_pct"]
        try:
            mtime = os.stat(self.settings.path).st_mtime
        except OSError:
            mtime = float(self.overrides.clock())
        self.overrides.sync_config(s, dflt, mtime)
        live = {"market.vp": self._vp_live, "events.maintenance_start_utc": self._maint_live,
                "market.fame_pct": self._fame_live}
        spec = {k: (self._live(live[k]) if k in live else None, dflt[k]) for k in s}
        for k, out in self.overrides.effective_many(spec).items():
            s[k] = out["value"]
        return s

    def tax(self):
        """{vp, fame_pct} in force for every net-proceeds reader (plan 027 / 079)."""
        s = self.eff_settings()
        return market.settings_from({"market": {"vp": s["market.vp"],
                                                "fame_pct": s["market.fame_pct"]}})

    def _maint_start(self):
        return self.eff_settings()["events.maintenance_start_utc"]

    def _derived_overrides(self, now):
        """Typed values whose ledger lives elsewhere: gear AP / AAP / DP
        (plan 066 `gs_src`, superseded by OCR) and manual watch thresholds."""
        out = []
        try:
            ch = self.progress.view(refresh=False)["character"]
        except Exception:  # noqa: BLE001 - a progress fault lists nothing
            ch = {}
        gs = ch.get("gs") if isinstance(ch.get("gs"), dict) else {}
        for stat, src in sorted((ch.get("gs_src") or {}).items()):
            if isinstance(src, dict) and src.get("source") == "typed":
                out.append({"key": f"gear.{stat}", "label": f"Gear {stat.upper()} (typed)",
                            "value": gs.get(stat), "source": "typed", "set_at": src.get("at"),
                            "expires_at": None, "expires_in_s": None,
                            "reason": "the next OCR read supersedes it", "cards": ["progress"],
                            "clearable": False})
        # Plan 081: typed planner inputs still in force (a newer live read clears them).
        for svc in (self.shopping, self.inventory, self.imperial):
            try:
                rows = svc.typed_overrides()
            except Exception:  # noqa: BLE001 - a service fault lists nothing
                rows = []
            for r in rows:
                out.append(dict(r, source="typed", expires_at=None, expires_in_s=None,
                                clearable=False))
        for w in self.market.watchlist.items():
            if w.get("auto") is True or (w.get("below") is None and w.get("above") is None):
                continue
            out.append({"key": f"market.watch.{w['id']}.{w['sid']}",
                        "label": "Watch threshold: " + str(self.names.name(w["id"], w["sid"])),
                        "value": {"below": w.get("below"), "above": w.get("above")},
                        "source": "typed", "set_at": None, "expires_at": None,
                        "expires_in_s": None, "reason": "typed on the Market tab",
                        "cards": ["market"], "clearable": False})
        return out

    def overrides_view(self):
        """GET /api/overrides: active entries (labels, value, set-at, expires-in,
        affected cards) after live signals and expiry have retired theirs."""
        self.eff_settings()
        self.overrides.sweep()
        now = float(self.overrides.clock())
        items = self.overrides.items(now, labels=getattr(settings, "LABELS", None))
        items += self._derived_overrides(now)
        return {"count": len(items), "items": items, "history": self.overrides.history()[:20]}

    def _sig_overrides(self):
        v = self.overrides_view()
        return {"count": v["count"], "items": v["items"]}

    # -- plan 073 signal inputs ------------------------------------------------

    def _game_health(self):
        fn = getattr(self.game, "health", None)
        return fn() if fn is not None else None

    def _sig_session_log(self):
        h = self._game_health()
        return None if h is None else {k: h[k] for k in (
            "configured", "log_dir_ok", "state", "line_at", "since", "polled_at")} | {
            "install_ok": h.get("install_ok")}

    def _sig_screenshots(self):
        h = self._game_health()
        return None if h is None else {"configured": h["shots_configured"],
                                       "documents_ok": h.get("documents_ok"),
                                       "dir_ok": h["shots_dir_ok"], "state": h["state"],
                                       "last_at": h["last_shot_at"]}

    def _sig_ocr(self):
        h = self._game_health()
        return dict(self.ocr_auto.health(), state=h["state"] if h else None)

    def _sig_notices(self):
        client = self.notices.client
        if client is None:
            return {"enabled": False}
        return dict(client.attempt(), enabled=True)

    def _sig_market(self):
        return self.market.health()

    def _sig_profile(self):
        src = self.progress.source()
        reason = self.progress._off_reason() if src["status"] == "off" else None
        return {"status": src["status"], "reason": reason, "updated": src["updated"]}

    def _ocr_auto_changed(self):
        self.bus.bump("ocr")
        self.bus.bump("grind")
        self.bus.bump("progress")  # plan 066: gs stats (leveling bumps its own seq)

    def _play_config(self):
        s = self.settings.view()["settings"]
        return s["play.auto_session"], s["play.grace_s"]

    def _maint_inputs(self):
        """Plan 069: the effective maintenance slot + official notices (plan 059 / 064)."""
        return {"slot": maint.slot(self._maint_start()),
                "notices": maint.clean_notices(self.store.get("maint_notices"))}

    def _market_alerts(self):
        """Plan 069: watched items' alerts from the sublist cache only (never fetches)."""
        out = []
        for w in self.market.watchlist.items():
            price = self.market.cached_price(w["id"], w["sid"])
            bands = self.market.bands(w["id"], w["sid"]) if w.get("p20") is True else None
            out.append({"id": w["id"], "name": self.names.name(w["id"], w["sid"]), "price": price,
                        "below": w.get("below"), "above": w.get("above"),
                        "alert": market.alert_for(price, w.get("below"), w.get("above"),
                                                  bands=bands, p20=w.get("p20") is True)})
        return out

    def _weekly_character(self):
        ch = self.progress.view(refresh=False)["character"]
        return dict(ch, level=self.progress._level(ch))

    # -- plan 070: stale prompts vanish from their payloads ------------------------

    # Age = first seen by this registry, never the row's own date (a post date
    # is not when the operator was first prompted).
    def events_out(self, out, sug, sug_ev):
        sug = dict(sug, candidates=self.prompts.keep(
            "coupon", sug.get("candidates"), lambda c: c["code"].upper()))
        sug_ev = dict(sug_ev, candidates=self.prompts.keep(
            "event_suggestion", sug_ev.get("candidates"), lambda c: str(c["group_no"])))
        return dict(out, suggested=sug, suggested_events=sug_ev,
                    login_days=self.login_days(out.get("items")))

    def login_days(self, items):
        """Plan 075 `login_days` block: tracked rows + notice rule suggestions
        (cached Detail reads only, never a fetch)."""
        return self.logindays.rows(items, self.notices.login_suggestions())

    def grind_out(self, out):
        pend = out.get("pending_stop")
        kept = self.prompts.keep("pending_stop", [pend] if pend else [],
                                 lambda p: f"{p['started']}:{p['at']}")
        return dict(out, pending_stop=kept[0] if kept else None)

    def ocr_auto_out(self, out):
        return dict(out, review=self.prompts.keep("ocr_review", out.get("review"),
                                                  lambda r: str(r["id"])))

    def prompt_timers(self, now):
        """Time-based ladder sources the dashboard cannot see on its own:
        daily / weekly reset and the next maintenance start."""
        when = _dt.datetime.fromtimestamp(now, _dt.timezone.utc)
        out = []
        for name, title, fn in (("daily", "Daily reset", today.next_daily_reset),
                                ("weekly", "Weekly reset", today.next_weekly_reset)):
            at = fn(when).timestamp()
            out.append({"key": f"resetSoon:{name}:{int(at)}", "at": at, "title": title})
        try:
            win = maint.next_window(when, maint.slot(self._maint_start()),
                                    self.notices.maint_notices())
        except Exception:  # noqa: BLE001 - a bad maintenance source never breaks the view
            win = None
        if win and win[0] > when:
            at = win[0].timestamp()
            out.append({"key": f"resetSoon:maint:{int(at)}", "at": at, "title": "Maintenance"})
        return out

    def maint_loss_timers(self):
        """Plan 074: unacked loss warnings for the maintLoss notify rule (ladder
        + the T-24 h / T-1 h toasts); acked ones never notify."""
        try:
            warns = self.maintdigest.unacked()
        except Exception:  # noqa: BLE001 - a bad digest source never breaks the view
            return []
        return [{"key": f"maintLoss:{w['key']}", "at": _dt.datetime.fromisoformat(w["due_utc"]).timestamp(),
                 "title": "Before maintenance", "text": w["text"]} for w in warns]

    def prompts_view(self):
        now = float(self.prompts.clock())
        return self.prompts.view(game_state=self.game.view().get("state"),
                                 timers=self.prompt_timers(now),
                                 maint_loss=self.maint_loss_timers())

    def today_view(self, body=None):
        """GET /api/today: plan 003 checklist + plan 033 `weekly_plan` + plan 056 `dice`
        + plan 068 `ready` on ready_minutes rows."""
        if body is None:
            self.autotick.evaluate()
            body = self.today.view()
        return dict(self.autotick.decorate(body),
                    weekly_plan=self.weekly.view(), dice=self.dice.status())

    def bosses_view(self, body=None, refresh=False):
        """GET /api/bosses: plan 031 table + loot ticks + plan 068 `suggested` +
        plan 072 `drift` (only GET starts its once-a-day refresh)."""
        out = self.autotick.boss_view(self.bosses.view() if body is None else body)
        return dict(out, drift=self.drift.view(refresh=refresh))

    def server_close(self):
        self.ocr_auto.stop()
        self.game.stop()
        super().server_close()

    def version(self):
        return {"commit": self.commit, "started": self.started, "pid": os.getpid(),
                "config_hash": self.cfg_hash, "schema": 1}

    def state(self):
        return {"app": "ebonwake", "version": __version__, "tabs": TABS,
                "sources": {"market": self.market.source(), "today": self.today.source(),
                            "profile": self.progress.source(), "grind": self.grind.source(),
                            "events": self.events.source(), "coupons": self.coupons.source(),
                            "deadeye": self.deadeye.source(), "bossdrift": self.drift.source(),
                            "game": self.game.source(), "leveling": self.leveling.source()},
                "now": _now_iso()}


class Handler(BaseHTTPRequestHandler):
    server_version = "Ebonwake"
    sys_version = ""

    def log_message(self, fmt, *args):  # quiet: no stderr spam under pythonw
        pass

    def _host_ok(self):
        host = (self.headers.get("Host") or "").strip()
        name = host.rsplit(":", 1)[0] if not host.startswith("[") else host.split("]")[0] + "]"
        return name in LOOPBACK_HOSTS

    def _send(self, code, body, ctype="application/json", cors=True):
        data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        if cors:
            self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def _redirect(self, location):
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _market_item(self, query):
        q = parse_qs(query)
        try:
            item_id = int(q["id"][0])
            sid = int(q.get("sid", ["0"])[0])
        except (KeyError, ValueError, IndexError):
            return self._send(400, {"error": "id and sid must be ints"})
        if not (0 <= item_id <= market.MAX_ID and 0 <= sid <= market.MAX_ID):
            return self._send(400, {"error": "id and sid out of range"})
        return self._send(200, self.server.market.item(item_id, sid))

    def do_GET(self):  # noqa: N802
        if not self._host_ok():
            return self._send(403, {"error": "host"})
        path, _, query = self.path.partition("?")
        if path == "/api/health":
            return self._send(200, {"ok": True, "app": "ebonwake", "port": ports.SERVER})
        if path == "/api/version":
            return self._send(200, self.server.version())
        if path == "/api/state":
            return self._send(200, self.server.state())
        if path == "/api/market/watch":
            return self._send(200, self.server.market.watch())
        if path == "/api/market/item":
            return self._market_item(query)
        if path == "/api/market/hot":
            return self._send(200, self.server.market.hot())
        if path == "/api/market/search":
            try:
                rows = self.server.names.search(parse_qs(query).get("q", [None])[0])
            except ValueError as e:
                return self._send(400, {"error": str(e)})
            return self._send(200, {"items": rows})
        if path == "/api/today":
            return self._send(200, self.server.today_view())
        if path == "/api/progress":
            return self._send(200, self.server.progress.view())
        if path == "/api/progress/history":
            try:
                return self._send(200, self.server.progress.history_view(parse_qs(query)))
            except ValueError as e:
                return self._send(400, {"error": str(e)})
        if path == "/api/grind":
            return self._send(200, self.server.grind_out(self.server.grind.view()))
        if path == "/api/grind/loot":
            try:
                spot = parse_qs(query).get("spot", [None])[0]
                return self._send(200, self.server.grind.loot(spot))
            except ValueError as e:
                return self._send(400, {"error": str(e)})
        if path == "/api/events":
            # plan 064: the notice read (and its auto-add) first, so items include it
            sug_ev = self.server.notices.view(refresh=True)
            sug = self.server.coupons.view(refresh=True)
            return self._send(200, self.server.events_out(self.server.events.view(), sug,
                                                          sug_ev))
        if path == "/api/deadeye":
            return self._send(200, self.server.deadeye.view())
        if path == "/api/deadeye/enhance":
            try:
                return self._send(200, self.server.enhance.query(parse_qs(query)))
            except ValueError as e:
                return self._send(400, {"error": str(e)})
        if path == "/api/deadeye/shopping":
            return self._send(200, self.server.shopping.view())
        if path == "/api/deadeye/calc":
            try:
                return self._send(200, self.server.deadeye.calc(parse_qs(query)))
            except ValueError as e:
                return self._send(400, {"error": str(e)})
        if path == "/api/game":
            return self._send(200, self.server.game.view())
        if path == "/api/ocr/auto":
            return self._send(200, self.server.ocr_auto_out(self.server.ocr_auto.view()))
        if path == "/api/leveling":
            self.server.notices.view(refresh=False)  # plan 064: import cached Hot Time reads
            return self._send(200, self.server.leveling.view())
        if path == "/api/bosses":
            return self._send(200, self.server.bosses_view(refresh=True))
        if path == "/api/prompts":
            return self._send(200, self.server.prompts_view())
        if path == "/api/overlay/context":
            return self._send(200, self.server.context.view())
        if path == "/api/spots":
            try:
                return self._send(200, self.server.spots.view(parse_qs(query)))
            except ValueError as e:
                return self._send(400, {"error": str(e)})
        if path == "/api/settings":
            return self._send(200, self.server.settings.view())
        if path == "/api/pets":
            q = parse_qs(query)
            if "exchange" not in q:
                return self._send(200, self.server.pets.view())
            try:
                return self._send(200, self.server.pets.exchange(q["exchange"][0]))
            except ValueError as e:
                return self._send(400, {"error": str(e)})
        if path == "/api/inventory":
            return self._send(200, self.server.inventory.view())
        if path == "/api/mounts":
            return self._send(200, self.server.mounts.view())
        if path == "/api/summary":
            return self._send(200, self.server.summary.view())
        if path == "/api/onboarding":
            return self._send(200, self.server.onboarding.view())
        if path == "/api/crafting":
            return self._send(200, self.server.crafting.view())
        if path == "/api/imperial":
            return self._send(200, self.server.imperial.view())
        if path == "/api/whatnow":
            return self._send(200, self.server.whatnow.view())
        if path == "/api/maint/digest":
            self.server.notices.view(refresh=False)  # plan 064: import cached maintenance reads
            return self._send(200, self.server.maintdigest.view())
        if path == "/api/signals":
            return self._send(200, self.server.signals.view())
        if path == "/api/overrides":
            return self._send(200, self.server.overrides_view())
        if path == "/api/portraits":
            return self._send(200, self.server.portraits.view(
                char_no=self.server.game.view().get("char_no")))
        if path.startswith("/api/portraits/img/"):  # plan 082: an indexed id, never a path
            size = parse_qs(query).get("size", ["full"])[0]
            png = self.server.portraits.image(path[len("/api/portraits/img/"):], size)
            if png is None:
                return self._send(404, {"error": "not found"})
            return self._send(200, png, "image/png")
        if path == "/events":
            return self._sse()
        if path == "/":  # plan 020: redirect so relative asset paths resolve
            return self._redirect(DASHBOARD_PAGE)
        if path == "/ops/fleet_kit/tokens.css":
            return self._file(KIT_TOKENS)
        if path.startswith("/app/"):
            target = (APP_DIR / path[len("/app/"):]).resolve()
            if APP_DIR.resolve() in target.parents and "node_modules" not in target.parts:
                return self._file(target)
        return self._send(404, {"error": "not found"})

    def _post_market_watch(self, body):
        if len(body) != 1 or not ({"add", "remove"} & set(body)):
            raise ValueError("body must be {\"add\": {...}} or {\"remove\": {...}}")
        wl = self.server.market.watchlist
        return {"watch": wl.add(body["add"]) if "add" in body else wl.remove(body["remove"])}

    def _post_today(self, body):
        ops = {"tick", "untick", "add", "remove", "move", "weekly_tick", "weekly_untick"}
        if len(body) != 1 or not (ops & set(body)):
            raise ValueError("body must be one of "
                             "{tick|untick|add|remove|move|weekly_tick|weekly_untick: ...}")
        (op, arg), = body.items()
        if op.startswith("weekly_"):  # plan 033
            getattr(self.server.weekly, op[len("weekly_"):])(arg)
            return self.server.today_view()
        return self.server.today_view(getattr(self.server.today, op)(arg))

    def _post_progress(self, body):
        ops = {"character": "set_character", "step": "step", "add_track": "add_track",
               "remove_track": "remove_track", "claim": "claim", "obj_add": "obj_add",
               "obj_edit": "obj_edit", "obj_del": "obj_del", "brackets_set": "brackets_set",
               "track_seed": "track_seed"}
        if len(body) != 1 or not (set(ops) & set(body)):
            raise ValueError("body must be one of {character|step|add_track|remove_track|"
                             "claim|obj_add|obj_edit|obj_del|brackets_set|track_seed: ...}")
        (op, arg), = body.items()
        return getattr(self.server.progress, ops[op])(arg)

    def _post_grind(self, body):
        ops = {"start", "stop", "log", "delete", "add_spot", "buff", "clear_buff",
               "drop_toggle", "drop_override", "loot_item", "loot_forget", "keep"}
        if len(body) != 1 or not (ops & set(body)):
            raise ValueError("body must be one of {start|stop|log|delete|add_spot|buff|"
                             "clear_buff|drop_toggle|drop_override|loot_item|loot_forget|"
                             "keep: ...}")
        (op, arg), = body.items()
        out = getattr(self.server.grind, op)(arg)
        if op == "stop":  # plan 062: a manual Stop overrides the auto play session
            self.server.play.manual_stop()
            out = self.server.grind.view()
        if isinstance(out, dict) and "pending_stop" in out:
            out = self.server.grind_out(out)
        return out

    def _post_events(self, body):
        ops = {"add", "edit", "done", "delete", "purge_expired", "dismiss_notice",
               "undo_notice", "login_mark"}
        if len(body) != 1 or not (ops & set(body)):
            raise ValueError("body must be one of {add|edit|done|delete|purge_expired|"
                             "dismiss_notice|undo_notice|login_mark: ...}")
        (op, arg), = body.items()
        if op == "login_mark":  # plan 075: "I logged in that day" (operator data)
            self.server.logindays.mark(arg)
            sug_ev = self.server.notices.view(refresh=False)
            out = self.server.events.view()
        elif op in ("dismiss_notice", "undo_notice"):  # plan 059 / plan 064
            getattr(self.server.notices, "dismiss" if op == "dismiss_notice" else "undo")(arg)
            sug_ev = self.server.notices.view(refresh=False)
            out = self.server.events.view()
        else:
            out = getattr(self.server.events, op)(arg)
            sug_ev = self.server.notices.view(refresh=False)
        # a POST never fetches
        return self.server.events_out(out, self.server.coupons.view(refresh=False), sug_ev)

    def _post_deadeye(self, body):
        ops = {"note", "add_step", "edit_step", "step_done", "delete_step", "move_step",
               "fs_add", "fs_use", "agris_set", "crons_set"}  # plan 036: the last four
        rates = {"rate_set": "override_set", "rate_del": "override_del"}  # plan 035
        shop = {"shop_set": "set", "shop_step": "step"}  # plan 037
        if len(body) != 1 or not ((ops | set(rates) | set(shop)) & set(body)):
            raise ValueError("body must be one of {note|add_step|edit_step|step_done|"
                             "delete_step|move_step|fs_add|fs_use|agris_set|crons_set|"
                             "rate_set|rate_del|shop_set|shop_step: ...}")
        (op, arg), = body.items()
        if op in rates:
            return getattr(self.server.enhance, rates[op])(arg)
        if op in shop:
            return getattr(self.server.shopping, shop[op])(arg)
        return getattr(self.server.deadeye, op)(arg)

    def _post_settings(self, body):
        led = self.server.overrides
        clear = None
        if isinstance(body, dict) and set(body) == {"clear"}:
            # Plan 079: {"clear": key} retires the override and puts the key back
            # to its default (so config/local.json never re-imports it).
            clear = body["clear"]
            if not (isinstance(clear, str) and clear in settings.SPEC and led.covers(clear)):
                raise ValueError("clear: not a clearable override key")
            body = {"set": {clear: settings.SPEC[clear][1]}}
        out = self.server.settings.apply(body)
        dflt = out["defaults"]
        for key, v in body["set"].items():  # typed writes become ledger entries
            if not led.covers(key):
                continue
            if v == dflt[key]:
                led.clear(key, by="operator")
            else:
                led.set(key, v, source="typed", reason="typed in Settings")
        if any(k.startswith("market.") for k in out["changed"]):  # net proceeds apply live
            s = out["settings"]
            self.server.market.settings = market.settings_from(
                {"market": {"vp": s["market.vp"], "fame_pct": s["market.fame_pct"]}})
        # Plan 065: a "use other" folder (or blank = back to detected) applies live.
        det = self.server.detector
        if det is not None and any(k.startswith("bdo.") for k in out["changed"]):
            det.apply()
        self.server.context.invalidate()  # plan 067: pins / blocks / auto apply live
        out["overrides"] = self.server.overrides_view()
        return out

    def _post_ocr(self, body):
        # Plan 063: {"review": {id, action, value?}} / {"undo": id} act on the
        # auto-OCR queue; every other body is a plan 009 / 040 read.
        if isinstance(body, dict) and set(body) == {"review"}:
            return self.server.ocr_auto.review(body["review"])
        if isinstance(body, dict) and set(body) == {"undo"}:
            return self.server.ocr_auto.undo(body["undo"])
        return self.server.ocr.read(body)

    def _post_leveling(self, body):
        ops = {"sample": "sample", "sample_del": "sample_del", "hot_add": "hot_add",
               "hot_del": "hot_del", "milestones": "set_milestones",
               "epoch_add": "epoch_add", "epoch_del": "epoch_del",
               "deadline_set": "deadline_set", "deadline_del": "deadline_del",
               "book_add": "book_add", "book_use": "book_use", "book_del": "book_del"}
        if len(body) != 1 or not (set(ops) & set(body)):
            raise ValueError("body must be one of {sample|sample_del|hot_add|hot_del|"
                             "milestones|epoch_add|epoch_del|deadline_set|deadline_del|"
                             "book_add|book_use|book_del: ...}")
        (op, arg), = body.items()
        return getattr(self.server.leveling, ops[op])(arg)

    def _post_bosses(self, body):
        ops = {"tick", "untick"}
        if len(body) != 1 or not (ops & set(body)):
            raise ValueError("body must be one of {tick|untick: {boss, day}}")
        (op, arg), = body.items()
        return self.server.bosses_view(getattr(self.server.bosses, op)(arg))

    def _post_pets(self, body):
        ops = {"add": "add", "edit": "edit", "remove": "remove", "feed": "feed",
               "goals": "set_goals"}
        if len(body) != 1 or not (set(ops) & set(body)):
            raise ValueError("body must be one of {add|edit|remove|feed|goals: ...}")
        (op, arg), = body.items()
        return getattr(self.server.pets, ops[op])(arg)

    def _post_inventory(self, body):
        ops = ("set", "source", "town_add", "town_edit", "town_del", "sale", "sale_del")
        if len(body) != 1 or not (set(ops) & set(body)):
            raise ValueError("body must be one of {" + "|".join(ops) + ": ...}")
        (op, arg), = body.items()
        return getattr(self.server.inventory, op)(arg)

    def _post_mounts(self, body):
        ops = {"add", "edit", "delete", "materials", "fern_rate", "failures"}
        if len(body) != 1 or not (ops & set(body)):
            raise ValueError("body must be one of "
                             "{add|edit|delete|materials|fern_rate|failures: ...}")
        (op, arg), = body.items()
        return getattr(self.server.mounts, op)(arg)

    def _post_onboarding(self, body):
        ops = {"dismiss", "restore"}
        if len(body) != 1 or not (ops & set(body)):
            raise ValueError("body must be {\"dismiss\": true} or {\"restore\": true}")
        (op, arg), = body.items()
        return getattr(self.server.onboarding, op)(arg)

    def _post_crafting(self, body):
        ops = {"add", "edit", "delete"}
        if len(body) != 1 or not (ops & set(body)):
            raise ValueError("body must be one of {add|edit|delete: ...}")
        (op, arg), = body.items()
        return getattr(self.server.crafting, op)(arg)

    def _post_imperial(self, body):
        ops = {"deliver": "deliver", "cp": "set_cp", "mastery": "set_mastery",
               "box_add": "box_add", "box_del": "box_del"}
        if len(body) != 1 or not (set(ops) & set(body)):
            raise ValueError("body must be one of {deliver|cp|mastery|box_add|box_del: ...}")
        (op, arg), = body.items()
        return getattr(self.server.imperial, ops[op])(arg)

    def _post_maint_digest(self, body):
        if set(body) != {"ack"}:
            raise ValueError("body must be {\"ack\": \"<warning key>\"}")
        self.server.notices.view(refresh=False)  # plan 064: import cached maintenance reads
        return self.server.maintdigest.ack(body["ack"])

    POST_ROUTES = {"/api/market/watch": _post_market_watch, "/api/today": _post_today,
                   "/api/progress": _post_progress, "/api/grind": _post_grind,
                   "/api/events": _post_events, "/api/deadeye": _post_deadeye,
                   "/api/ocr": _post_ocr, "/api/leveling": _post_leveling,
                   "/api/bosses": _post_bosses, "/api/settings": _post_settings,
                   "/api/pets": _post_pets, "/api/inventory": _post_inventory,
                   "/api/mounts": _post_mounts, "/api/onboarding": _post_onboarding,
                   "/api/crafting": _post_crafting, "/api/imperial": _post_imperial,
                   "/api/maint/digest": _post_maint_digest}

    def do_POST(self):  # noqa: N802
        """Shared guard for every POST route: loopback Host + application/json +
        <= 4 KiB (more only via POST_CAPS) + a JSON object body. No CORS header is sent and OPTIONS is never
        answered, so browser pages cannot make this request. A route handler gets
        the parsed dict and returns the 200 body or raises ValueError (-> 400)."""
        unread = [True]

        def reply(code, body):
            if unread[0]:  # drain a small unread body so the close is not a reset
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    n = 0
                while 0 < n <= DRAIN_MAX_BYTES:  # chunked; loopback only, bounded
                    chunk = self.rfile.read(min(n, 1 << 16))
                    if not chunk:
                        break
                    n -= len(chunk)
            self.close_connection = True
            return self._send(code, body, cors=False)

        if not self._host_ok():
            return reply(403, {"error": "host"})
        rpath = self.path.split("?", 1)[0]
        route = self.POST_ROUTES.get(rpath)
        cap = POST_CAPS.get(rpath, MAX_POST_BYTES)
        if route is None:
            return reply(404, {"error": "not found"})
        ctype = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if ctype != "application/json":
            return reply(415, {"error": "content-type must be application/json"})
        try:
            length = int(self.headers.get("Content-Length") or "")
        except ValueError:
            return reply(411, {"error": "length required"})
        if length < 0 or length > cap:
            return reply(413, {"error": f"body over {cap} bytes"})
        raw = self.rfile.read(length)
        unread[0] = False
        try:
            body = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return reply(400, {"error": "bad json"})
        if not isinstance(body, dict):
            return reply(400, {"error": "body must be a JSON object"})
        try:
            out = route(self, body)
        except ValueError as e:
            return reply(400, {"error": str(e)})
        except ocr.OcrError as e:
            return reply(502, {"error": f"ocr failed: {e}"})
        if rpath in POST_DOMAINS:
            self.server.bus.bump(POST_DOMAINS[rpath])
        return reply(200, out)

    def _file(self, target):
        try:
            data = Path(target).read_bytes()
        except OSError:
            return self._send(404, {"error": "not found"})
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if str(target).endswith(".js"):
            ctype = "text/javascript"
        return self._send(200, data, ctype)

    def _sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        game, lev, bus, play = self.server.game, self.server.leveling, self.server.bus, \
            self.server.play
        ctx = self.server.context
        seq, lseq, dseq, pseq = game.seq, lev.seq, bus.snapshot(), play.seq
        csig = context.signature(ctx.view())
        wn = self.server.whatnow
        wseq, wcheck = wn.seq, time.monotonic()
        interval = self.server.sse_interval
        try:
            msg = json.dumps({"type": "heartbeat", "now": _now_iso()})
            self.wfile.write(f"data: {msg}\n\n".encode("utf-8"))
            self.wfile.flush()
            quiet = time.monotonic() + interval
            while True:
                # Named events (game, leveling) leave onmessage (heartbeat)
                # consumers untouched. Leveling changes are picked up within one
                # SSE_TICK_S slice of the game wait.
                left = quiet - time.monotonic()
                new = game.wait_change(seq, max(0.0, min(SSE_TICK_S, left)))
                out = []
                if new != seq:
                    seq = new
                    out.append(f"event: game\ndata: {json.dumps(game.view())}\n\n")
                if lev.seq != lseq:
                    lseq = lev.seq
                    try:  # a bad buff source never ends the stream (refute r1 minor 3)
                        out.append(f"event: leveling\ndata: {json.dumps(lev.view())}\n\n")
                    except Exception:  # noqa: BLE001
                        pass
                if play.seq != pseq:  # plan 062: {state: open|closed, id}
                    pseq = play.seq
                    ev = play.event()
                    if ev is not None:
                        out.append(f"event: play_session\ndata: {json.dumps(ev)}\n\n")
                cview = ctx.view()  # plan 067: cached CACHE_S; pushed only on change
                if context.signature(cview) != csig:
                    csig = context.signature(cview)
                    out.append(f"event: overlay_context\ndata: {json.dumps(cview)}\n\n")
                now_seq = bus.snapshot()
                changed = out or now_seq != dseq
                for d in sorted(k for k in now_seq if now_seq[k] != dseq.get(k)):
                    out.append(f"event: {d}\ndata: {json.dumps(d)}\n\n")
                dseq = now_seq
                # Plan 069: full view on change; a domain change forces a recompute.
                if changed or time.monotonic() >= wcheck:
                    wcheck = time.monotonic() + WHATNOW_CHECK_S
                    try:  # a bad source never ends the stream
                        new_w, wview = wn.refresh(0 if changed else WHATNOW_CHECK_S)
                    except Exception:  # noqa: BLE001
                        new_w = wseq
                    if new_w != wseq:
                        wseq = new_w
                        out.append(f"event: whatnow\ndata: {json.dumps(wview)}\n\n")
                if not out and time.monotonic() >= quiet:
                    msg = json.dumps({"type": "heartbeat", "now": _now_iso()})
                    out.append(f"data: {msg}\n\n")
                if out:
                    self.wfile.write("".join(out).encode("utf-8"))
                    self.wfile.flush()
                    quiet = time.monotonic() + interval
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            return


def make_server(port=ports.SERVER, store_root=None, commit=None, sse_interval=15.0,
                market_client=None, market_seed=None, today_clock=None,
                profile_client=None, profile_cfg=None, grind_clock=None, events_clock=None,
                deadeye_clock=None, game_watch=None, game_cfg=None, game_poll=False,
                ocr_runner=None, ocr_cache_dir=None, leveling_clock=None,
                coupon_client=None, coupon_spawn=None, bosses_clock=None,
                config_path=None, notice_client=None, notice_spawn=None, detector=None,
                context_clock=None, drift_client=None, drift_spawn=None):
    return EWServer(("127.0.0.1", port), store_root=store_root, commit=commit,
                    sse_interval=sse_interval, market_client=market_client,
                    market_seed=market_seed, today_clock=today_clock,
                    profile_client=profile_client, profile_cfg=profile_cfg,
                    grind_clock=grind_clock, events_clock=events_clock,
                    deadeye_clock=deadeye_clock, game_watch=game_watch,
                    game_cfg=game_cfg, game_poll=game_poll, ocr_runner=ocr_runner,
                    ocr_cache_dir=ocr_cache_dir, leveling_clock=leveling_clock,
                    coupon_client=coupon_client, coupon_spawn=coupon_spawn,
                    bosses_clock=bosses_clock, config_path=config_path,
                    notice_client=notice_client, notice_spawn=notice_spawn,
                    detector=detector, context_clock=context_clock,
                    drift_client=drift_client, drift_spawn=drift_spawn)


def _drift_client():
    """Plan 072: the live drift client when the data file carries a valid
    `drift` block; otherwise the check stays off (state unknown)."""
    try:
        cfg = bossdrift.config(bosses.load_table())
    except ValueError:
        return None
    return bossdrift.DriftClient(cfg) if cfg is not None else None


def main(argv=None, probe=None):
    # Single instance (plan 010): an EW server already on the port wins.
    if (probe or single.probe)() == "ew":
        return 0
    # Plan 030: settings coupons.check = false keeps coupon suggestions off.
    # Plan 059: events.notice_check = false keeps event-notice suggestions off.
    cfg = settings.Settings(CONFIG_PATH).view()["settings"]
    srv = make_server(commit=read_commit(), game_poll=True,
                      game_cfg=gamewatch.config_bdo(REPO_ROOT),
                      detector=detect.Detector(config=lambda: gamewatch.config_bdo(REPO_ROOT)),
                      coupon_client=coupons.CouponClient() if cfg["coupons.check"] else None,
                      notice_client=(eventnotices.NoticeClient()
                                     if cfg["events.notice_check"] else None),
                      drift_client=_drift_client())
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0

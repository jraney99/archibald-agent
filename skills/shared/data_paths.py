#!/usr/bin/env python3
"""Where every data file lives. Scripts ask here; they never type a data path.

WHY (2026-09-20): no rule said where data goes, so each session picked its own spot —
health logs ended up in three places (the root of ~/.openclaw, nutrition/config/,
shared/), and 147 of 337 scripts typed a full path themselves, 82 of them to a loose
file at the root. With the path repeated in every script, moving one file means
editing many, so nothing ever got tidied. On 2026-09-17 Archibald searched five places
before it found the food log.

THE RULE (full text: workspace/Documentation/rules/32-where-data-lives.md)
  Skills/<skill>/config/  settings the user chooses
  Skills/<skill>/data/    records that matter (meals, water, medication, transactions)
  Skills/<skill>/state/   throwaway bookkeeping, safe to delete
  One owner skill per file; shared/ holds code only; nothing new at the root.

    from data_paths import path, new_file
    LOG = path("hydration_log.json")                 # a registered file
    NEW = new_file("hydration", "state", "x.json")    # a brand-new one

MOVING A FILE IS SAFE BECAUSE OF THE `moved` FLAG. Until a name is in MOVED, path()
returns its old location — always, even when the file does not exist yet — so a script
that uses the helper and a script that still types the old path can never disagree
about where the file is. To move one: switch every user to path(), then move the file
and add the name to MOVED in the same change. test_data_layout.py enforces all of this.
"""
from __future__ import annotations

import os
from pathlib import Path

# The deployment root. Tests point OPENCLAW_HOME at a throwaway directory so that a
# helper resolved through here can never write live state (2026-10-02: watcher and
# occasion tests appended to the real context_prompt_attempts.jsonl). Production
# never sets it, so the default is the one place data has always lived.
OPENCLAW = Path(os.environ.get("OPENCLAW_HOME") or "~/.openclaw")
SKILLS = OPENCLAW / "workspace" / "Skills"
KINDS = ("config", "data", "state")

# name -> (owner skill, kind, where it lives today, relative to ~/.openclaw)
# owner "shared" = written by shared code and still needs a real owner before it moves.
REGISTRY: dict[str, tuple[str, str, str]] = {
    "finance_settled_mappings.generated.json": ("afrexai-personal-finance", "data", "workspace/Skills/afrexai-personal-finance/data/finance_settled_mappings.generated.json"),
    "statement_watch.json":                ("afrexai-personal-finance",  "state",  "workspace/Skills/afrexai-personal-finance/state/statement_watch.json"),
    # created 2026-10-09: the Spotify concert matcher. The taste graph is a derived
    # cache but an expensive one (9 API calls) and the concert log is the record of
    # which shows were surfaced to the user and when, so both are data; the dedupe table
    # is pure bookkeeping and safe to delete. The OAuth token is deliberately NOT
    # here: it is a credential, not data, and lives at spotify/state/spotify_token.json
    # at 0600 (same treatment the RUNTIME pattern below gives *token*.json).
    "spotify_config.json":                 ("spotify",                   "config", "workspace/Skills/spotify/config/spotify_config.json"),
    "spotify_taste.json":                  ("spotify",                   "data",   "workspace/Skills/spotify/data/spotify_taste.json"),
    "concert_log.json":                    ("spotify",                   "data",   "workspace/Skills/spotify/data/concert_log.json"),
    "concert_seen.json":                   ("spotify",                   "state",  "workspace/Skills/spotify/state/concert_seen.json"),
    "price_watch.json":                    ("spotify",                   "data",   "workspace/Skills/spotify/data/price_watch.json"),
    "onsale_seen.json":                    ("spotify",                   "state",  "workspace/Skills/spotify/state/onsale_seen.json"),
    # The play log is irreplaceable: Spotify keeps only the last 50 plays and has no
    # historical endpoint, so anything not collected is gone for good. Data, never state.
    "listening_log.jsonl":                 ("spotify",                   "data",   "workspace/Skills/spotify/data/listening_log.jsonl"),
    "listening_log_state.json":            ("spotify",                   "state",  "workspace/Skills/spotify/state/listening_log_state.json"),
    # created 2026-09-28 under the rule: wardrobe prompts that wait for a tap or a typed answer, and the packing list
    "pending_wear_photo.json":             ("ai-wardrobe-steward",       "state",  "workspace/Skills/ai-wardrobe-steward/state/pending_wear_photo.json"),
    "closet_purchase_prompts.json":        ("ai-wardrobe-steward",       "state",  "workspace/Skills/ai-wardrobe-steward/state/closet_purchase_prompts.json"),
    "packing_state.json":                  ("ai-wardrobe-steward",       "state",  "workspace/Skills/ai-wardrobe-steward/state/packing_state.json"),
    "wardrobe_travel.json":                ("ai-wardrobe-steward",       "config", "workspace/Skills/ai-wardrobe-steward/config/wardrobe_travel.json"),
    # created 2026-10-12: store registry (the user's brands + how each catalog is read); its throwaway cache is state/store_catalog/<slug>.json via new_file
    "stores.json":                         ("ai-wardrobe-steward",       "config", "workspace/Skills/ai-wardrobe-steward/config/stores.json"),
    # created 2026-10-12: Pinterest board import (public .rss feed -> profile/photos/inspiration/)
    "pinterest_boards.json":               ("ai-wardrobe-steward",       "config", "workspace/Skills/ai-wardrobe-steward/config/pinterest_boards.json"),
    "pinterest_board_watch.json":          ("ai-wardrobe-steward",       "state",  "workspace/Skills/ai-wardrobe-steward/state/pinterest_board_watch.json"),
    "inspiration_pins.json":               ("ai-wardrobe-steward",       "data",   "workspace/Skills/ai-wardrobe-steward/data/inspiration_pins.json"),
    "pinterest_digest.json":               ("ai-wardrobe-steward",       "state",  "workspace/Skills/ai-wardrobe-steward/state/pinterest_digest.json"),
    # created 2026-10-12: purchase loop (fit-verdict prompts after delivery)
    "fit_prompts.json":                    ("ai-wardrobe-steward",       "state",  "workspace/Skills/ai-wardrobe-steward/state/fit_prompts.json"),
    "pending_fit_detail.json":             ("ai-wardrobe-steward",       "state",  "workspace/Skills/ai-wardrobe-steward/state/pending_fit_detail.json"),
    # created 2026-10-13: 👍/👎 on what the Mon/Thu scan surfaces, so finds become taste data
    "find_feedback.json":                  ("ai-wardrobe-steward",       "state",  "workspace/Skills/ai-wardrobe-steward/state/find_feedback.json"),
    # created 2026-10-13: measured garment colours per catalog product id, so an image is fetched once
    "product_colors.json":                 ("ai-wardrobe-steward",       "state",  "workspace/Skills/ai-wardrobe-steward/state/product_colors.json"),
    # built as OPENCLAW / "name" rather than typed whole, so a literal-path search misses them
    "pending_txn_category.json":           ("afrexai-personal-finance",  "state",  "pending_txn_category.json"),
    "pending_p2p_category.json":           ("afrexai-personal-finance",  "state",  "pending_p2p_category.json"),
    "pending_bank_transfer_category.json": ("afrexai-personal-finance",  "state",  "pending_bank_transfer_category.json"),
    "hard_stop_pending.json":              ("shared",                    "state",  "hard_stop_pending.json"),
    # created 2026-09-20 under the rule, so it was born in its proper place
    "manual_wake.json":                    ("hatch-restore",             "state",  "workspace/Skills/hatch-restore/state/manual_wake.json"),
    "wake_sync_state.json":                ("hatch-restore",             "state",  "workspace/Skills/hatch-restore/state/wake_sync_state.json"),
    # created 2026-09-21 under the rule
    "daytrade.json":                       ("brokerage-trading",         "config", "workspace/Skills/brokerage-trading/config/daytrade.json"),
    "day_trades.json":                     ("brokerage-trading",         "state",  "workspace/Skills/brokerage-trading/state/day_trades.json"),
    # created 2026-09-27 under the rule: the user's nightly putting habit.
    "putting_log.json":                    ("golf-putting",              "data",   "workspace/Skills/golf-putting/data/putting_log.json"),
    "putting_cue_state.json":              ("golf-putting",              "state",  "workspace/Skills/golf-putting/state/putting_cue_state.json"),
    # created 2026-10-06 under the rule: the monthly tape-measurement prompt and the
    # typed answer it waits for (pending-context.mjs reads the pending file).
    "pending_measurement.json":            ("hevy-workout",              "state",  "workspace/Skills/hevy-workout/state/pending_measurement.json"),
    # created 2026-10-06 under the rule: the ChiliPad Dock Pro's settings, its sleep.me
    # device id, and the temperature trial — which rung, and how the user rated each night.
    "chilipad_config.json":                ("chilipad",                  "config", "workspace/Skills/chilipad/config/chilipad_config.json"),
    "chilipad_devices.json":               ("chilipad",                  "config", "workspace/Skills/chilipad/config/chilipad_devices.json"),
    "chilipad_raw_devices.json":           ("chilipad",                  "config", "workspace/Skills/chilipad/config/chilipad_raw_devices.json"),
    "chilipad_nights.json":                ("chilipad",                  "data",   "workspace/Skills/chilipad/data/chilipad_nights.json"),
    "chilipad_maintenance.json":           ("chilipad",                  "data",   "workspace/Skills/chilipad/data/chilipad_maintenance.json"),
    "chilipad_maintenance_prompt.json":    ("chilipad",                  "state",  "workspace/Skills/chilipad/state/chilipad_maintenance_prompt.json"),
    # created 2026-10-06 under the rule: the Nest thermostat's settings, its SDM device
    # name, and what the sleep/away setback changed so it can be put back.
    "nest_config.json":                    ("nest-thermostat",           "config", "workspace/Skills/nest-thermostat/config/nest_config.json"),
    "nest_devices.json":                   ("nest-thermostat",           "config", "workspace/Skills/nest-thermostat/config/nest_devices.json"),
    "nest_raw_devices.json":               ("nest-thermostat",           "config", "workspace/Skills/nest-thermostat/config/nest_raw_devices.json"),
    "nest_setback.json":                   ("nest-thermostat",           "state",  "workspace/Skills/nest-thermostat/state/nest_setback.json"),
    "car_config.json":                     ("car-maintenance",           "config", "workspace/Skills/car-maintenance/config/car_config.json"),
    "car_service_log.json":                ("car-maintenance",           "data",   "workspace/Skills/car-maintenance/data/car_service_log.json"),
    "car_odometer_log.json":               ("car-maintenance",           "data",   "workspace/Skills/car-maintenance/data/car_odometer_log.json"),
    "car_odometer_prompt_state.json":      ("car-maintenance",           "state",  "workspace/Skills/car-maintenance/state/car_odometer_prompt_state.json"),
    "car_brief_state.json":                ("car-maintenance",           "state",  "workspace/Skills/car-maintenance/state/car_brief_state.json"),
    "chilipad_rate_prompt.json":           ("chilipad",                  "state",  "workspace/Skills/chilipad/state/chilipad_rate_prompt.json"),
    # created 2026-10-08: stdout of a ramp that now runs detached from the cron worker.
    "ramp_detached.log":                   ("chilipad",                  "state",  "workspace/Skills/chilipad/state/ramp_detached.log"),
    "fade_detached.log":                   ("govee-lights",              "state",  "workspace/Skills/govee-lights/state/fade_detached.log"),
    "pending_odometer.json":               ("car-maintenance",           "state",  "workspace/Skills/car-maintenance/state/pending_odometer.json"),
    "pending_transcript.json":             ("call-transcriber",          "state",  "pending_transcript.json"),
    "commitments.sqlite":                 ("to-do", "data", "workspace/memory/commitments.sqlite"),
    "contacts.json":                       ("crm",                       "data",   "contacts.json"),
    "journal_notes.json":                  ("dr-physician",                   "data",   "workspace/Skills/shared/journal_notes.json"),
    "premeal_break_log.json":                ("dr-physician",                   "data",   "workspace/Skills/shared/premeal_break_log.json"),
    "sender_ledger.json":                  ("email-monitor",             "data",   "sender_ledger.json"),
    "cal_rename_seen.json":                ("email-monitor",             "state",  "cal_rename_seen.json"),
    "email_monitor_state.json":            ("email-monitor",             "state",  "email_monitor_state.json"),
    "flight_timing_state.json":            ("email-monitor",             "state",  "flight_timing_state.json"),
    "pending_cal_event.json":              ("email-monitor",             "state",  "pending_cal_event.json"),
    "pending_cal_queue.json":              ("email-monitor",             "state",  "pending_cal_queue.json"),
    "pending_cal_rename.json":             ("email-monitor",             "state",  "pending_cal_rename.json"),
    "pending_checked_bag.json":            ("email-monitor",             "state",  "pending_checked_bag.json"),
    "pending_crm.json":                    ("email-monitor",             "state",  "pending_crm.json"),
    "pending_diary_entry.json":            ("email-monitor",             "state",  "pending_diary_entry.json"),
    "pending_filter.json":                 ("email-monitor",             "state",  "pending_filter.json"),
    "pending_flight_log.json":             ("email-monitor",             "state",  "pending_flight_log.json"),
    "pending_morning_gratitude.json":      ("email-monitor",             "state",  "pending_morning_gratitude.json"),
    "pending_morning_journal.json":        ("email-monitor",             "state",  "pending_morning_journal.json"),
    "pending_unsub.json":                  ("email-monitor",             "state",  "pending_unsub.json"),
    "pending_uv_event.json":               ("email-monitor",             "state",  "pending_uv_event.json"),
    "golf-courses.json":                   ("golf-course-log",           "data",   "golf-courses.json"),
    "zoom_lighting_state.json":            ("govee-lights",              "state",  "workspace/Skills/shared/zoom_lighting_state.json"),
    # created 2026-09-30 under the rule, so it was born in its proper place
    "evening_hold.json":                   ("govee-lights",              "state",  "workspace/Skills/govee-lights/state/evening_hold.json"),
    "light_modes.json":                    ("govee-lights",              "config", "workspace/Skills/govee-lights/config/light_modes.json"),
    "govee_lan.json":                      ("govee-lights",              "state",  "workspace/Skills/govee-lights/state/govee_lan.json"),
    "sunrise_stop.json":                   ("govee-lights",              "state",  "workspace/Skills/govee-lights/state/sunrise_stop.json"),
    "chilipad_hold.json":                  ("chilipad",                  "state",  "workspace/Skills/chilipad/state/chilipad_hold.json"),
    "hatch_alarm_cache.json":              ("hatch-restore",             "state",  "workspace/Skills/hatch-restore/state/hatch_alarm_cache.json"),
    "grocery_digest_state.json":           ("grocery-expiration",        "state",  "grocery_digest_state.json"),
    "movement_log.json":                   ("hevy-workout",              "data",   "workspace/Skills/shared/movement_log.json"),
    "movement_reminder_state.json":        ("hevy-workout",              "state",  "movement_reminder_state.json"),
    "pending_equipment.json":              ("hevy-workout",              "state",  "pending_equipment.json"),
    "pending_vacation.json":               ("hevy-workout",              "state",  "pending_vacation.json"),
    "hydration_log.json":                  ("hydration",                 "data",   "hydration_log.json"),
    "hydration_nudge_state.json":          ("hydration",                 "state",  "hydration_nudge_state.json"),
    "hydration_reconcile_state.json":      ("hydration",                 "state",  "hydration_reconcile_state.json"),
    # created 2026-09-28 under the rule: last-pinged/snooze per open loop.
    "open_loop_escalation_state.json":     ("to-do",                     "state",  "workspace/Skills/to-do/state/open_loop_escalation_state.json"),
    "loop_closer_state.json":              ("loop-closer",               "state",  "loop_closer_state.json"),
    "evening_briefing_state.json":         ("morning-briefing",          "state",  "evening_briefing_state.json"),
    # born under the rule 2026-09-27, so it starts in its proper place
    "briefing_plan.json":                  ("morning-briefing",          "state",  "workspace/Skills/morning-briefing/state/briefing_plan.json"),
    "breakfast_anchor_log.json":           ("nutrition",                 "data",   "workspace/Skills/shared/breakfast_anchor_log.json"),
    "food_log.json":                       ("nutrition",                 "data",   "workspace/Skills/nutrition/config/food_log.json"),
    "readiness_log.json":                  ("nutrition",                 "data",   "workspace/Skills/shared/readiness_log.json"),
    "restrictions_log.json":               ("nutrition",                 "data",   "workspace/Skills/shared/restrictions_log.json"),
    "supplement_log.json":                 ("nutrition",                 "data",   "workspace/Skills/nutrition/config/supplement_log.json"),
    "breakfast_anchor_plan.json":          ("nutrition",                 "state",  "breakfast_anchor_plan.json"),
    "breakfast_anchor_sent.json":          ("nutrition",                 "state",  "breakfast_anchor_sent.json"),
    "energy_checkin_state.json":           ("nutrition",                 "state",  "energy_checkin_state.json"),
    "meal_nudge_index.json":               ("nutrition",                 "state",  "meal_nudge_index.json"),
    "meal_reminder_state.json":            ("nutrition",                 "state",  "meal_reminder_state.json"),
    "prep_confirm_state.json":             ("nutrition",                 "state",  "prep_confirm_state.json"),
    "pending_playbypoint_booking.json":    ("playbypoint-golf",          "state",  "pending_playbypoint_booking.json"),
    "meals_log.json":                      ("reminders",                 "data",   "meals_log.json"),
    "freezer_meals_reminder_state.json":   ("reminders",                 "state",  "freezer_meals_reminder_state.json"),
    "pending_restaurant_booking.json":     ("restaurant-booking",        "state",  "pending_restaurant_booking.json"),
    "pending_restaurant_cancel.json":      ("restaurant-booking",        "state",  "pending_restaurant_cancel.json"),
    "blind_spots.json":                    ("shared",                    "data",   "blind_spots.json"),
    "side_project.sqlite":                   ("side-project",                "data",   "workspace/Skills/side-project/data/side_project.sqlite"),
    "calendar_holds.json":                 ("shared",                    "data",   "calendar_holds.json"),
    # created 2026-10-11 under the rule: tee-time booking at the user's approved courses. The
    # approved list is his to edit (config); a booking he paid for is a record (data); the
    # proposal waiting for his tap is bookkeeping (state).
    "approved_courses.json":               ("golf-booking",              "config", "workspace/Skills/golf-booking/config/approved_courses.json"),
    "tee_time_bookings.json":              ("golf-booking",              "data",   "workspace/Skills/golf-booking/data/tee_time_bookings.json"),
    "pending_golf_booking.json":           ("golf-booking",              "state",  "workspace/Skills/golf-booking/state/pending_golf_booking.json"),
    "deal_watch_state.json":               ("golf-booking",              "state",  "workspace/Skills/golf-booking/state/deal_watch_state.json"),
    "chat_cleanup_state.json":             ("shared",                    "state",  "chat_cleanup_state.json"),
    "monitor_agent_cost_state.json":       ("shared",                    "state",  "monitor_agent_cost_state.json"),
    # created 2026-10-12 under the rule: The user's reading log (Audible + Kindle). Neither app
    # exposes progress, so the library and the position log are the only record — data.
    "reading_config.json":                 ("reading-tracker",           "config", "workspace/Skills/reading-tracker/config/reading_config.json"),
    "reading_library.json":                ("reading-tracker",           "data",   "workspace/Skills/reading-tracker/data/reading_library.json"),
    "reading_sessions.jsonl":              ("reading-tracker",           "data",   "workspace/Skills/reading-tracker/data/reading_sessions.jsonl"),
    # The Goodreads feed key is a credential, so it is NOT here: state/goodreads_feed_token.json at 0600.
    "goodreads_sync_state.json":           ("reading-tracker",           "state",  "workspace/Skills/reading-tracker/state/goodreads_sync_state.json"),
    # created 2026-10-12 under the rule: which podcast episodes the user listened to. He asked for it
    # because he "always forgets" — the log IS the memory, so it is data.
    "podcast_config.json":                 ("podcast-tracker",           "config", "workspace/Skills/podcast-tracker/config/podcast_config.json"),
    "podcast_log.jsonl":                   ("podcast-tracker",           "data",   "workspace/Skills/podcast-tracker/data/podcast_log.jsonl"),
    "spotify_podcast_sync.json":           ("podcast-tracker",           "state",  "workspace/Skills/podcast-tracker/state/spotify_podcast_sync.json"),
    "spotify_audiobook_sync.json":         ("reading-tracker",           "state",  "workspace/Skills/reading-tracker/state/spotify_audiobook_sync.json"),
    # The Audible device token is a credential, so it is NOT here: state/audible_auth_token.json at 0600.
    "audible_sync.json":                   ("reading-tracker",           "state",  "workspace/Skills/reading-tracker/state/audible_sync.json"),
    # created 2026-10-12 under the rule: the Zengge (ZENGGE app) Bluetooth light the user got from a
    # friend. Nickname -> Bluetooth name; the user chooses the nicknames, so it is config.
    "zengge_devices.json":                 ("zengge-lights",             "config", "workspace/Skills/zengge-lights/config/zengge_devices.json"),
    # the helper's unix socket (ble_helper.py): recreated at every start, safe to delete
    "zengge_helper.sock":                  ("zengge-lights",             "state",  "workspace/Skills/zengge-lights/state/zengge_helper.sock"),
    "zengge_fade_detached.log":            ("zengge-lights",             "state",  "workspace/Skills/zengge-lights/state/zengge_fade_detached.log"),
}

# Names whose file has been moved to Skills/<owner>/<kind>/<name>. Empty = nothing has moved.
MOVED: frozenset[str] = frozenset()


# The real deployment's skill folders, used to validate a skill NAME even when
# OPENCLAW_HOME points a test at a sandbox that has no folders of its own.
_REAL_SKILLS = Path("~/.openclaw") / "workspace" / "Skills"


def new_file(skill: str, kind: str, name: str) -> Path:
    """The place for a brand-new file: Skills/<skill>/<kind>/<name>."""
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}, not {kind!r}")
    if skill == "shared" or not ((SKILLS / skill).is_dir() or (_REAL_SKILLS / skill).is_dir()):
        raise ValueError(f"{skill!r} cannot own data: it must be a skill folder, and shared/ holds code only")
    target = SKILLS / skill / kind / name
    if SKILLS != _REAL_SKILLS:
        target.parent.mkdir(parents=True, exist_ok=True)   # a sandbox is created on demand
    return target


def path(name: str) -> Path:
    """Where a registered file lives right now."""
    try:
        owner, kind, today = REGISTRY[name]
    except KeyError:
        raise KeyError(f"{name!r} is not registered in data_paths.REGISTRY. A new file: use "
                       f"new_file(skill, kind, name) and register it.") from None
    return SKILLS / owner / kind / name if name in MOVED else OPENCLAW / today


def owner(name: str) -> str:
    return REGISTRY[name][0]

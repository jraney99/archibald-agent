#!/usr/bin/env python3
"""Calendar poller: decide deterministically, wake the stylist only to choose and caption.

Every 15 min (6am-9pm). outfit_brief.py does the deciding — which events deserve an
outfit (event_triage), the forecast for their place and hour, the ranked outfits.
This then:
  - sends the one-tap "who is this?" / "is this place dressy?" question when the
    triage cannot know (outfit_questions.py; once per person/venue per event), and
  - hands finished briefs to the model, whose only job is to pick one of three
    outfits and write a sentence (propose_from_brief.py — one command, no flags to guess).

Trigger, caption, questions and 🚫 button approved by the user 2026-09-20.
"""
import argparse, json, shutil, subprocess, sys
from datetime import datetime
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent; SCRIPTS=Path(__file__).resolve().parent
STATE=ROOT/'state/calendar_outfit_watch.json'; PAYLOAD=ROOT/'state/calendar_outfit_briefs.json'; PROPOSALS=ROOT/'profile/outfit_proposals.json'
CHAT='1000000001'; MAX_PER_RUN=4
# Evening-before delivery (The user 2026-09-28): a board for tomorrow waits until 7:30pm tonight and
# arrives under one "👔 Tomorrow:" line with the others; a board for today goes out at once.
EVENING_HOUR, EVENING_MINUTE = 19, 30
# The facts are decided by scripts; the one judgment call left (which of three outfits,
# and the sentence) goes to the stronger model. About one short call per board. The user 2026-09-20.
STYLIST_MODEL=None  # resolved from the live config by stylist_model(); see below
sys.path.insert(0,str(SCRIPTS))
import outfit_questions  # noqa: E402
from proposal_caption import event_name, when  # noqa: E402


def now_local():
    try:
        sys.path.insert(0,str(ROOT.parent/'shared')); from current_location import get_tz
        return datetime.now(get_tz())
    except Exception:  # noqa: BLE001
        return datetime.now().astimezone()


def due_now(brief, now):
    """Today's events: now. Tomorrow's: from 7:30pm tonight. Later: hold."""
    day=(brief.get('event') or {}).get('start_iso','')[:10]
    if not day: return True
    delta=(datetime.fromisoformat(day).date()-now.date()).days
    if delta<=0: return True
    if delta==1: return (now.hour,now.minute)>=(EVENING_HOUR,EVENING_MINUTE)
    return False


def short_name(brief):
    title=(brief.get('event') or {}).get('title','')
    if brief.get('kind')=='flight':
        import re
        m=re.search(r'(?:→|->)\s*([A-Z]{3})',title)
        return f"Flight to {m.group(1)}" if m else 'Flight'
    import re
    return re.split(r'\s+[-@]\s+',event_name(title),1)[0][:40]


def tomorrow_header(briefs):
    return '👔 Tomorrow: '+' · '.join(f"{short_name(b)} ({when((b.get('event') or {}).get('start_iso','')).rsplit(', ',1)[-1]})" for b in briefs)


def save_state(state, known, fresh_count=0, asked_count=0):
    """Persist immediately, never only at the end of a successful run.

    2026-09-29: the stylist call started failing (a model the config no longer
    allows) and raised before the single state write at the bottom of main(), so
    `evening_header_sent` — set the moment the "👔 Tomorrow:" line was actually
    DELIVERED — was discarded every time. The next run 15 minutes later had no
    memory of it and sent the header again. The user got five. A message that has
    already reached him is a fact, and the record of it must survive whatever
    fails next."""
    state.update({"dispatched": list(known)[-500:],
                  "last_scan_at": datetime.now().astimezone().isoformat(),
                  "last_count": fresh_count, "last_asked": asked_count})
    STATE.write_text(json.dumps(state, indent=2))


def stylist_model():
    """The strong model for the one judgment call, resolved against the live
    config rather than hardcoded.

    A pinned name is what broke this: the script asked for claude-sonnet-5 long
    after the allowlist moved to claude-sonnet-4-6, and every run died. Prefer the
    configured primary when it is not a Haiku (The user's 2026-09-20 choice was
    "stronger than the default", not one specific model), else the best allowed
    non-Haiku, else no override at all — a weaker stylist beats no outfit."""
    try:
        cfg = json.loads((ROOT.parent.parent.parent / 'openclaw.json').read_text())
        defaults = cfg['agents']['defaults']
        allowed = list(defaults.get('models') or {})
        primary = (defaults.get('model') or {}).get('primary')
        if primary and 'haiku' not in primary.lower() and (not allowed or primary in allowed):
            return primary
        for name in allowed:
            if 'haiku' not in name.lower():
                return name
    except Exception:  # noqa: BLE001
        pass
    return None


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--dry-run',action='store_true'); ap.add_argument('--events-json',help='use this calendar file instead of the live calendar'); args=ap.parse_args()
    global STYLIST_MODEL
    STYLIST_MODEL=stylist_model()
    STATE.parent.mkdir(parents=True,exist_ok=True); state=json.loads(STATE.read_text()) if STATE.exists() else {"dispatched":[]}
    cmd=[sys.executable,str(SCRIPTS/'outfit_brief.py')]+(['--events-json',args.events_json] if args.events_json else ['--days','2'])
    r=subprocess.run(cmd,capture_output=True,text=True,timeout=120)
    if r.returncode: raise SystemExit(r.stdout+r.stderr)
    found=json.loads(r.stdout)
    proposed={p.get('event_key') for p in (json.loads(PROPOSALS.read_text()) if PROPOSALS.exists() else []) if p.get('status') not in ('superseded','cancelled')}
    known=set(state.get('dispatched',[]))|proposed
    now=now_local(); today=now.date().isoformat()
    ready=[b for b in found['briefs'] if b['event_key'] not in known and b.get('top_outfits') and due_now(b,now)]
    fresh=ready[:MAX_PER_RUN]; held=[b['event']['title'] for b in found['briefs'] if b['event_key'] not in known and b.get('top_outfits') and not due_now(b,now)]
    no_outfit=[b['event']['title'] for b in found['briefs'] if b['event_key'] not in known and not b.get('top_outfits')]
    for_tomorrow=[b for b in fresh if (b.get('event') or {}).get('start_iso','')[:10]!=today]
    header=tomorrow_header(for_tomorrow) if for_tomorrow and state.get('evening_header_sent')!=today else None
    if args.dry_run:
        print(json.dumps({"would_send_boards_for":[b['event']['title'] for b in fresh],"held_until_evening":held,"header":header,
                          "would_ask":[outfit_questions.build(n['event'],n['verdict'])['text'] for n in found['needs_answer']],
                          "no_complete_outfit_for":no_outfit},indent=2,ensure_ascii=False)); return
    asked=[q['text'] for q in (outfit_questions.ask(n['event'],n['verdict']) for n in found['needs_answer']) if q]
    if header:
        from send_outfit_proposal import api
        if api('sendMessage',{"chat_id":CHAT,"text":header}).get('ok'):
            state['evening_header_sent']=today
            save_state(state,known)  # delivered — never send it twice, whatever fails below
    try:
      if fresh:
        PAYLOAD.write_text(json.dumps(fresh,indent=2,ensure_ascii=False))
        propose=SCRIPTS/'propose_from_brief.py'
        prompt=f'''Use the ai-wardrobe-steward skill. {len(fresh)} finished outfit brief(s) are in {PAYLOAD} (a JSON list). Everything is already decided: the event, the forecast, the ranked outfits. For EACH brief, by its list index: read it, choose ONE of its top_outfits (1, 2 or 3) using style judgment and its notes_for_the_stylist (his own guidance — follow it), and write one or two plain sentences on why, naming a layer or blazer if his guidance calls for one. Then run exactly this, once per brief: python3 {propose} --briefs {PAYLOAD} --index <index> --choice <1|2|3> --reason "<your sentences>". That single command creates the proposal, renders the board and sends it. Do not run any other wardrobe script, do not look up weather, do not change or add items, never contact anyone. If the command prints an error, report that text and stop. Output NO_REPLY when every brief is sent.'''
        oc=shutil.which('openclaw') or 'openclaw'
        run=subprocess.run([oc,'agent','--agent','main','--session-key','agent:main:wardrobe-calendar','--channel','telegram','--reply-channel','telegram','--reply-to',CHAT,*(['--model',STYLIST_MODEL] if STYLIST_MODEL else []),'--message',prompt,'--timeout','600','--json'],capture_output=True,text=True,timeout=660)
        if run.returncode: raise SystemExit(run.stderr or run.stdout)
        # Only a board that actually went out counts as dispatched; a failed run must
        # stay eligible for a retry, which is why this is here and not in `finally`.
        known.update(b['event_key'] for b in fresh)
    finally:
      save_state(state,known,len(fresh),len(asked))
    if fresh or asked or no_outfit: print(json.dumps({"dispatched":len(fresh),"asked":asked,"no_complete_outfit_for":no_outfit},ensure_ascii=False))


if __name__=='__main__': main()

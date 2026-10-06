"""Find habits in observed activity and turn them into suggested automations (routines)."""
import json

from collections import Counter, defaultdict
from datetime import datetime

from . import config

ROUTINES_PATH = config.DATA_DIR / "routines.json"
IGNORED_EXES = {"", "explorer.exe", "searchhost.exe", "shellexperiencehost.exe", "lockapp.exe",
                "python.exe", "pythonw.exe", "johnny.exe", "startmenuexperiencehost.exe",
                "applicationframehost.exe", "textinputhost.exe"}


def load_routines():
    try:
        return json.loads(ROUTINES_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"routines": [], "dismissed": []}


def save_routines(data):
    config.ensure_dirs()
    ROUTINES_PATH.write_text(json.dumps(data, indent=1), encoding="utf-8")


def find_habits(memory, days=14, min_days=3):
    """Return suggestions like {'id','kind','text','routine'}."""
    rows = memory.activity_since(days * 86400)
    if not rows:
        return []
    data = load_routines()
    dismissed = set(data.get("dismissed", []))
    existing = {r["id"] for r in data.get("routines", [])}

    # 1) First launch of an app each day at a consistent hour -> "open X at HH:MM"
    first_seen = defaultdict(dict)  # exe -> {date: minutes_since_midnight}
    for ts, exe, _title, _dur in rows:
        if exe.lower() in IGNORED_EXES:
            continue
        d = datetime.fromtimestamp(ts)
        day = d.date().isoformat()
        if day not in first_seen[exe]:
            first_seen[exe][day] = d.hour * 60 + d.minute
    out = []
    for exe, per_day in first_seen.items():
        if len(per_day) < min_days:
            continue
        mins = sorted(per_day.values())
        median = mins[len(mins) // 2]
        close = [m for m in mins if abs(m - median) <= 30]
        if len(close) >= min_days and len(close) / len(mins) >= 0.6:
            at = f"{median // 60:02d}:{median % 60:02d}"
            rid = f"open:{exe}:{at[:2]}"
            if rid in dismissed or rid in existing:
                continue
            out.append({
                "id": rid, "kind": "daily_open",
                "text": f"You usually open {exe.replace('.exe', '')} around {at} "
                        f"({len(close)} of the last {len(mins)} days). Want me to open it for you every day at {at}?",
                "routine": {"id": rid, "type": "open_app", "target": exe, "at": at, "days": "daily",
                            "enabled": True, "last_run": ""},
            })

    # 2) Apps that are always used together -> "launch group"
    seq = [exe for _, exe, _, _ in rows if exe.lower() not in IGNORED_EXES]
    pairs = Counter()
    for a, b in zip(seq, seq[1:]):
        if a != b:
            pairs[tuple(sorted((a, b)))] += 1
    for (a, b), n in pairs.most_common(3):
        if n < 12:
            break
        rid = f"group:{a}:{b}"
        if rid in dismissed or rid in existing:
            continue
        out.append({
            "id": rid, "kind": "group",
            "text": f"You switch between {a.replace('.exe', '')} and {b.replace('.exe', '')} a lot ({n} times). "
                    f"Want a voice command \"start my {a.replace('.exe', '')} workspace\" that opens both?",
            "routine": {"id": rid, "type": "open_group", "targets": [a, b], "at": "", "days": "on_command",
                        "phrase": f"start my {a.replace('.exe', '')} workspace", "enabled": True, "last_run": ""},
        })
    return out


def accept(suggestion):
    data = load_routines()
    data["routines"].append(suggestion["routine"])
    save_routines(data)


def dismiss(suggestion):
    data = load_routines()
    data.setdefault("dismissed", []).append(suggestion["id"])
    save_routines(data)


def due_routines(now=None):
    """Routines whose time has come today and that haven't run yet today."""
    now = now or datetime.now()
    data = load_routines()
    today = now.date().isoformat()
    due = []
    for r in data.get("routines", []):
        if not r.get("enabled") or not r.get("at") or r.get("last_run") == today:
            continue
        if r.get("days") == "weekdays" and now.weekday() >= 5:
            continue
        hh, mm = map(int, r["at"].split(":"))
        if (now.hour, now.minute) >= (hh, mm) and (now.hour * 60 + now.minute) - (hh * 60 + mm) < 60:
            due.append(r)
    return due


def mark_ran(rid):
    data = load_routines()
    for r in data.get("routines", []):
        if r["id"] == rid:
            r["last_run"] = datetime.now().date().isoformat()
    save_routines(data)


def usage_report(memory, seconds=86400):
    rows = memory.top_apps(seconds, 8)
    if not rows:
        return "I haven't observed any activity in that period yet."
    parts = [f"{exe.replace('.exe', '')}: {d / 3600:.1f}h" if d >= 3600 else f"{exe.replace('.exe', '')}: {d / 60:.0f}m"
             for exe, d, _ in rows if exe]
    return "Time by app — " + ", ".join(parts)



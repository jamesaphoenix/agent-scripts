"""Validate the finite job's registered owner, monitor and observation schedule."""
import re
import datetime
from pathlib import Path


def focused_roots(manifest):
    roots = list(dict.fromkeys(Path(p) for p in [*manifest.get('focusedRoots', []),
        manifest.get('focusedParent'), manifest.get('focusedContinuation')] if p))
    if manifest.get('focusedContinuation'):
        target = Path(manifest['focusedContinuation'])
        remainder = target.with_name(target.name + '-remainder')
        if (remainder / 'job.json').exists() and remainder not in roots:
            roots.append(remainder)
    return roots


def campaign_window(manifest):
    """New windows are request-anchored; legacy runs keep their six-hour contract."""
    hours = manifest.get('hours', 6)
    if type(hours) is not int or hours not in (6, 12):
        raise ValueError('Choose a six-hour or explicitly authorised twelve-hour window')
    supplied = manifest.get('authorizedStartedAt'), manifest.get('authorizedDeadline')
    if hours == 12 and not all(supplied):
        raise ValueError('Twelve-hour stages require the original request and deadline')
    if any(supplied) and not all(supplied):
        raise ValueError('Both original timestamps are required')
    start = datetime.datetime.fromisoformat(supplied[0]) if supplied[0] else datetime.datetime.now(datetime.timezone.utc)
    deadline = datetime.datetime.fromisoformat(supplied[1]) if supplied[1] else start + datetime.timedelta(hours=hours)
    if start.tzinfo is None or deadline.tzinfo is None or deadline - start != datetime.timedelta(hours=hours):
        raise ValueError('Original authorised duration changed')
    return start, deadline, deadline - datetime.timedelta(minutes=15)


def launchd_settings(manifest):
    labels = manifest.get("launchdLabels", {
        "run": "com.jud.tutor-lead-pathways-20261011",
        "watch": "com.jud.tutor-lead-pathways-watch-20261011",
    })
    interval = manifest.get("monitorIntervalSeconds", 1800)
    if type(interval) is not int or not 60 <= interval <= 3600:
        raise ValueError("Monitor interval must be an integer from 60 to 3600 seconds")
    if not isinstance(labels, dict) or set(labels) != {"run", "watch"}:
        raise ValueError("Both registered launchd labels are required")
    owner = labels["run"]
    if not isinstance(owner, str) or not re.fullmatch(r"com\.jud\.tutor-lead-pathways-[a-z0-9]+(?:-[a-z0-9]+)*", owner):
        raise ValueError("Invalid tutor discovery owner label")
    suffix = owner.removeprefix("com.jud.tutor-lead-pathways-")
    if labels["watch"] != "com.jud.tutor-lead-pathways-watch-" + suffix:
        raise ValueError("Monitor must address its own registered discovery run")
    return labels, interval

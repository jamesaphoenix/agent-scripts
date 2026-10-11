"""Validate the finite job's registered owner, monitor and observation schedule."""
import re


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

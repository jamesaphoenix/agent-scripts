#!/usr/bin/env python3
"""Query helper for config/docker-apps.json.

Bash loops call this with a subcommand and read tab-separated output, so the JSON
schema lives in exactly one place and adding an app never means editing a script.

Subcommands:
  validate                 exit 1 with a message on any schema problem
  defaults                 KEY<TAB>VALUE lines from the defaults block
  list                     ID<TAB>COMPOSE_PROJECT for every enabled app
  start-order ID           SERVICE<TAB>DELAY_SECONDS lines in bring-up order
  temporal ID              one line: ENABLED<TAB>CHECK_FROM<TAB>RESTART_CSV<TAB>KICKSTART_CSV
  temporal-apps            ID<TAB>COMPOSE_PROJECT for enabled apps with temporal.enabled
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config" / "docker-apps.json"


def load(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(f"docker-apps: config not found: {path}")
    except json.JSONDecodeError as exc:
        raise SystemExit(f"docker-apps: invalid JSON in {path}: {exc}")


def validate(data: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    apps = data.get("apps")
    if not isinstance(apps, list) or not apps:
        return ["apps must be a non-empty list"]
    seen_ids: set[str] = set()
    seen_projects: set[str] = set()
    for index, app in enumerate(apps):
        where = f"apps[{index}]"
        if not isinstance(app, dict):
            problems.append(f"{where}: must be an object")
            continue
        app_id = app.get("id")
        project = app.get("composeProject")
        if not isinstance(app_id, str) or not app_id:
            problems.append(f"{where}: id is required")
        elif app_id in seen_ids:
            problems.append(f"{where}: duplicate id {app_id!r}")
        else:
            seen_ids.add(app_id)
        if not isinstance(project, str) or not project:
            problems.append(f"{where}: composeProject is required")
        elif project in seen_projects:
            problems.append(f"{where}: duplicate composeProject {project!r}")
        else:
            seen_projects.add(project)
        order = app.get("startOrder")
        if not isinstance(order, list) or not order or not all(isinstance(s, str) and s for s in order):
            problems.append(f"{where}: startOrder must be a non-empty list of service names")
            order = []
        delays = app.get("startDelaySeconds") or {}
        if not isinstance(delays, dict):
            problems.append(f"{where}: startDelaySeconds must be an object")
        else:
            for service, delay in delays.items():
                if service not in order:
                    problems.append(f"{where}: startDelaySeconds names unknown service {service!r}")
                if not isinstance(delay, (int, float)) or delay < 0:
                    problems.append(f"{where}: startDelaySeconds[{service!r}] must be a non-negative number")
        temporal = app.get("temporal") or {}
        if not isinstance(temporal, dict):
            problems.append(f"{where}: temporal must be an object")
        elif temporal.get("enabled", False):
            check_from = temporal.get("checkFromService")
            if not isinstance(check_from, str) or not check_from:
                problems.append(f"{where}: temporal.checkFromService is required when temporal.enabled")
            elif check_from not in order:
                problems.append(f"{where}: temporal.checkFromService {check_from!r} is not in startOrder")
            for key in ("restartServicesAfterRepair", "kickstartLaunchdAfterRepair"):
                value = temporal.get(key, [])
                if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
                    problems.append(f"{where}: temporal.{key} must be a list of strings")
            for service in temporal.get("restartServicesAfterRepair", []) or []:
                if isinstance(service, str) and service not in order:
                    problems.append(f"{where}: temporal.restartServicesAfterRepair names unknown service {service!r}")
    return problems


def enabled_apps(data: dict[str, Any]) -> list[dict[str, Any]]:
    return [app for app in data.get("apps", []) if app.get("enabled", True)]


def find_app(data: dict[str, Any], app_id: str) -> dict[str, Any]:
    for app in data.get("apps", []):
        if app.get("id") == app_id:
            return app
    raise SystemExit(f"docker-apps: unknown app id {app_id!r}")


def main(argv: list[str]) -> int:
    config_path = Path(os.environ.get("DOCKER_APPS_CONFIG", str(DEFAULT_CONFIG)))
    if not argv:
        print((__doc__ or "").strip(), file=sys.stderr)
        return 2
    command, args = argv[0], argv[1:]
    data = load(config_path)
    problems = validate(data)
    if problems:
        for problem in problems:
            print(f"docker-apps: {config_path}: {problem}", file=sys.stderr)
        return 1

    if command == "validate":
        print(f"docker-apps: {config_path} ok ({len(enabled_apps(data))} enabled app(s))")
        return 0
    if command == "defaults":
        for key, value in (data.get("defaults") or {}).items():
            print(f"{key}\t{value}")
        return 0
    if command == "list":
        for app in enabled_apps(data):
            print(f"{app['id']}\t{app['composeProject']}")
        return 0
    if command == "temporal-apps":
        for app in enabled_apps(data):
            if (app.get("temporal") or {}).get("enabled", False):
                print(f"{app['id']}\t{app['composeProject']}")
        return 0
    if command == "start-order":
        if len(args) != 1:
            raise SystemExit("usage: docker-apps.py start-order ID")
        app = find_app(data, args[0])
        delays = app.get("startDelaySeconds") or {}
        for service in app["startOrder"]:
            print(f"{service}\t{delays.get(service, 0)}")
        return 0
    if command == "temporal":
        if len(args) != 1:
            raise SystemExit("usage: docker-apps.py temporal ID")
        app = find_app(data, args[0])
        temporal = app.get("temporal") or {}
        enabled = "1" if temporal.get("enabled", False) else "0"
        check_from = temporal.get("checkFromService", "") if enabled == "1" else ""
        restart = ",".join(temporal.get("restartServicesAfterRepair", []) or [])
        kickstart = ",".join(temporal.get("kickstartLaunchdAfterRepair", []) or [])
        print(f"{enabled}\t{check_from}\t{restart}\t{kickstart}")
        return 0
    print(f"docker-apps: unknown subcommand {command!r}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

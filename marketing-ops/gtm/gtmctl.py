#!/usr/bin/env python3
"""Unpublished-only Google Tag Manager workspace control plane."""

from __future__ import annotations

import argparse
import copy
import dataclasses
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence


DEFAULT_API_BASE_URL = "https://tagmanager.googleapis.com/tagmanager/v2"
DEFAULT_TOKEN_URL = "https://oauth2.googleapis.com/token"
DEFAULT_HTTP_MAX_ATTEMPTS = 6
DEFAULT_HTTP_INITIAL_BACKOFF_SECONDS = 1.0
DEFAULT_HTTP_MAX_BACKOFF_SECONDS = 30.0
RETRYABLE_HTTP_STATUSES = frozenset({429, 500, 502, 503, 504})
ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
SERVER_ONLY_FIELDS = {
    "accountId",
    "containerId",
    "fingerprint",
    "path",
    "tagManagerUrl",
    "variableId",
    "triggerId",
    "tagId",
    "workspaceId",
}
RESOURCE_KEYS = {
    "variables": "variable",
    "triggers": "trigger",
    "tags": "tag",
}


class GtmCtlError(RuntimeError):
    """Expected command failure with a user-facing message."""


@dataclasses.dataclass(frozen=True)
class ManifestBundle:
    raw: dict[str, Any]
    resolved: dict[str, Any]
    source: Path
    op_refs: tuple[str, ...]


@dataclasses.dataclass(frozen=True)
class Target:
    account: dict[str, Any]
    container: dict[str, Any]


@dataclasses.dataclass(frozen=True)
class DesiredResources:
    variables: tuple[dict[str, Any], ...]
    triggers: tuple[dict[str, Any], ...]
    boot_trigger_name: str
    event_trigger_name: str
    scoped_event_trigger_names: tuple[tuple[str, str], ...]
    exact_event_trigger_names: tuple[tuple[str, str], ...]


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise GtmCtlError(f"Manifest not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise GtmCtlError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise GtmCtlError("Manifest root must be a JSON object")
    return value


def _secure_oauth_sources(raw: Mapping[str, Any]) -> None:
    oauth = raw.get("oauth")
    if not isinstance(oauth, Mapping):
        raise GtmCtlError("Manifest oauth must be an object")
    for key in ("clientId", "clientSecret", "refreshToken"):
        value = oauth.get(key)
        if not isinstance(value, str) or not value:
            raise GtmCtlError(f"Manifest oauth.{key} is required")
        if not (value.startswith("op://") or ENV_PATTERN.search(value)):
            raise GtmCtlError(
                f"oauth.{key} must be an environment interpolation or an op:// reference"
            )


def _interpolate_string(value: str, environ: Mapping[str, str]) -> str:
    missing: list[str] = []

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in environ:
            missing.append(name)
            return match.group(0)
        return environ[name]

    result = ENV_PATTERN.sub(replace, value)
    if missing:
        names = ", ".join(sorted(set(missing)))
        raise GtmCtlError(f"Missing required environment variables: {names}")
    return result


def _walk_strings(value: Any, fn: Callable[[str], str]) -> Any:
    if isinstance(value, str):
        return fn(value)
    if isinstance(value, list):
        return [_walk_strings(item, fn) for item in value]
    if isinstance(value, dict):
        return {key: _walk_strings(item, fn) for key, item in value.items()}
    return value


def _collect_op_refs(value: Any) -> tuple[str, ...]:
    found: set[str] = set()

    def collect(item: str) -> str:
        if item.startswith("op://"):
            found.add(item)
        return item

    _walk_strings(value, collect)
    return tuple(sorted(found))


def _default_op_reader(reference: str) -> str:
    if shutil.which("op") is None:
        raise GtmCtlError("1Password CLI is required to resolve op:// references")
    try:
        completed = subprocess.run(
            ["op", "read", reference],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        raise GtmCtlError(f"Timed out resolving {reference} through 1Password CLI") from exc
    if completed.returncode != 0:
        message = completed.stderr.strip() or "op read failed"
        raise GtmCtlError(f"Could not resolve {reference}: {message}")
    return completed.stdout.rstrip("\n")


def load_manifest(
    path: str | Path,
    *,
    environ: Mapping[str, str] | None = None,
    op_reader: Callable[[str], str] = _default_op_reader,
) -> ManifestBundle:
    source = Path(path).expanduser().resolve()
    raw = _read_json(source)
    _secure_oauth_sources(raw)
    environment = environ if environ is not None else os.environ
    interpolated = _walk_strings(raw, lambda value: _interpolate_string(value, environment))
    op_refs = _collect_op_refs(interpolated)
    resolved = _walk_strings(
        interpolated,
        lambda value: op_reader(value) if value.startswith("op://") else value,
    )
    validate_manifest(resolved)
    return ManifestBundle(raw=raw, resolved=resolved, source=source, op_refs=op_refs)


def _require_string(parent: Mapping[str, Any], key: str, location: str) -> str:
    value = parent.get(key)
    if not isinstance(value, str) or not value.strip():
        raise GtmCtlError(f"{location}.{key} must be a non-empty string")
    return value


def _require_list(parent: Mapping[str, Any], key: str, location: str) -> list[Any]:
    value = parent.get(key)
    if not isinstance(value, list):
        raise GtmCtlError(f"{location}.{key} must be a list")
    return value


def _require_unique_strings(
    parent: Mapping[str, Any],
    key: str,
    location: str,
) -> list[str]:
    values = _require_list(parent, key, location)
    if not values or not all(
        isinstance(item, str) and item.strip() for item in values
    ):
        raise GtmCtlError(f"{location}.{key} must contain non-empty strings")
    if len(set(values)) != len(values):
        raise GtmCtlError(f"{location}.{key} must not contain duplicates")
    return values


def _reject_unknown_fields(
    value: Mapping[str, Any],
    allowed_fields: set[str],
    location: str,
) -> None:
    extra_fields = set(value) - allowed_fields
    if extra_fields:
        names = ", ".join(sorted(extra_fields))
        raise GtmCtlError(f"{location} contains unknown fields: {names}")


def _require_fields(
    value: Mapping[str, Any],
    required_fields: set[str],
    location: str,
) -> None:
    missing_fields = required_fields - set(value)
    if missing_fields:
        names = ", ".join(sorted(missing_fields))
        raise GtmCtlError(f"{location} is missing required fields: {names}")


def _validate_consent_types(parent: Mapping[str, Any], location: str) -> list[str]:
    return _require_unique_strings(parent, "consentTypes", location)


def _validate_data_layer_field(
    parent: Mapping[str, Any],
    key: str,
    location: str,
    data_layer_fields: Sequence[str],
) -> str:
    field = _require_string(parent, key, location)
    if field not in data_layer_fields:
        raise GtmCtlError(f"{location}.{key} must also be in managed.dataLayerFields")
    return field


def _has_unescaped_top_level_alternation(pattern: str) -> bool:
    depth = 0
    in_character_class = False
    escaped = False
    for character in pattern:
        if escaped:
            escaped = False
            continue
        if character == "\\":
            escaped = True
            continue
        if in_character_class:
            if character == "]":
                in_character_class = False
            continue
        if character == "[":
            in_character_class = True
        elif character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
        elif character == "|" and depth == 0:
            return True
    return False


def validate_manifest(manifest: Mapping[str, Any]) -> None:
    _reject_unknown_fields(
        manifest,
        {"$schema", "schemaVersion", "target", "oauth", "workspace", "managed", "superseded"},
        "manifest",
    )
    if "$schema" in manifest and not isinstance(manifest["$schema"], str):
        raise GtmCtlError("manifest.$schema must be a string")
    schema_version = manifest.get("schemaVersion")
    if type(schema_version) is not int or schema_version != 1:
        raise GtmCtlError("schemaVersion must be the integer 1")

    target = manifest.get("target")
    if not isinstance(target, Mapping):
        raise GtmCtlError("target must be an object")
    _require_fields(target, {"accountName", "containerName"}, "target")
    _reject_unknown_fields(target, {"accountName", "containerName"}, "target")
    _require_string(target, "accountName", "target")
    _require_string(target, "containerName", "target")

    oauth = manifest.get("oauth")
    if not isinstance(oauth, Mapping):
        raise GtmCtlError("oauth must be an object")
    oauth_fields = {"clientId", "clientSecret", "refreshToken"}
    _require_fields(oauth, oauth_fields, "oauth")
    _reject_unknown_fields(oauth, oauth_fields, "oauth")
    for key in ("clientId", "clientSecret", "refreshToken"):
        _require_string(oauth, key, "oauth")

    workspace = manifest.get("workspace")
    if not isinstance(workspace, Mapping):
        raise GtmCtlError("workspace must be an object")
    workspace_fields = {"compareName", "description"}
    _require_fields(workspace, workspace_fields, "workspace")
    _reject_unknown_fields(workspace, workspace_fields, "workspace")
    _require_string(workspace, "compareName", "workspace")
    _require_string(workspace, "description", "workspace")

    managed = manifest.get("managed")
    if not isinstance(managed, Mapping):
        raise GtmCtlError("managed must be an object")
    managed_required_fields = {
        "resourcePrefix",
        "managedNote",
        "deploymentEnvironmentField",
        "pageLocationField",
        "surfaceField",
        "surfaceValues",
        "bootEvent",
        "allowedEventPattern",
        "dataLayerFields",
        "measurementIdMappings",
        "eventParameters",
        "consentTypes",
        "sendPageView",
    }
    managed_allowed_fields = managed_required_fields | {
        "eventParameterAllowlists",
        "exactEventTriggers",
        "googleAds",
        "microsoftAds",
        "ensureBuiltInVariables",
    }
    _require_fields(managed, managed_required_fields, "managed")
    _reject_unknown_fields(managed, managed_allowed_fields, "managed")
    for key in (
        "resourcePrefix",
        "managedNote",
        "deploymentEnvironmentField",
        "pageLocationField",
        "surfaceField",
        "bootEvent",
        "allowedEventPattern",
    ):
        _require_string(managed, key, "managed")

    surface_values = _require_list(managed, "surfaceValues", "managed")
    if not surface_values or not all(
        isinstance(item, str) and item.strip() for item in surface_values
    ):
        raise GtmCtlError("managed.surfaceValues must contain non-empty strings")
    if len(set(surface_values)) != len(surface_values):
        raise GtmCtlError("managed.surfaceValues must not contain duplicates")

    allowed_event_pattern = str(managed["allowedEventPattern"])
    deferred_event_pattern = re.fullmatch(
        r"\$\{[A-Za-z_][A-Za-z0-9_]*\}",
        allowed_event_pattern,
    )
    if deferred_event_pattern is None and (
        not allowed_event_pattern.startswith("^")
        or not allowed_event_pattern.endswith("$")
    ):
        raise GtmCtlError(
            "managed.allowedEventPattern must be anchored with ^ and $"
        )
    try:
        re.compile(allowed_event_pattern)
    except re.error as exc:
        raise GtmCtlError(f"managed.allowedEventPattern is invalid: {exc}") from exc
    if _has_unescaped_top_level_alternation(allowed_event_pattern):
        raise GtmCtlError(
            "managed.allowedEventPattern must group alternations so ^ and $ apply "
            "to every branch"
        )

    data_layer_fields = _require_list(managed, "dataLayerFields", "managed")
    if not data_layer_fields or not all(isinstance(item, str) and item for item in data_layer_fields):
        raise GtmCtlError("managed.dataLayerFields must contain non-empty strings")
    if len(set(data_layer_fields)) != len(data_layer_fields):
        raise GtmCtlError("managed.dataLayerFields must not contain duplicates")
    for required_field in (
        managed["deploymentEnvironmentField"],
        managed["pageLocationField"],
        managed["surfaceField"],
    ):
        if required_field not in data_layer_fields:
            raise GtmCtlError(
                f"managed.dataLayerFields must include {required_field!r}"
            )

    mappings = _require_list(managed, "measurementIdMappings", "managed")
    if not mappings:
        raise GtmCtlError("managed.measurementIdMappings must not be empty")
    environments: set[str] = set()
    for index, mapping in enumerate(mappings):
        location = f"managed.measurementIdMappings[{index}]"
        if not isinstance(mapping, Mapping):
            raise GtmCtlError(f"{location} must be an object")
        mapping_fields = {"environment", "measurementId"}
        _require_fields(mapping, mapping_fields, location)
        _reject_unknown_fields(mapping, mapping_fields, location)
        environment = _require_string(mapping, "environment", location)
        _require_string(mapping, "measurementId", location)
        if environment in environments:
            raise GtmCtlError(f"Duplicate measurement ID environment: {environment}")
        environments.add(environment)

    parameters = _require_list(managed, "eventParameters", "managed")
    parameter_names: set[str] = set()
    for index, parameter in enumerate(parameters):
        location = f"managed.eventParameters[{index}]"
        if not isinstance(parameter, Mapping):
            raise GtmCtlError(f"{location} must be an object")
        parameter_fields = {"name", "field"}
        _require_fields(parameter, parameter_fields, location)
        _reject_unknown_fields(parameter, parameter_fields, location)
        name = _require_string(parameter, "name", location)
        field = _require_string(parameter, "field", location)
        if name in parameter_names:
            raise GtmCtlError(f"Duplicate event parameter name: {name}")
        if field not in data_layer_fields:
            raise GtmCtlError(f"{location}.field must also be in managed.dataLayerFields")
        parameter_names.add(name)

    event_parameter_allowlists = managed.get("eventParameterAllowlists", [])
    if not isinstance(event_parameter_allowlists, list):
        raise GtmCtlError("managed.eventParameterAllowlists must be a list")
    if "eventParameterAllowlists" in managed and not event_parameter_allowlists:
        raise GtmCtlError("managed.eventParameterAllowlists must not be empty")
    allowlist_names: set[str] = set()
    allowlisted_events: set[str] = set()
    for index, allowlist in enumerate(event_parameter_allowlists):
        location = f"managed.eventParameterAllowlists[{index}]"
        if not isinstance(allowlist, Mapping):
            raise GtmCtlError(f"{location} must be an object")
        allowlist_fields = {"name", "events", "parameters"}
        _require_fields(allowlist, allowlist_fields, location)
        _reject_unknown_fields(allowlist, allowlist_fields, location)
        name = _require_string(allowlist, "name", location)
        if name in allowlist_names:
            raise GtmCtlError(f"Duplicate event parameter allowlist name: {name}")
        allowlist_names.add(name)
        events = _require_unique_strings(allowlist, "events", location)
        for event in events:
            if event in allowlisted_events:
                raise GtmCtlError(
                    f"Event {event!r} appears in more than one event parameter allowlist"
                )
            if deferred_event_pattern is None and re.fullmatch(
                allowed_event_pattern,
                event,
            ) is None:
                raise GtmCtlError(
                    f"{location}.events contains {event!r}, which is not allowed by "
                    "managed.allowedEventPattern"
                )
            allowlisted_events.add(event)
        allowed_parameters = _require_unique_strings(
            allowlist,
            "parameters",
            location,
        )
        for parameter_name in allowed_parameters:
            if parameter_name not in parameter_names:
                raise GtmCtlError(
                    f"{location}.parameters contains unknown event parameter "
                    f"{parameter_name!r}"
                )

    _validate_consent_types(managed, "managed")
    if not isinstance(managed.get("sendPageView"), bool):
        raise GtmCtlError("managed.sendPageView must be a boolean")

    exact_event_triggers = managed.get("exactEventTriggers", [])
    if not isinstance(exact_event_triggers, list):
        raise GtmCtlError("managed.exactEventTriggers must be a list")
    if "exactEventTriggers" in managed and not exact_event_triggers:
        raise GtmCtlError("managed.exactEventTriggers must not be empty")
    exact_event_trigger_names: set[str] = set()
    condition_operators = {
        "contains",
        "endsWith",
        "equals",
        "matchRegex",
        "startsWith",
    }
    for index, trigger in enumerate(exact_event_triggers):
        location = f"managed.exactEventTriggers[{index}]"
        if not isinstance(trigger, Mapping):
            raise GtmCtlError(f"{location} must be an object")
        required_fields = {"name", "event", "conditions"}
        _require_fields(trigger, required_fields, location)
        _reject_unknown_fields(trigger, required_fields, location)
        name = _require_string(trigger, "name", location)
        _require_string(trigger, "event", location)
        if name in exact_event_trigger_names:
            raise GtmCtlError(f"Duplicate exact event trigger name: {name}")
        exact_event_trigger_names.add(name)
        conditions = _require_list(trigger, "conditions", location)
        for condition_index, condition in enumerate(conditions):
            condition_location = f"{location}.conditions[{condition_index}]"
            if not isinstance(condition, Mapping):
                raise GtmCtlError(f"{condition_location} must be an object")
            condition_fields = {"field", "operator", "value"}
            _require_fields(condition, condition_fields, condition_location)
            _reject_unknown_fields(condition, condition_fields, condition_location)
            _validate_data_layer_field(
                condition,
                "field",
                condition_location,
                data_layer_fields,
            )
            operator = _require_string(condition, "operator", condition_location)
            if operator not in condition_operators:
                names = ", ".join(sorted(condition_operators))
                raise GtmCtlError(
                    f"{condition_location}.operator must be one of: {names}"
                )
            condition_value = _require_string(condition, "value", condition_location)
            if operator == "matchRegex":
                try:
                    re.compile(condition_value)
                except re.error as exc:
                    raise GtmCtlError(
                        f"{condition_location}.value is an invalid regular expression: {exc}"
                    ) from exc

    google_ads: Mapping[str, Any] | None = None
    if "googleAds" in managed:
        candidate = managed["googleAds"]
        if not isinstance(candidate, Mapping):
            raise GtmCtlError("managed.googleAds must be an object")
        required_fields = {"enableConversionLinker", "consentTypes"}
        allowed_fields = required_fields | {
            "conversionActions",
            "conversionLinkerTriggerName",
        }
        _require_fields(candidate, required_fields, "managed.googleAds")
        _reject_unknown_fields(candidate, allowed_fields, "managed.googleAds")
        if not isinstance(candidate["enableConversionLinker"], bool):
            raise GtmCtlError(
                "managed.googleAds.enableConversionLinker must be a boolean"
            )
        _validate_consent_types(candidate, "managed.googleAds")
        if "conversionLinkerTriggerName" in candidate:
            conversion_linker_trigger_name = _require_string(
                candidate,
                "conversionLinkerTriggerName",
                "managed.googleAds",
            )
            if conversion_linker_trigger_name not in exact_event_trigger_names:
                raise GtmCtlError(
                    "managed.googleAds.conversionLinkerTriggerName must reference "
                    "managed.exactEventTriggers"
                )

        conversion_actions = candidate.get("conversionActions", [])
        if not isinstance(conversion_actions, list):
            raise GtmCtlError("managed.googleAds.conversionActions must be a list")
        if "conversionActions" in candidate and not conversion_actions:
            raise GtmCtlError("managed.googleAds.conversionActions must not be empty")
        conversion_action_names: set[str] = set()
        conversion_action_fields = {
            "name",
            "conversionId",
            "conversionLabel",
            "triggerName",
            "consentTypes",
            "orderIdField",
            "conversionValueField",
            "currencyCodeField",
        }
        for index, action in enumerate(conversion_actions):
            location = f"managed.googleAds.conversionActions[{index}]"
            if not isinstance(action, Mapping):
                raise GtmCtlError(f"{location} must be an object")
            _require_fields(action, conversion_action_fields, location)
            _reject_unknown_fields(action, conversion_action_fields, location)
            name = _require_string(action, "name", location)
            if name in conversion_action_names:
                raise GtmCtlError(f"Duplicate Google Ads conversion action name: {name}")
            conversion_action_names.add(name)
            _require_string(action, "conversionId", location)
            _require_string(action, "conversionLabel", location)
            trigger_name = _require_string(action, "triggerName", location)
            if trigger_name not in exact_event_trigger_names:
                raise GtmCtlError(
                    f"{location}.triggerName must reference managed.exactEventTriggers"
                )
            _validate_consent_types(action, location)
            for field_key in (
                "orderIdField",
                "conversionValueField",
                "currencyCodeField",
            ):
                _validate_data_layer_field(
                    action,
                    field_key,
                    location,
                    data_layer_fields,
                )
        google_ads = candidate

    microsoft_ads: Mapping[str, Any] | None = None
    if "microsoftAds" in managed:
        candidate = managed["microsoftAds"]
        if not isinstance(candidate, Mapping):
            raise GtmCtlError("managed.microsoftAds must be an object")
        allowed_fields = {"baseTags", "conversionActions"}
        _reject_unknown_fields(candidate, allowed_fields, "managed.microsoftAds")
        if not any(key in candidate for key in allowed_fields):
            raise GtmCtlError(
                "managed.microsoftAds must include baseTags or conversionActions"
            )

        base_tags = candidate.get("baseTags", [])
        if not isinstance(base_tags, list):
            raise GtmCtlError("managed.microsoftAds.baseTags must be a list")
        if "baseTags" in candidate and not base_tags:
            raise GtmCtlError("managed.microsoftAds.baseTags must not be empty")
        base_tag_names: set[str] = set()
        base_queue_names: set[str] = set()
        base_tag_required_fields = {
            "name",
            "tagId",
            "triggerName",
            "consentTypes",
        }
        base_tag_allowed_fields = base_tag_required_fields | {
            "uetqName",
            "enableAutoSpaTracking",
        }
        for index, base_tag in enumerate(base_tags):
            location = f"managed.microsoftAds.baseTags[{index}]"
            if not isinstance(base_tag, Mapping):
                raise GtmCtlError(f"{location} must be an object")
            _require_fields(base_tag, base_tag_required_fields, location)
            _reject_unknown_fields(base_tag, base_tag_allowed_fields, location)
            name = _require_string(base_tag, "name", location)
            if name in base_tag_names:
                raise GtmCtlError(f"Duplicate Microsoft Ads base tag name: {name}")
            base_tag_names.add(name)
            _require_string(base_tag, "tagId", location)
            trigger_name = _require_string(base_tag, "triggerName", location)
            if trigger_name not in exact_event_trigger_names:
                raise GtmCtlError(
                    f"{location}.triggerName must reference managed.exactEventTriggers"
                )
            _validate_consent_types(base_tag, location)
            if "enableAutoSpaTracking" in base_tag and not isinstance(
                base_tag["enableAutoSpaTracking"], bool
            ):
                raise GtmCtlError(
                    f"{location}.enableAutoSpaTracking must be a boolean"
                )
            queue_name = (
                _require_string(base_tag, "uetqName", location)
                if "uetqName" in base_tag
                else "uetq"
            )
            base_queue_names.add(queue_name)

        conversion_actions = candidate.get("conversionActions", [])
        if not isinstance(conversion_actions, list):
            raise GtmCtlError("managed.microsoftAds.conversionActions must be a list")
        if "conversionActions" in candidate and not conversion_actions:
            raise GtmCtlError("managed.microsoftAds.conversionActions must not be empty")
        conversion_action_names: set[str] = set()
        conversion_action_required_fields = {
            "name",
            "triggerName",
            "consentTypes",
            "goalValueField",
            "currencyField",
            "eventCategoryField",
            "transactionIdField",
            "eventLabelField",
        }
        conversion_action_allowed_fields = conversion_action_required_fields | {
            "uetqName"
        }
        for index, action in enumerate(conversion_actions):
            location = f"managed.microsoftAds.conversionActions[{index}]"
            if not isinstance(action, Mapping):
                raise GtmCtlError(f"{location} must be an object")
            _require_fields(action, conversion_action_required_fields, location)
            _reject_unknown_fields(action, conversion_action_allowed_fields, location)
            name = _require_string(action, "name", location)
            if name in conversion_action_names:
                raise GtmCtlError(f"Duplicate Microsoft Ads conversion action name: {name}")
            conversion_action_names.add(name)
            trigger_name = _require_string(action, "triggerName", location)
            if trigger_name not in exact_event_trigger_names:
                raise GtmCtlError(
                    f"{location}.triggerName must reference managed.exactEventTriggers"
                )
            _validate_consent_types(action, location)
            queue_name = (
                _require_string(action, "uetqName", location)
                if "uetqName" in action
                else "uetq"
            )
            if queue_name not in base_queue_names:
                raise GtmCtlError(
                    f"{location}.uetqName must match a managed Microsoft Ads base tag"
                )
            for field_key in (
                "goalValueField",
                "currencyField",
                "eventCategoryField",
                "transactionIdField",
                "eventLabelField",
            ):
                _validate_data_layer_field(
                    action,
                    field_key,
                    location,
                    data_layer_fields,
                )
        microsoft_ads = candidate

    built_ins = managed.get("ensureBuiltInVariables", [])
    if not isinstance(built_ins, list) or not all(isinstance(item, str) and item for item in built_ins):
        raise GtmCtlError("managed.ensureBuiltInVariables must be a list of strings")
    if len(set(built_ins)) != len(built_ins):
        raise GtmCtlError("managed.ensureBuiltInVariables must not contain duplicates")

    superseded = manifest.get("superseded", {})
    if not isinstance(superseded, Mapping):
        raise GtmCtlError("superseded must be an object")
    superseded_fields = {"pauseTags", "deleteTags", "deleteTriggers", "deleteVariables"}
    _reject_unknown_fields(superseded, superseded_fields, "superseded")
    for key in superseded_fields:
        values = superseded.get(key, [])
        if not isinstance(values, list) or not all(isinstance(item, str) and item for item in values):
            raise GtmCtlError(f"superseded.{key} must be a list of strings")
        if len(set(values)) != len(values):
            raise GtmCtlError(f"superseded.{key} must not contain duplicates")
    pause_delete_overlap = set(superseded.get("pauseTags", [])).intersection(
        superseded.get("deleteTags", [])
    )
    if pause_delete_overlap:
        names = ", ".join(sorted(pause_delete_overlap))
        raise GtmCtlError(
            "superseded.pauseTags and superseded.deleteTags overlap: " + names
        )

    prefix = managed["resourcePrefix"]
    event_parameter_allowlists = managed.get("eventParameterAllowlists", [])
    managed_event_tags = (
        {
            _scoped_event_tag_name(prefix, allowlist["name"])
            for allowlist in event_parameter_allowlists
        }
        if event_parameter_allowlists
        else {f"{prefix} - GA4 event - Allowed marketing event"}
    )
    managed_event_triggers = (
        {
            _scoped_event_trigger_name(prefix, allowlist["name"])
            for allowlist in event_parameter_allowlists
        }
        if event_parameter_allowlists
        else {f"{prefix} - Event - Allowed marketing events"}
    )
    managed_names = {
        "pauseTags": {
            f"{prefix} - Google tag - Public marketing",
            *managed_event_tags,
        },
        "deleteTags": {
            f"{prefix} - Google tag - Public marketing",
            *managed_event_tags,
        },
        "deleteTriggers": {
            f"{prefix} - Event - Marketing boot",
            *managed_event_triggers,
            *(
                _exact_event_trigger_name(prefix, trigger["name"])
                for trigger in exact_event_triggers
            ),
        },
        "deleteVariables": {
            *(_dlv_name(prefix, field) for field in data_layer_fields),
            _lookup_name(prefix),
        },
    }
    if google_ads and google_ads["enableConversionLinker"]:
        conversion_linker_name = _conversion_linker_name(prefix)
        managed_names["pauseTags"].add(conversion_linker_name)
        managed_names["deleteTags"].add(conversion_linker_name)
    if google_ads:
        for action in google_ads.get("conversionActions", []):
            conversion_name = _google_ads_conversion_name(prefix, action["name"])
            managed_names["pauseTags"].add(conversion_name)
            managed_names["deleteTags"].add(conversion_name)
    if microsoft_ads:
        for base_tag in microsoft_ads.get("baseTags", []):
            base_name = _microsoft_uet_base_name(prefix, base_tag["name"])
            managed_names["pauseTags"].add(base_name)
            managed_names["deleteTags"].add(base_name)
        for action in microsoft_ads.get("conversionActions", []):
            conversion_name = _microsoft_uet_conversion_name(prefix, action["name"])
            managed_names["pauseTags"].add(conversion_name)
            managed_names["deleteTags"].add(conversion_name)
    for key, protected_names in managed_names.items():
        overlap = protected_names.intersection(superseded.get(key, []))
        if overlap:
            names = ", ".join(sorted(overlap))
            raise GtmCtlError(f"superseded.{key} overlaps managed resources: {names}")


class JsonHttp:
    """Small JSON transport that can be pointed at a mock HTTP server in tests."""

    def __init__(
        self,
        *,
        max_attempts: int = DEFAULT_HTTP_MAX_ATTEMPTS,
        initial_backoff_seconds: float = DEFAULT_HTTP_INITIAL_BACKOFF_SECONDS,
        max_backoff_seconds: float = DEFAULT_HTTP_MAX_BACKOFF_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if initial_backoff_seconds < 0 or max_backoff_seconds < 0:
            raise ValueError("backoff values must not be negative")
        self.max_attempts = max_attempts
        self.initial_backoff_seconds = initial_backoff_seconds
        self.max_backoff_seconds = max_backoff_seconds
        self.sleep = sleep

    def _backoff_seconds(self, attempt: int, retry_after: str | None) -> float:
        if retry_after:
            try:
                parsed = max(0.0, float(retry_after))
            except ValueError:
                parsed = -1.0
            if parsed >= 0:
                return min(parsed, self.max_backoff_seconds)
        exponential = self.initial_backoff_seconds * (2 ** (attempt - 1))
        return min(exponential, self.max_backoff_seconds)

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        json_body: Any | None = None,
        form_body: Mapping[str, str] | None = None,
        allow_retries: bool | None = None,
    ) -> Any:
        normalized_method = method.upper()
        retries_allowed = (
            normalized_method in {"GET", "HEAD"}
            if allow_retries is None
            else allow_retries
        )
        request_headers = dict(headers or {})
        body: bytes | None = None
        if json_body is not None:
            body = json.dumps(json_body).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        elif form_body is not None:
            body = urllib.parse.urlencode(form_body).encode("utf-8")
            request_headers["Content-Type"] = "application/x-www-form-urlencoded"
        payload: bytes = b""
        for attempt in range(1, self.max_attempts + 1):
            request = urllib.request.Request(
                url,
                data=body,
                headers=request_headers,
                method=method,
            )
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    payload = response.read()
                break
            except urllib.error.HTTPError as exc:
                error_payload = exc.read().decode("utf-8", errors="replace")
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                exc.close()
                retryable = retries_allowed and exc.code in RETRYABLE_HTTP_STATUSES
                if retryable and attempt < self.max_attempts:
                    self.sleep(
                        self._backoff_seconds(
                            attempt,
                            retry_after,
                        )
                    )
                    continue
                try:
                    detail = json.loads(error_payload)
                    error = detail.get("error") if isinstance(detail, Mapping) else None
                    if isinstance(error, Mapping):
                        message = error.get("message") or error_payload
                    elif isinstance(error, str):
                        description = (
                            detail.get("error_description")
                            if isinstance(detail, Mapping)
                            else None
                        )
                        message = description or error
                    else:
                        message = error_payload
                except json.JSONDecodeError:
                    message = error_payload
                attempts = (
                    f" after {attempt} attempts" if retryable and attempt > 1 else ""
                )
                raise GtmCtlError(
                    f"HTTP {exc.code}{attempts} for {method} {url}: {message}"
                ) from exc
            except urllib.error.URLError as exc:
                raise GtmCtlError(f"Request failed for {method} {url}: {exc.reason}") from exc
        if not payload:
            return {}
        try:
            return json.loads(payload)
        except json.JSONDecodeError as exc:
            raise GtmCtlError(f"Non-JSON response for {method} {url}") from exc


def _validate_container_version(
    value: Any,
    *,
    location: str,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise GtmCtlError(f"Unexpected {location} container version response")
    version_id = value.get("containerVersionId")
    if not isinstance(version_id, str) or not version_id:
        raise GtmCtlError(
            f"{location.capitalize()} container version response did not contain "
            "containerVersionId"
        )
    for response_key in RESOURCE_KEYS.values():
        resources = value.get(response_key, [])
        if not isinstance(resources, list):
            raise GtmCtlError(
                f"{location.capitalize()} container version {response_key} must be a list"
            )
    return dict(value)


class GtmClient:
    def __init__(
        self,
        oauth: Mapping[str, str],
        *,
        http: JsonHttp | None = None,
        api_base_url: str = DEFAULT_API_BASE_URL,
        token_url: str = DEFAULT_TOKEN_URL,
    ) -> None:
        self.oauth = dict(oauth)
        self.http = http or JsonHttp()
        self.api_base_url = api_base_url.rstrip("/")
        self.token_url = token_url
        self._access_token: str | None = None

    def access_token(self) -> str:
        if self._access_token:
            return self._access_token
        response = self.http.request(
            "POST",
            self.token_url,
            form_body={
                "client_id": self.oauth["clientId"],
                "client_secret": self.oauth["clientSecret"],
                "refresh_token": self.oauth["refreshToken"],
                "grant_type": "refresh_token",
            },
            allow_retries=True,
        )
        token = response.get("access_token") if isinstance(response, Mapping) else None
        if not isinstance(token, str) or not token:
            raise GtmCtlError("OAuth token response did not contain access_token")
        self._access_token = token
        return token

    def request(
        self,
        method: str,
        path: str,
        *,
        body: Any | None = None,
        query: Mapping[str, Any] | None = None,
    ) -> Any:
        url = f"{self.api_base_url}/{path.lstrip('/')}"
        if query:
            clean_query: list[tuple[str, str]] = []
            for key, value in query.items():
                if isinstance(value, (list, tuple)):
                    clean_query.extend((key, str(item)) for item in value)
                elif value is not None:
                    clean_query.append((key, str(value)))
            url = f"{url}?{urllib.parse.urlencode(clean_query)}"
        return self.http.request(
            method,
            url,
            headers={"Authorization": f"Bearer {self.access_token()}"},
            json_body=body,
        )

    def list_all(self, path: str, response_key: str) -> list[dict[str, Any]]:
        values: list[dict[str, Any]] = []
        page_token: str | None = None
        while True:
            response = self.request("GET", path, query={"pageToken": page_token})
            page = response.get(response_key, []) if isinstance(response, Mapping) else []
            if not isinstance(page, list):
                raise GtmCtlError(f"Unexpected list response for {path}")
            values.extend(item for item in page if isinstance(item, dict))
            next_token = response.get("nextPageToken") if isinstance(response, Mapping) else None
            if not next_token:
                return values
            page_token = str(next_token)

    def resolve_target(self, account_name: str, container_name: str) -> Target:
        accounts = self.list_all("accounts", "account")
        account = _unique_by_name(accounts, account_name, "account")
        account_path = _resource_path(account, "accounts", "accountId")
        containers = self.list_all(f"{account_path}/containers", "container")
        container = _unique_by_name(containers, container_name, "container")
        return Target(account=account, container=container)

    def list_workspaces(self, target: Target) -> list[dict[str, Any]]:
        return self.list_all(f"{_container_path(target)}/workspaces", "workspace")

    def live_version(self, target: Target) -> dict[str, Any] | None:
        try:
            response = self.request("GET", f"{_container_path(target)}/versions:live")
        except GtmCtlError as exc:
            if str(exc).startswith("HTTP 404 "):
                return None
            raise
        return _validate_container_version(response, location="live")

    def resolve_workspace(self, target: Target, name: str) -> dict[str, Any]:
        return _unique_by_name(self.list_workspaces(target), name, "workspace")

    def create_workspace(
        self,
        target: Target,
        *,
        name: str,
        description: str,
    ) -> dict[str, Any]:
        return self.request(
            "POST",
            f"{_container_path(target)}/workspaces",
            body={"name": name, "description": description},
        )

    def list_resources(self, workspace_path: str, kind: str) -> list[dict[str, Any]]:
        return self.list_all(f"{workspace_path}/{kind}", RESOURCE_KEYS[kind])

    def create_resource(
        self,
        workspace_path: str,
        kind: str,
        body: Mapping[str, Any],
    ) -> dict[str, Any]:
        return self.request("POST", f"{workspace_path}/{kind}", body=dict(body))

    def update_resource(
        self,
        existing: Mapping[str, Any],
        body: Mapping[str, Any],
    ) -> dict[str, Any]:
        path = _require_resource_path(existing)
        return self.request(
            "PUT",
            path,
            body=dict(body),
            query={"fingerprint": existing.get("fingerprint")},
        )

    def delete_resource(self, existing: Mapping[str, Any]) -> None:
        self.request(
            "DELETE",
            _require_resource_path(existing),
            query={"fingerprint": existing.get("fingerprint")},
        )

    def ensure_built_in_variables(self, workspace_path: str, types: Sequence[str]) -> list[str]:
        existing = self.list_all(f"{workspace_path}/built_in_variables", "builtInVariable")
        existing_types = {str(item.get("type")) for item in existing}
        missing = [item for item in types if item not in existing_types]
        if missing:
            self.request(
                "POST",
                f"{workspace_path}/built_in_variables",
                query={"type": missing},
            )
        return missing

    def workspace_status(self, workspace_path: str) -> dict[str, Any]:
        response = self.request("GET", f"{workspace_path}/status")
        return dict(response) if isinstance(response, Mapping) else {}

    def quick_preview(self, workspace_path: str) -> dict[str, Any]:
        response = self.request("POST", f"{workspace_path}:quick_preview", body={})
        if not isinstance(response, Mapping):
            raise GtmCtlError("Unexpected GTM quick preview response")
        compiler_error = response.get("compilerError", False)
        if not isinstance(compiler_error, bool):
            raise GtmCtlError(
                "GTM quick preview compilerError must be a boolean when present"
            )
        result = dict(response)
        result["compilerError"] = compiler_error
        result["containerVersion"] = _validate_container_version(
            response.get("containerVersion"),
            location="quick preview",
        )
        return result


def _resource_path(resource: Mapping[str, Any], prefix: str, id_key: str) -> str:
    path = resource.get("path")
    if isinstance(path, str) and path:
        return path
    resource_id = resource.get(id_key)
    if not resource_id:
        raise GtmCtlError(f"API resource did not include path or {id_key}")
    return f"{prefix}/{resource_id}"


def _require_resource_path(resource: Mapping[str, Any]) -> str:
    path = resource.get("path")
    if not isinstance(path, str) or not path:
        raise GtmCtlError("GTM resource did not include a path")
    return path


def _container_path(target: Target) -> str:
    path = target.container.get("path")
    if isinstance(path, str) and path:
        return path
    account_path = _resource_path(target.account, "accounts", "accountId")
    container_id = target.container.get("containerId")
    if not container_id:
        raise GtmCtlError("Container did not include path or containerId")
    return f"{account_path}/containers/{container_id}"


def _unique_by_name(
    resources: Iterable[Mapping[str, Any]],
    name: str,
    resource_type: str,
) -> dict[str, Any]:
    matches = [dict(item) for item in resources if item.get("name") == name]
    if not matches:
        raise GtmCtlError(f"No {resource_type} named {name!r} was found")
    if len(matches) > 1:
        raise GtmCtlError(f"More than one {resource_type} named {name!r} was found")
    return matches[0]


def _template_parameter(key: str, value: str) -> dict[str, str]:
    return {"type": "template", "key": key, "value": value}


def _boolean_parameter(key: str, value: bool) -> dict[str, str]:
    return {"type": "boolean", "key": key, "value": "true" if value else "false"}


def _condition(kind: str, left: str, right: str) -> dict[str, Any]:
    return {
        "type": kind,
        "parameter": [
            _template_parameter("arg0", left),
            _template_parameter("arg1", right),
        ],
    }


def _dlv_name(prefix: str, field: str) -> str:
    return f"{prefix} - DLV - {field}"


def _lookup_name(prefix: str) -> str:
    return f"{prefix} - Lookup - GA4 Measurement ID"


def _conversion_linker_name(prefix: str) -> str:
    return f"{prefix} - Google Ads - Conversion Linker"


def _exact_event_trigger_name(prefix: str, name: str) -> str:
    return f"{prefix} - Exact event - {name}"


def _scoped_event_trigger_name(prefix: str, name: str) -> str:
    return f"{prefix} - Event - GA4 - {name}"


def _scoped_event_tag_name(prefix: str, name: str) -> str:
    return f"{prefix} - GA4 event - {name}"


def _google_ads_conversion_name(prefix: str, name: str) -> str:
    return f"{prefix} - Google Ads - Conversion - {name}"


def _microsoft_uet_base_name(prefix: str, name: str) -> str:
    return f"{prefix} - Microsoft Ads - UET base - {name}"


def _microsoft_uet_conversion_name(prefix: str, name: str) -> str:
    return f"{prefix} - Microsoft Ads - UET conversion - {name}"


def _dlv_reference(prefix: str, field: str) -> str:
    return f"{{{{{_dlv_name(prefix, field)}}}}}"


def _consent_settings(types: Sequence[str]) -> dict[str, Any]:
    return {
        "consentStatus": "needed",
        "consentType": {
            "type": "list",
            "list": [{"type": "template", "value": item} for item in types],
        },
    }


def _exact_match_pattern(values: Sequence[str]) -> str:
    return f"^(?:{'|'.join(re.escape(value) for value in values)})$"


def desired_resources(manifest: Mapping[str, Any]) -> DesiredResources:
    managed = manifest["managed"]
    prefix = managed["resourcePrefix"]
    note = managed["managedNote"]
    deployment_field = managed["deploymentEnvironmentField"]
    surface_field = managed["surfaceField"]

    variables: list[dict[str, Any]] = []
    for field in managed["dataLayerFields"]:
        variables.append(
            {
                "name": _dlv_name(prefix, field),
                "type": "v",
                "parameter": [
                    {"type": "integer", "key": "dataLayerVersion", "value": "2"},
                    {"type": "boolean", "key": "setDefaultValue", "value": "false"},
                    _template_parameter("name", field),
                ],
                "notes": note,
            }
        )

    lookup_rows = []
    for mapping in managed["measurementIdMappings"]:
        lookup_rows.append(
            {
                "type": "map",
                "map": [
                    _template_parameter("key", mapping["environment"]),
                    _template_parameter("value", mapping["measurementId"]),
                ],
            }
        )
    variables.append(
        {
            "name": _lookup_name(prefix),
            "type": "smm",
            "parameter": [
                _template_parameter("input", f"{{{{{_dlv_name(prefix, deployment_field)}}}}}"),
                {"type": "list", "key": "map", "list": lookup_rows},
            ],
            "notes": note,
        }
    )

    surface_filter = [
        _condition(
            "matchRegex",
            f"{{{{{_dlv_name(prefix, surface_field)}}}}}",
            _exact_match_pattern(managed["surfaceValues"]),
        )
    ]
    boot_trigger_name = f"{prefix} - Event - Marketing boot"
    event_trigger_name = f"{prefix} - Event - Allowed marketing events"
    triggers: list[dict[str, Any]] = [
        {
            "name": boot_trigger_name,
            "type": "customEvent",
            "customEventFilter": [
                _condition("equals", "{{_event}}", managed["bootEvent"]),
            ],
            "filter": copy.deepcopy(surface_filter),
            "notes": note,
        },
    ]
    scoped_event_trigger_names: list[tuple[str, str]] = []
    event_parameter_allowlists = managed.get("eventParameterAllowlists", [])
    if event_parameter_allowlists:
        for allowlist in event_parameter_allowlists:
            trigger_name = _scoped_event_trigger_name(prefix, allowlist["name"])
            scoped_event_trigger_names.append((allowlist["name"], trigger_name))
            triggers.append(
                {
                    "name": trigger_name,
                    "type": "customEvent",
                    "customEventFilter": [
                        _condition(
                            "matchRegex",
                            "{{_event}}",
                            _exact_match_pattern(allowlist["events"]),
                        ),
                    ],
                    "filter": copy.deepcopy(surface_filter),
                    "notes": note,
                }
            )
    else:
        triggers.append(
            {
                "name": event_trigger_name,
                "type": "customEvent",
                "customEventFilter": [
                    _condition(
                        "matchRegex",
                        "{{_event}}",
                        managed["allowedEventPattern"],
                    ),
                ],
                "filter": copy.deepcopy(surface_filter),
                "notes": note,
            }
        )
    exact_event_trigger_names: list[tuple[str, str]] = []
    for trigger in managed.get("exactEventTriggers", []):
        trigger_name = _exact_event_trigger_name(prefix, trigger["name"])
        exact_event_trigger_names.append((trigger["name"], trigger_name))
        conditions = copy.deepcopy(surface_filter)
        conditions.extend(
            _condition(
                condition["operator"],
                _dlv_reference(prefix, condition["field"]),
                condition["value"],
            )
            for condition in trigger["conditions"]
        )
        triggers.append(
            {
                "name": trigger_name,
                "type": "customEvent",
                "customEventFilter": [
                    _condition("equals", "{{_event}}", trigger["event"]),
                ],
                "filter": conditions,
                "notes": note,
            }
        )
    return DesiredResources(
        variables=tuple(variables),
        triggers=tuple(triggers),
        boot_trigger_name=boot_trigger_name,
        event_trigger_name=event_trigger_name,
        scoped_event_trigger_names=tuple(scoped_event_trigger_names),
        exact_event_trigger_names=tuple(exact_event_trigger_names),
    )


def _google_ads_conversion_tag(
    *,
    prefix: str,
    note: str,
    action: Mapping[str, Any],
    trigger_id: str,
) -> dict[str, Any]:
    return {
        "name": _google_ads_conversion_name(prefix, action["name"]),
        "type": "awct",
        "paused": False,
        "parameter": [
            _boolean_parameter("enableNewCustomerReporting", False),
            _boolean_parameter("enableConversionLinker", True),
            _template_parameter(
                "orderId",
                _dlv_reference(prefix, action["orderIdField"]),
            ),
            _boolean_parameter("enableProductReporting", False),
            _template_parameter(
                "conversionValue",
                _dlv_reference(prefix, action["conversionValueField"]),
            ),
            _boolean_parameter("enableShippingData", False),
            _template_parameter("conversionId", action["conversionId"]),
            _template_parameter(
                "currencyCode",
                _dlv_reference(prefix, action["currencyCodeField"]),
            ),
            _template_parameter("conversionLabel", action["conversionLabel"]),
            _boolean_parameter("rdp", False),
        ],
        "firingTriggerId": [trigger_id],
        "consentSettings": _consent_settings(action["consentTypes"]),
        "notes": note,
    }


def _microsoft_uet_base_tag(
    *,
    prefix: str,
    note: str,
    base_tag: Mapping[str, Any],
    trigger_id: str,
) -> dict[str, Any]:
    return {
        "name": _microsoft_uet_base_name(prefix, base_tag["name"]),
        "type": "baut",
        "paused": False,
        "parameter": [
            _boolean_parameter("c_navTimingApi", False),
            _template_parameter("tagId", base_tag["tagId"]),
            _boolean_parameter("c_consentInheritGtm", True),
            _boolean_parameter("c_storeConvTrackCookies", True),
            _template_parameter("uetqName", base_tag.get("uetqName", "uetq")),
            _boolean_parameter("c_removeQueryFromUrls", True),
            _boolean_parameter("c_disableAutoPageView", False),
            _boolean_parameter("c_enhancedConversion", False),
            _boolean_parameter("c_consentUpdates", True),
            _template_parameter("eventType", "PAGE_LOAD"),
            _boolean_parameter(
                "c_enableAutoSpaTracking",
                base_tag.get("enableAutoSpaTracking", False),
            ),
        ],
        "firingTriggerId": [trigger_id],
        "consentSettings": _consent_settings(base_tag["consentTypes"]),
        "notes": note,
    }


def _microsoft_uet_conversion_tag(
    *,
    prefix: str,
    note: str,
    action: Mapping[str, Any],
    trigger_id: str,
) -> dict[str, Any]:
    return {
        "name": _microsoft_uet_conversion_name(prefix, action["name"]),
        "type": "baut",
        "paused": False,
        "parameter": [
            _template_parameter(
                "goalValue",
                _dlv_reference(prefix, action["goalValueField"]),
            ),
            _template_parameter(
                "p_currency",
                _dlv_reference(prefix, action["currencyField"]),
            ),
            _template_parameter(
                "eventCategory",
                _dlv_reference(prefix, action["eventCategoryField"]),
            ),
            _template_parameter("uetqName", action.get("uetqName", "uetq")),
            _template_parameter("customEventAction", "purchase"),
            _template_parameter("eventType", "CUSTOM"),
            {
                "type": "list",
                "key": "customParamTable",
                "list": [
                    {
                        "type": "map",
                        "map": [
                            _template_parameter(
                                "customParamName",
                                "transaction_id",
                            ),
                            _template_parameter(
                                "customParamValue",
                                _dlv_reference(
                                    prefix,
                                    action["transactionIdField"],
                                ),
                            ),
                        ],
                    }
                ],
            },
            _template_parameter(
                "eventLabel",
                _dlv_reference(prefix, action["eventLabelField"]),
            ),
        ],
        "firingTriggerId": [trigger_id],
        "consentSettings": _consent_settings(action["consentTypes"]),
        "notes": note,
    }


def desired_tags(
    manifest: Mapping[str, Any],
    *,
    trigger_ids: Mapping[str, str],
) -> tuple[dict[str, Any], ...]:
    managed = manifest["managed"]
    prefix = managed["resourcePrefix"]
    note = managed["managedNote"]
    page_location_field = managed["pageLocationField"]
    resources = desired_resources(manifest)
    measurement_variable = f"{{{{{_lookup_name(prefix)}}}}}"
    exact_trigger_names = dict(resources.exact_event_trigger_names)

    def provider_trigger_id(trigger_name: str) -> str:
        resource_name = exact_trigger_names.get(trigger_name)
        if resource_name is None:
            raise GtmCtlError(
                f"Provider trigger {trigger_name!r} was not found in managed exact event triggers"
            )
        trigger_id = trigger_ids.get(resource_name)
        if trigger_id is None:
            raise GtmCtlError(f"No GTM trigger ID was resolved for {resource_name!r}")
        return trigger_id

    event_parameters_by_name = {
        parameter["name"]: parameter
        for parameter in managed["eventParameters"]
    }

    def event_settings_rows(parameter_names: Sequence[str]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for parameter_name in parameter_names:
            parameter = event_parameters_by_name[parameter_name]
            rows.append(
                {
                    "type": "map",
                    "map": [
                        _template_parameter("parameter", parameter["name"]),
                        _template_parameter(
                            "parameterValue",
                            f"{{{{{_dlv_name(prefix, parameter['field'])}}}}}",
                        ),
                    ],
                }
            )
        return rows

    def ga4_event_tag(
        *,
        name: str,
        parameter_names: Sequence[str],
        trigger_id: str,
    ) -> dict[str, Any]:
        return {
            "name": name,
            "type": "gaawe",
            "paused": False,
            "parameter": [
                {"type": "boolean", "key": "sendEcommerceData", "value": "false"},
                {
                    "type": "list",
                    "key": "eventSettingsTable",
                    "list": event_settings_rows(parameter_names),
                },
                _template_parameter("eventName", "{{Event}}"),
                _template_parameter("measurementIdOverride", measurement_variable),
            ],
            "firingTriggerId": [trigger_id],
            "consentSettings": _consent_settings(managed["consentTypes"]),
            "notes": note,
        }

    google_tag = {
        "name": f"{prefix} - Google tag - Public marketing",
        "type": "googtag",
        "paused": False,
        "parameter": [
            _template_parameter("tagId", measurement_variable),
            {
                "type": "list",
                "key": "configSettingsTable",
                "list": [
                    {
                        "type": "map",
                        "map": [
                            _template_parameter("parameter", "send_page_view"),
                            _template_parameter(
                                "parameterValue",
                                "true" if managed["sendPageView"] else "false",
                            ),
                        ],
                    },
                    {
                        "type": "map",
                        "map": [
                            _template_parameter("parameter", "page_location"),
                            _template_parameter(
                                "parameterValue",
                                f"{{{{{_dlv_name(prefix, page_location_field)}}}}}",
                            ),
                        ],
                    },
                    {
                        "type": "map",
                        "map": [
                            _template_parameter("parameter", "allow_google_signals"),
                            _template_parameter("parameterValue", "false"),
                        ],
                    },
                    {
                        "type": "map",
                        "map": [
                            _template_parameter(
                                "parameter",
                                "allow_ad_personalization_signals",
                            ),
                            _template_parameter("parameterValue", "false"),
                        ],
                    },
                ],
            },
        ],
        "firingTriggerId": [trigger_ids[resources.boot_trigger_name]],
        "consentSettings": _consent_settings(managed["consentTypes"]),
        "notes": note,
    }
    tags = [google_tag]
    event_parameter_allowlists = managed.get("eventParameterAllowlists", [])
    if event_parameter_allowlists:
        scoped_trigger_names = dict(resources.scoped_event_trigger_names)
        for allowlist in event_parameter_allowlists:
            trigger_name = scoped_trigger_names[allowlist["name"]]
            tags.append(
                ga4_event_tag(
                    name=_scoped_event_tag_name(prefix, allowlist["name"]),
                    parameter_names=allowlist["parameters"],
                    trigger_id=trigger_ids[trigger_name],
                )
            )
    else:
        tags.append(
            ga4_event_tag(
                name=f"{prefix} - GA4 event - Allowed marketing event",
                parameter_names=tuple(event_parameters_by_name),
                trigger_id=trigger_ids[resources.event_trigger_name],
            )
        )
    google_ads = managed.get("googleAds")
    if google_ads and google_ads["enableConversionLinker"]:
        conversion_linker_trigger_name = google_ads.get(
            "conversionLinkerTriggerName"
        )
        conversion_linker_trigger_id = (
            provider_trigger_id(conversion_linker_trigger_name)
            if conversion_linker_trigger_name is not None
            else trigger_ids[resources.boot_trigger_name]
        )
        tags.append(
            {
                "name": _conversion_linker_name(prefix),
                "type": "gclidw",
                "paused": False,
                "parameter": [
                    {
                        "type": "boolean",
                        "key": "enableCookieOverrides",
                        "value": "false",
                    }
                ],
                "firingTriggerId": [conversion_linker_trigger_id],
                "consentSettings": _consent_settings(google_ads["consentTypes"]),
                "notes": note,
            }
        )
    if google_ads:
        for action in google_ads.get("conversionActions", []):
            tags.append(
                _google_ads_conversion_tag(
                    prefix=prefix,
                    note=note,
                    action=action,
                    trigger_id=provider_trigger_id(action["triggerName"]),
                )
            )

    microsoft_ads = managed.get("microsoftAds")
    if microsoft_ads:
        for base_tag in microsoft_ads.get("baseTags", []):
            tags.append(
                _microsoft_uet_base_tag(
                    prefix=prefix,
                    note=note,
                    base_tag=base_tag,
                    trigger_id=provider_trigger_id(base_tag["triggerName"]),
                )
            )
        for action in microsoft_ads.get("conversionActions", []):
            tags.append(
                _microsoft_uet_conversion_tag(
                    prefix=prefix,
                    note=note,
                    action=action,
                    trigger_id=provider_trigger_id(action["triggerName"]),
                )
            )
    return tuple(tags)


def _index_by_name(resources: Sequence[Mapping[str, Any]], kind: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for resource in resources:
        name = resource.get("name")
        if not isinstance(name, str) or not name:
            continue
        if name in result:
            raise GtmCtlError(f"Duplicate {kind} name in workspace: {name}")
        result[name] = dict(resource)
    return result


def _desired_resource_equal(
    existing: Mapping[str, Any],
    desired: Mapping[str, Any],
) -> bool:
    current = _mutable_resource(existing)
    expected = _mutable_resource(desired)
    if expected.get("paused") is False and "paused" not in current:
        current["paused"] = False
    return current == expected


def _mutable_resource(resource: Mapping[str, Any]) -> dict[str, Any]:
    return {key: copy.deepcopy(value) for key, value in resource.items() if key not in SERVER_ONLY_FIELDS}


def _action_for(existing: Mapping[str, Any] | None, desired: Mapping[str, Any]) -> str:
    if existing is None:
        return "create"
    return "noop" if _desired_resource_equal(existing, desired) else "update"


class GtmControlPlane:
    def __init__(self, manifest: Mapping[str, Any], client: GtmClient) -> None:
        self.manifest = dict(manifest)
        self.client = client

    def target(self) -> Target:
        target = self.manifest["target"]
        return self.client.resolve_target(target["accountName"], target["containerName"])

    def _live_version_summary(self, target: Target) -> dict[str, Any]:
        live_version = self.client.live_version(target)
        if live_version is None:
            return {"present": False}
        return {
            "present": True,
            "id": live_version.get("containerVersionId"),
            "name": live_version.get("name"),
            "counts": {
                kind: len(live_version.get(response_key, []))
                if isinstance(live_version.get(response_key, []), list)
                else 0
                for kind, response_key in RESOURCE_KEYS.items()
            },
        }

    def inventory(self, workspace_name: str | None = None) -> dict[str, Any]:
        target = self.target()
        workspaces = self.client.list_workspaces(target)
        result: dict[str, Any] = {
            "account": {"name": target.account.get("name")},
            "container": {
                "name": target.container.get("name"),
                "publicId": target.container.get("publicId"),
            },
            "workspaces": [
                {"name": item.get("name"), "description": item.get("description", "")}
                for item in workspaces
            ],
            "liveVersion": self._live_version_summary(target),
        }
        if workspace_name:
            workspace = _unique_by_name(workspaces, workspace_name, "workspace")
            workspace_path = _require_resource_path(workspace)
            result["selectedWorkspace"] = {
                "name": workspace_name,
                "counts": {
                    kind: len(self.client.list_resources(workspace_path, kind))
                    for kind in RESOURCE_KEYS
                },
            }
        return result

    def _workspace_resources(self, workspace_path: str) -> dict[str, list[dict[str, Any]]]:
        return {
            kind: self.client.list_resources(workspace_path, kind)
            for kind in RESOURCE_KEYS
        }

    def _plan_in_workspace(
        self,
        workspace: Mapping[str, Any],
    ) -> dict[str, Any]:
        workspace_path = _require_resource_path(workspace)
        current = self._workspace_resources(workspace_path)
        desired = desired_resources(self.manifest)
        variable_index = _index_by_name(current["variables"], "variable")
        trigger_index = _index_by_name(current["triggers"], "trigger")
        tag_index = _index_by_name(current["tags"], "tag")

        variable_actions = [
            {"name": item["name"], "action": _action_for(variable_index.get(item["name"]), item)}
            for item in desired.variables
        ]
        trigger_actions = [
            {"name": item["name"], "action": _action_for(trigger_index.get(item["name"]), item)}
            for item in desired.triggers
        ]

        trigger_ids: dict[str, str] = {}
        for trigger in desired.triggers:
            current_trigger = trigger_index.get(trigger["name"])
            trigger_id = current_trigger.get("triggerId") if current_trigger else None
            trigger_ids[trigger["name"]] = str(trigger_id or f"pending:{trigger['name']}")
        tags = desired_tags(self.manifest, trigger_ids=trigger_ids)
        tag_actions = []
        for item in tags:
            existing = tag_index.get(item["name"])
            if any(value.startswith("pending:") for value in item["firingTriggerId"]):
                action = "create" if existing is None else "update"
            else:
                action = _action_for(existing, item)
            tag_actions.append({"name": item["name"], "action": action})

        superseded_actions = self._superseded_plan(current)
        return {
            "workspace": {"name": workspace.get("name")},
            "managed": {
                "variables": variable_actions,
                "triggers": trigger_actions,
                "tags": tag_actions,
            },
            "superseded": superseded_actions,
        }

    def plan(self, workspace_name: str | None = None) -> dict[str, Any]:
        target = self.target()
        selected = workspace_name or self.manifest["workspace"]["compareName"]
        workspace = self.client.resolve_workspace(target, selected)
        return self._plan_in_workspace(workspace)

    def _superseded_plan(
        self,
        current: Mapping[str, Sequence[Mapping[str, Any]]],
    ) -> list[dict[str, str]]:
        superseded = self.manifest.get("superseded", {})
        indexes = {
            kind: _index_by_name(list(current[kind]), kind[:-1])
            for kind in RESOURCE_KEYS
        }
        actions: list[dict[str, str]] = []
        for name in superseded.get("pauseTags", []):
            existing = indexes["tags"].get(name)
            action = "missing" if existing is None else ("noop" if existing.get("paused") else "pause")
            actions.append({"resource": "tag", "name": name, "action": action})
        for manifest_key, kind in (
            ("deleteTags", "tags"),
            ("deleteTriggers", "triggers"),
            ("deleteVariables", "variables"),
        ):
            for name in superseded.get(manifest_key, []):
                action = "delete" if name in indexes[kind] else "missing"
                actions.append({"resource": kind[:-1], "name": name, "action": action})
        return actions

    def _upsert_many(
        self,
        workspace_path: str,
        kind: str,
        desired_items: Sequence[Mapping[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
        existing_items = self.client.list_resources(workspace_path, kind)
        existing_index = _index_by_name(existing_items, kind[:-1])
        final: list[dict[str, Any]] = []
        actions: list[dict[str, str]] = []
        for desired in desired_items:
            existing = existing_index.get(str(desired["name"]))
            action = _action_for(existing, desired)
            if action == "create":
                saved = self.client.create_resource(workspace_path, kind, desired)
            elif action == "update":
                assert existing is not None
                saved = self.client.update_resource(existing, desired)
            else:
                assert existing is not None
                saved = existing
            final.append(saved)
            actions.append({"name": str(desired["name"]), "action": action})
        return final, actions

    def _apply_superseded(self, workspace_path: str) -> list[dict[str, str]]:
        current = self._workspace_resources(workspace_path)
        indexes = {
            kind: _index_by_name(current[kind], kind[:-1])
            for kind in RESOURCE_KEYS
        }
        superseded = self.manifest.get("superseded", {})
        actions: list[dict[str, str]] = []

        for name in superseded.get("pauseTags", []):
            existing = indexes["tags"].get(name)
            if existing is None:
                action = "missing"
            elif existing.get("paused"):
                action = "noop"
            else:
                body = _mutable_resource(existing)
                body["paused"] = True
                self.client.update_resource(existing, body)
                action = "pause"
            actions.append({"resource": "tag", "name": name, "action": action})

        for manifest_key, kind in (
            ("deleteTags", "tags"),
            ("deleteTriggers", "triggers"),
            ("deleteVariables", "variables"),
        ):
            for name in superseded.get(manifest_key, []):
                existing = indexes[kind].get(name)
                if existing is None:
                    action = "missing"
                else:
                    self.client.delete_resource(existing)
                    action = "delete"
                actions.append({"resource": kind[:-1], "name": name, "action": action})
        return actions

    @staticmethod
    def _refuse_default_workspace(workspace_name: str) -> None:
        if workspace_name.strip().casefold() == "default workspace":
            raise GtmCtlError(
                "Default Workspace cannot be used for apply or resume. "
                "Use a dedicated review workspace."
            )

    def _reconcile_workspace(self, workspace: Mapping[str, Any]) -> dict[str, Any]:
        workspace_path = _require_resource_path(workspace)
        built_ins = self.client.ensure_built_in_variables(
            workspace_path,
            self.manifest["managed"].get("ensureBuiltInVariables", []),
        )
        desired = desired_resources(self.manifest)
        _, variable_actions = self._upsert_many(
            workspace_path,
            "variables",
            desired.variables,
        )
        saved_triggers, trigger_actions = self._upsert_many(
            workspace_path,
            "triggers",
            desired.triggers,
        )
        trigger_ids: dict[str, str] = {}
        for trigger in saved_triggers:
            name = str(trigger.get("name"))
            trigger_id = trigger.get("triggerId")
            if not trigger_id:
                raise GtmCtlError(f"Saved trigger {name!r} did not include triggerId")
            trigger_ids[name] = str(trigger_id)
        tags = desired_tags(self.manifest, trigger_ids=trigger_ids)
        _, tag_actions = self._upsert_many(workspace_path, "tags", tags)
        superseded_actions = self._apply_superseded(workspace_path)
        return {
            "workspace": {
                "name": workspace.get("name"),
                "path": workspace_path,
            },
            "builtInVariablesCreated": built_ins,
            "managed": {
                "variables": variable_actions,
                "triggers": trigger_actions,
                "tags": tag_actions,
            },
            "superseded": superseded_actions,
            "publishAttempted": False,
        }

    def apply(self, workspace_name: str) -> dict[str, Any]:
        self._refuse_default_workspace(workspace_name)
        target = self.target()
        existing_names = {item.get("name") for item in self.client.list_workspaces(target)}
        if workspace_name in existing_names:
            raise GtmCtlError(
                f"Workspace {workspace_name!r} already exists. Apply always requires a fresh workspace."
            )
        workspace = self.client.create_workspace(
            target,
            name=workspace_name,
            description=self.manifest["workspace"]["description"],
        )
        return self._reconcile_workspace(workspace)

    def resume(self, workspace_name: str) -> dict[str, Any]:
        self._refuse_default_workspace(workspace_name)
        target = self.target()
        workspace = self.client.resolve_workspace(target, workspace_name)
        self._refuse_default_workspace(str(workspace.get("name", "")))
        expected_description = self.manifest["workspace"]["description"]
        actual_description = workspace.get("description", "")
        if actual_description != expected_description:
            raise GtmCtlError(
                f"Workspace {workspace_name!r} description does not match the manifest. "
                "Refusing to mutate it."
            )
        return self._reconcile_workspace(workspace)

    def validate(self, workspace_name: str) -> dict[str, Any]:
        target = self.target()
        workspace = self.client.resolve_workspace(target, workspace_name)
        workspace_path = _require_resource_path(workspace)
        plan = self._plan_in_workspace(workspace)
        drift = [
            {"kind": kind, **item}
            for kind, items in plan["managed"].items()
            for item in items
            if item["action"] != "noop"
        ]
        superseded_drift = [
            item for item in plan["superseded"] if item["action"] not in ("noop", "missing")
        ]
        status = self.client.workspace_status(workspace_path)
        preview = self.client.quick_preview(workspace_path)
        compiler_error = bool(preview.get("compilerError"))
        conflicts = status.get("mergeConflict", [])
        valid = not drift and not superseded_drift and not compiler_error and not conflicts
        result = {
            "workspace": {"name": workspace_name, "path": workspace_path},
            "valid": valid,
            "managedDrift": drift,
            "supersededDrift": superseded_drift,
            "mergeConflictCount": len(conflicts) if isinstance(conflicts, list) else 0,
            "compilerError": compiler_error,
            "quickPreview": {
                "syncStatus": preview.get("syncStatus"),
                "containerVersionPresent": isinstance(preview.get("containerVersion"), Mapping),
            },
            "liveVersion": self._live_version_summary(target),
            "publishAttempted": False,
        }
        if not valid:
            raise GtmCtlError(json.dumps(result, sort_keys=True))
        return result


def _client_from_manifest(manifest: Mapping[str, Any]) -> GtmClient:
    api_base_url = os.environ.get("GTM_API_BASE_URL", DEFAULT_API_BASE_URL)
    token_url = os.environ.get("GTM_TOKEN_URL", DEFAULT_TOKEN_URL)
    return GtmClient(
        manifest["oauth"],
        api_base_url=api_base_url,
        token_url=token_url,
    )


def _print_result(value: Mapping[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(value, indent=2, sort_keys=True))
        return
    print(json.dumps(value, indent=2, sort_keys=True))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gtmctl",
        description="Manage reviewable GTM workspaces without creating or publishing versions.",
    )
    parser.add_argument("--manifest", required=True, help="Path to a GTM manifest JSON file")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    commands = parser.add_subparsers(dest="command", required=True)

    doctor = commands.add_parser("doctor", help="Validate configuration and API access")
    doctor.add_argument("--offline", action="store_true", help="Skip OAuth and GTM API checks")

    inventory = commands.add_parser("inventory", help="List the selected container and workspaces")
    inventory.add_argument("--workspace", help="Also count resources in this workspace")

    plan = commands.add_parser("plan", help="Compare desired resources with an existing workspace")
    plan.add_argument("--workspace", help="Workspace name, otherwise workspace.compareName")

    apply = commands.add_parser("apply", help="Create and populate a fresh unpublished workspace")
    apply.add_argument("--workspace", required=True, help="Fresh workspace name")
    apply.add_argument("--yes", action="store_true", help="Confirm workspace creation and edits")

    resume = commands.add_parser(
        "resume",
        help="Reconcile a matching existing unpublished workspace after an interrupted apply",
    )
    resume.add_argument("--workspace", required=True, help="Existing review workspace name")
    resume.add_argument("--yes", action="store_true", help="Confirm workspace edits")

    validate = commands.add_parser("validate", help="Run drift, conflict, and quick-preview checks")
    validate.add_argument("--workspace", required=True, help="Workspace name to validate")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        bundle = load_manifest(args.manifest)
        if args.command == "doctor":
            result: dict[str, Any] = {
                "manifest": str(bundle.source),
                "schemaVersion": bundle.resolved["schemaVersion"],
                "opReferenceCount": len(bundle.op_refs),
                "configuration": "ok",
                "online": not args.offline,
            }
            if not args.offline:
                target = _client_from_manifest(bundle.resolved).resolve_target(
                    bundle.resolved["target"]["accountName"],
                    bundle.resolved["target"]["containerName"],
                )
                result["target"] = {
                    "account": target.account.get("name"),
                    "container": target.container.get("name"),
                }
            _print_result(result, as_json=args.json)
            return 0

        control = GtmControlPlane(bundle.resolved, _client_from_manifest(bundle.resolved))
        if args.command == "inventory":
            result = control.inventory(args.workspace)
        elif args.command == "plan":
            result = control.plan(args.workspace)
        elif args.command == "apply":
            if not args.yes:
                raise GtmCtlError("apply requires --yes")
            result = control.apply(args.workspace)
        elif args.command == "resume":
            if not args.yes:
                raise GtmCtlError("resume requires --yes")
            result = control.resume(args.workspace)
        elif args.command == "validate":
            result = control.validate(args.workspace)
        else:
            parser.error(f"Unsupported command: {args.command}")
            return 2
        _print_result(result, as_json=args.json)
        return 0
    except GtmCtlError as exc:
        print(f"gtmctl: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

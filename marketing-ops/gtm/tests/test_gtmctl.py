from __future__ import annotations

import copy
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


MODULE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_DIR))

import gtmctl  # noqa: E402


def manifest() -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "target": {
            "accountName": "Example Account",
            "containerName": "Example Container",
        },
        "oauth": {
            "clientId": "client-id",
            "clientSecret": "client-secret",
            "refreshToken": "refresh-token",
        },
        "workspace": {
            "compareName": "Default Workspace",
            "description": "Review managed analytics resources",
        },
        "managed": {
            "resourcePrefix": "APP",
            "managedNote": "Managed by gtmctl",
            "deploymentEnvironmentField": "page_environment",
            "pageLocationField": "page_location",
            "surfaceField": "route_surface",
            "surfaceValues": ["marketing", "billing_confirmation"],
            "bootEvent": "marketing_boot",
            "allowedEventPattern": "^marketing_(cta_click|navigation_click|login_click)$",
            "dataLayerFields": [
                "app_id",
                "page_environment",
                "page_location",
                "route_surface",
                "cta_id",
                "cta_location",
                "commercial_pathway",
                "destination_path",
            ],
            "measurementIdMappings": [
                {"environment": "production", "measurementId": "measurement-production"},
                {"environment": "staging", "measurementId": "measurement-staging"},
            ],
            "eventParameters": [
                {"name": "app_id", "field": "app_id"},
                {"name": "page_environment", "field": "page_environment"},
                {"name": "route_surface", "field": "route_surface"},
                {"name": "cta_id", "field": "cta_id"},
                {"name": "cta_location", "field": "cta_location"},
                {"name": "commercial_pathway", "field": "commercial_pathway"},
                {"name": "destination_path", "field": "destination_path"},
            ],
            "consentTypes": ["analytics_storage"],
            "sendPageView": True,
            "googleAds": {
                "enableConversionLinker": True,
                "consentTypes": ["ad_storage"],
            },
            "ensureBuiltInVariables": ["event"],
        },
        "superseded": {
            "pauseTags": ["Legacy listed tag"],
            "deleteTags": [],
            "deleteTriggers": [],
            "deleteVariables": [],
        },
    }


def provider_manifest() -> dict[str, Any]:
    value = manifest()
    value["managed"]["dataLayerFields"].extend(
        [
            "conversion_name",
            "conversion_value",
            "currency",
            "transaction_id",
            "event_label",
        ]
    )
    value["managed"]["exactEventTriggers"] = [
        {
            "name": "provider-boot-production",
            "event": "marketing_boot",
            "conditions": [
                {
                    "field": "page_environment",
                    "operator": "equals",
                    "value": "production",
                }
            ],
        },
        {
            "name": "provider-purchase-production",
            "event": "purchase",
            "conditions": [
                {
                    "field": "page_environment",
                    "operator": "equals",
                    "value": "production",
                },
                {
                    "field": "conversion_name",
                    "operator": "equals",
                    "value": "subscription_purchase",
                },
            ],
        },
    ]
    value["managed"]["googleAds"]["conversionActions"] = [
        {
            "name": "purchase-production",
            "conversionId": "configured-conversion-id",
            "conversionLabel": "configured-conversion-label",
            "triggerName": "provider-purchase-production",
            "consentTypes": ["ad_storage", "ad_user_data"],
            "orderIdField": "transaction_id",
            "conversionValueField": "conversion_value",
            "currencyCodeField": "currency",
        }
    ]
    value["managed"]["microsoftAds"] = {
        "baseTags": [
            {
                "name": "production",
                "tagId": "configured-uet-tag-id",
                "triggerName": "provider-boot-production",
                "enableAutoSpaTracking": True,
                "consentTypes": ["ad_storage"],
            }
        ],
        "conversionActions": [
            {
                "name": "purchase-production",
                "triggerName": "provider-purchase-production",
                "consentTypes": ["ad_storage"],
                "goalValueField": "conversion_value",
                "currencyField": "currency",
                "eventCategoryField": "conversion_name",
                "transactionIdField": "transaction_id",
                "eventLabelField": "event_label",
            }
        ],
    }
    return value


def scoped_event_manifest() -> dict[str, Any]:
    value = provider_manifest()
    value["managed"]["allowedEventPattern"] = (
        "^(?:page_view|marketing_cta_click|purchase)$"
    )
    value["managed"]["eventParameters"].extend(
        [
            {"name": "page_location", "field": "page_location"},
            {"name": "conversion_name", "field": "conversion_name"},
            {"name": "value", "field": "conversion_value"},
            {"name": "currency", "field": "currency"},
            {"name": "transaction_id", "field": "transaction_id"},
        ]
    )
    value["managed"]["eventParameterAllowlists"] = [
        {
            "name": "page-view",
            "events": ["page_view"],
            "parameters": [
                "app_id",
                "page_environment",
                "route_surface",
                "page_location",
            ],
        },
        {
            "name": "marketing-interaction",
            "events": ["marketing_cta_click"],
            "parameters": [
                "app_id",
                "page_environment",
                "route_surface",
                "page_location",
                "cta_id",
                "cta_location",
                "commercial_pathway",
                "destination_path",
            ],
        },
        {
            "name": "purchase",
            "events": ["purchase"],
            "parameters": [
                "app_id",
                "page_environment",
                "route_surface",
                "page_location",
                "commercial_pathway",
                "conversion_name",
                "transaction_id",
                "value",
                "currency",
            ],
        },
    ]
    return value


class MockState:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.next_workspace = 9
        self.next_ids = {"variables": 100, "triggers": 200, "tags": 300}
        self.compiler_error = False
        self.live_version_response_override: Any | None = None
        self.quick_preview_response_override: Any | None = None
        self.transient_failures: dict[tuple[str, str], list[int]] = {}
        self.workspaces: dict[str, dict[str, Any]] = {
            "3": {
                "workspace": {
                    "name": "Default Workspace",
                    "description": "",
                    "workspaceId": "3",
                    "path": "accounts/1/containers/2/workspaces/3",
                },
                "variables": [],
                "triggers": [],
                "tags": [
                    {
                        "name": "Legacy listed tag",
                        "type": "html",
                        "paused": False,
                        "tagId": "1",
                        "path": "accounts/1/containers/2/workspaces/3/tags/1",
                        "fingerprint": "f-1",
                    },
                    {
                        "name": "Legacy unlisted tag",
                        "type": "html",
                        "paused": False,
                        "tagId": "2",
                        "path": "accounts/1/containers/2/workspaces/3/tags/2",
                        "fingerprint": "f-2",
                    },
                ],
                "builtInVariable": [],
            }
        }

    def workspace_for_path(self, path: str) -> tuple[str, dict[str, Any]]:
        parts = path.split("/")
        workspace_index = parts.index("workspaces")
        workspace_id = parts[workspace_index + 1].split(":", 1)[0]
        return workspace_id, self.workspaces[workspace_id]


class MockHandler(BaseHTTPRequestHandler):
    server: "MockServer"

    def log_message(self, format: str, *args: object) -> None:
        del format, args

    def _read_body(self) -> Any:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return None
        if self.headers.get("Content-Type") == "application/json":
            return json.loads(raw)
        return urllib.parse.parse_qs(raw.decode("utf-8"))

    def _send(
        self,
        status: int,
        body: Any,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(payload)

    def _record(self, body: Any) -> tuple[str, dict[str, list[str]]]:
        parsed = urllib.parse.urlparse(self.path)
        self.server.state.requests.append(
            {
                "method": self.command,
                "path": parsed.path,
                "query": urllib.parse.parse_qs(parsed.query),
                "body": body,
            }
        )
        return parsed.path, urllib.parse.parse_qs(parsed.query)

    def _maybe_fail_transiently(self, path: str) -> bool:
        failures = self.server.state.transient_failures.get((self.command, path), [])
        if not failures:
            return False
        status = failures.pop(0)
        self._send(
            status,
            {"error": {"message": f"simulated transient HTTP {status}"}},
            headers={"Retry-After": "0"},
        )
        return True

    def _require_auth(self) -> bool:
        if self.headers.get("Authorization") != "Bearer access-token":
            self._send(401, {"error": {"message": "missing bearer token"}})
            return False
        return True

    def do_GET(self) -> None:
        body = self._read_body()
        path, _ = self._record(body)
        if not self._require_auth():
            return
        if self._maybe_fail_transiently(path):
            return
        api_path = path.removeprefix("/tagmanager/v2/")
        if api_path == "accounts":
            self._send(200, {"account": [{"name": "Example Account", "accountId": "1", "path": "accounts/1"}]})
            return
        if api_path == "accounts/1/containers":
            self._send(
                200,
                {
                    "container": [
                        {
                            "name": "Example Container",
                            "containerId": "2",
                            "publicId": "public-container-id",
                            "path": "accounts/1/containers/2",
                        }
                    ]
                },
            )
            return
        if api_path == "accounts/1/containers/2/workspaces":
            self._send(200, {"workspace": [value["workspace"] for value in self.server.state.workspaces.values()]})
            return
        if api_path == "accounts/1/containers/2/versions:live":
            if self.server.state.live_version_response_override is not None:
                self._send(200, self.server.state.live_version_response_override)
                return
            self._send(
                200,
                {
                    "containerVersionId": "4",
                    "name": "Published baseline",
                    "tag": [{"name": "Legacy tag"}],
                    "trigger": [{"name": "All pages"}],
                    "variable": [],
                },
            )
            return
        if "/workspaces/" in api_path:
            workspace_id, workspace = self.server.state.workspace_for_path(api_path)
            prefix = f"accounts/1/containers/2/workspaces/{workspace_id}"
            suffix = api_path.removeprefix(prefix + "/")
            if suffix in ("variables", "triggers", "tags"):
                key = gtmctl.RESOURCE_KEYS[suffix]
                self._send(200, {key: workspace[suffix]})
                return
            if suffix == "built_in_variables":
                self._send(200, {"builtInVariable": workspace["builtInVariable"]})
                return
            if suffix == "status":
                self._send(200, {"workspaceChange": [], "mergeConflict": []})
                return
        self._send(404, {"error": {"message": f"unhandled GET {api_path}"}})

    def do_POST(self) -> None:
        body = self._read_body()
        path, query = self._record(body)
        if path == "/token":
            if self._maybe_fail_transiently(path):
                return
            self._send(200, {"access_token": "access-token", "token_type": "Bearer"})
            return
        if not self._require_auth():
            return
        if self._maybe_fail_transiently(path):
            return
        api_path = path.removeprefix("/tagmanager/v2/")
        if api_path == "accounts/1/containers/2/workspaces":
            workspace_id = str(self.server.state.next_workspace)
            self.server.state.next_workspace += 1
            workspace = {
                "name": body["name"],
                "description": body["description"],
                "workspaceId": workspace_id,
                "path": f"accounts/1/containers/2/workspaces/{workspace_id}",
            }
            base = self.server.state.workspaces["3"]
            copied = {
                "workspace": workspace,
                "variables": copy.deepcopy(base["variables"]),
                "triggers": copy.deepcopy(base["triggers"]),
                "tags": copy.deepcopy(base["tags"]),
                "builtInVariable": copy.deepcopy(base["builtInVariable"]),
            }
            for kind in ("variables", "triggers", "tags"):
                id_key = {"variables": "variableId", "triggers": "triggerId", "tags": "tagId"}[kind]
                for item in copied[kind]:
                    item["path"] = f"{workspace['path']}/{kind}/{item[id_key]}"
            self.server.state.workspaces[workspace_id] = copied
            self._send(200, workspace)
            return
        if api_path.endswith(":quick_preview"):
            if self.server.state.quick_preview_response_override is not None:
                self._send(200, self.server.state.quick_preview_response_override)
                return
            self._send(
                200,
                {
                    "compilerError": self.server.state.compiler_error,
                    "containerVersion": {
                        "containerVersionId": "preview",
                        "name": "quick preview",
                        "tag": [],
                        "trigger": [],
                        "variable": [],
                    },
                    "syncStatus": {"mergeConflict": False},
                },
            )
            return
        if "/workspaces/" in api_path:
            workspace_id, workspace = self.server.state.workspace_for_path(api_path)
            prefix = f"accounts/1/containers/2/workspaces/{workspace_id}"
            suffix = api_path.removeprefix(prefix + "/")
            if suffix == "built_in_variables":
                for item in query.get("type", []):
                    workspace["builtInVariable"].append({"name": item.title(), "type": item})
                self._send(200, {})
                return
            if suffix in ("variables", "triggers", "tags"):
                resource_id = str(self.server.state.next_ids[suffix])
                self.server.state.next_ids[suffix] += 1
                id_key = {"variables": "variableId", "triggers": "triggerId", "tags": "tagId"}[suffix]
                saved = copy.deepcopy(body)
                saved[id_key] = resource_id
                saved["path"] = f"{prefix}/{suffix}/{resource_id}"
                saved["fingerprint"] = f"f-{resource_id}"
                workspace[suffix].append(saved)
                self._send(200, saved)
                return
        self._send(404, {"error": {"message": f"unhandled POST {api_path}"}})

    def do_PUT(self) -> None:
        body = self._read_body()
        path, _ = self._record(body)
        if not self._require_auth():
            return
        if self._maybe_fail_transiently(path):
            return
        api_path = path.removeprefix("/tagmanager/v2/")
        workspace_id, workspace = self.server.state.workspace_for_path(api_path)
        del workspace_id
        parts = api_path.split("/")
        kind = parts[-2]
        resource_id = parts[-1]
        id_key = {"variables": "variableId", "triggers": "triggerId", "tags": "tagId"}[kind]
        for index, existing in enumerate(workspace[kind]):
            if str(existing[id_key]) == resource_id:
                saved = copy.deepcopy(body)
                saved[id_key] = resource_id
                saved["path"] = existing["path"]
                saved["fingerprint"] = f"updated-{resource_id}"
                workspace[kind][index] = saved
                self._send(200, saved)
                return
        self._send(404, {"error": {"message": "resource not found"}})

    def do_DELETE(self) -> None:
        body = self._read_body()
        path, _ = self._record(body)
        if not self._require_auth():
            return
        if self._maybe_fail_transiently(path):
            return
        api_path = path.removeprefix("/tagmanager/v2/")
        _, workspace = self.server.state.workspace_for_path(api_path)
        parts = api_path.split("/")
        kind = parts[-2]
        resource_id = parts[-1]
        id_key = {"variables": "variableId", "triggers": "triggerId", "tags": "tagId"}[kind]
        workspace[kind] = [item for item in workspace[kind] if str(item[id_key]) != resource_id]
        self._send(200, {})


class MockServer(ThreadingHTTPServer):
    state: MockState


class RunningMockServer:
    def __init__(self) -> None:
        self.state = MockState()
        self.server = MockServer(("127.0.0.1", 0), MockHandler)
        self.server.state = self.state
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> "RunningMockServer":
        self.thread.start()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        del exc_type, exc, traceback
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    @property
    def base_url(self) -> str:
        host, port = self.server.server_address
        return f"http://{host}:{port}"

    def client(self, *, http: gtmctl.JsonHttp | None = None) -> gtmctl.GtmClient:
        return gtmctl.GtmClient(
            manifest()["oauth"],
            http=http,
            api_base_url=f"{self.base_url}/tagmanager/v2",
            token_url=f"{self.base_url}/token",
        )


class ManifestTests(unittest.TestCase):
    def _raw_manifest(self) -> dict[str, Any]:
        value = manifest()
        value["target"] = {
            "accountName": "${ACCOUNT_NAME}",
            "containerName": "${CONTAINER_NAME}",
        }
        value["oauth"] = {
            "clientId": "${OAUTH_CLIENT_ID}",
            "clientSecret": "${OAUTH_CLIENT_SECRET}",
            "refreshToken": "${OAUTH_REFRESH_TOKEN}",
        }
        value["managed"]["measurementIdMappings"][0]["measurementId"] = "${GA4_PRODUCTION_ID}"
        return value

    def _desired_provider_resources(
        self,
        value: dict[str, Any] | None = None,
    ) -> tuple[gtmctl.DesiredResources, tuple[dict[str, Any], ...]]:
        provider_value = value or provider_manifest()
        resources = gtmctl.desired_resources(provider_value)
        trigger_ids = {
            trigger["name"]: f"trigger-{index}"
            for index, trigger in enumerate(resources.triggers, start=1)
        }
        return resources, gtmctl.desired_tags(
            provider_value,
            trigger_ids=trigger_ids,
        )

    def test_interpolates_environment_and_resolves_op_references(self) -> None:
        raw = self._raw_manifest()
        environ = {
            "ACCOUNT_NAME": "Example Account",
            "CONTAINER_NAME": "Example Container",
            "OAUTH_CLIENT_ID": "op://vault/item/client_id",
            "OAUTH_CLIENT_SECRET": "op://vault/item/client_secret",
            "OAUTH_REFRESH_TOKEN": "op://vault/item/refresh_token",
            "GA4_PRODUCTION_ID": "measurement-production",
        }
        op_values = {
            "op://vault/item/client_id": "client-id",
            "op://vault/item/client_secret": "client-secret",
            "op://vault/item/refresh_token": "refresh-token",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "manifest.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            bundle = gtmctl.load_manifest(path, environ=environ, op_reader=op_values.__getitem__)
        self.assertEqual(bundle.resolved["oauth"]["clientSecret"], "client-secret")
        self.assertEqual(len(bundle.op_refs), 3)
        self.assertEqual(
            bundle.resolved["managed"]["measurementIdMappings"][0]["measurementId"],
            "measurement-production",
        )

    def test_interpolates_provider_configuration_without_shared_ids(self) -> None:
        raw = provider_manifest()
        raw["oauth"] = {
            "clientId": "${OAUTH_CLIENT_ID}",
            "clientSecret": "${OAUTH_CLIENT_SECRET}",
            "refreshToken": "${OAUTH_REFRESH_TOKEN}",
        }
        google_action = raw["managed"]["googleAds"]["conversionActions"][0]
        microsoft_base = raw["managed"]["microsoftAds"]["baseTags"][0]
        google_action["conversionId"] = "${GOOGLE_CONVERSION_ID}"
        google_action["conversionLabel"] = "${GOOGLE_CONVERSION_LABEL}"
        microsoft_base["tagId"] = "${MICROSOFT_UET_TAG_ID}"
        environ = {
            "OAUTH_CLIENT_ID": "oauth-client",
            "OAUTH_CLIENT_SECRET": "oauth-secret",
            "OAUTH_REFRESH_TOKEN": "oauth-refresh",
            "GOOGLE_CONVERSION_ID": "google-conversion",
            "GOOGLE_CONVERSION_LABEL": "google-label",
            "MICROSOFT_UET_TAG_ID": "microsoft-uet",
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "manifest.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            bundle = gtmctl.load_manifest(
                path,
                environ=environ,
                op_reader=lambda value: value,
            )

        resolved_google = bundle.resolved["managed"]["googleAds"][
            "conversionActions"
        ][0]
        resolved_microsoft = bundle.resolved["managed"]["microsoftAds"][
            "baseTags"
        ][0]
        self.assertEqual(resolved_google["conversionId"], "google-conversion")
        self.assertEqual(resolved_google["conversionLabel"], "google-label")
        self.assertEqual(resolved_microsoft["tagId"], "microsoft-uet")

    def test_rejects_plaintext_oauth_in_manifest(self) -> None:
        raw = self._raw_manifest()
        raw["oauth"]["clientSecret"] = "plaintext-secret"
        environ = {
            "ACCOUNT_NAME": "Example Account",
            "CONTAINER_NAME": "Example Container",
            "OAUTH_CLIENT_ID": "client-id",
            "OAUTH_REFRESH_TOKEN": "refresh-token",
            "GA4_PRODUCTION_ID": "measurement-production",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "manifest.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(gtmctl.GtmCtlError, "environment interpolation or an op://"):
                gtmctl.load_manifest(path, environ=environ, op_reader=lambda value: value)

    def test_rejects_missing_environment_variable(self) -> None:
        raw = self._raw_manifest()
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "manifest.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(gtmctl.GtmCtlError, "Missing required environment variables"):
                gtmctl.load_manifest(path, environ={}, op_reader=lambda value: value)

    def test_rejects_superseded_action_against_managed_name(self) -> None:
        value = manifest()
        value["superseded"]["pauseTags"] = ["APP - Google tag - Public marketing"]
        with self.assertRaisesRegex(gtmctl.GtmCtlError, "overlaps managed resources"):
            gtmctl.validate_manifest(value)

    def test_conversion_linker_is_optional_and_disabled_without_side_effects(self) -> None:
        for google_ads in (
            None,
            {
                "enableConversionLinker": False,
                "consentTypes": ["ad_storage"],
            },
        ):
            with self.subTest(google_ads=google_ads):
                value = manifest()
                if google_ads is None:
                    del value["managed"]["googleAds"]
                else:
                    value["managed"]["googleAds"] = google_ads
                value["superseded"]["deleteTags"] = [
                    "APP - Google Ads - Conversion Linker"
                ]

                gtmctl.validate_manifest(value)
                resources = gtmctl.desired_resources(value)
                tags = gtmctl.desired_tags(
                    value,
                    trigger_ids={
                        resources.boot_trigger_name: "boot-trigger",
                        resources.event_trigger_name: "event-trigger",
                    },
                )

                self.assertEqual({tag["type"] for tag in tags}, {"googtag", "gaawe"})

    def test_conversion_linker_has_strict_configuration(self) -> None:
        invalid_values = (
            (None, "must be an object"),
            ({}, "missing required fields"),
            (
                {
                    "enableConversionLinker": True,
                    "consentTypes": ["ad_storage"],
                    "extra": True,
                },
                "contains unknown fields",
            ),
            (
                {"enableConversionLinker": "true", "consentTypes": ["ad_storage"]},
                "enableConversionLinker must be a boolean",
            ),
            (
                {"enableConversionLinker": True, "consentTypes": []},
                "consentTypes must contain non-empty strings",
            ),
            (
                {"enableConversionLinker": True, "consentTypes": ["ad_storage", " "]},
                "consentTypes must contain non-empty strings",
            ),
            (
                {
                    "enableConversionLinker": True,
                    "consentTypes": ["ad_storage", "ad_storage"],
                },
                "consentTypes must not contain duplicates",
            ),
            (
                {
                    "enableConversionLinker": True,
                    "conversionLinkerTriggerName": " ",
                    "consentTypes": ["ad_storage"],
                },
                "conversionLinkerTriggerName must be a non-empty string",
            ),
            (
                {
                    "enableConversionLinker": True,
                    "conversionLinkerTriggerName": "missing-trigger",
                    "consentTypes": ["ad_storage"],
                },
                "conversionLinkerTriggerName must reference managed.exactEventTriggers",
            ),
        )
        for google_ads, expected in invalid_values:
            with self.subTest(google_ads=google_ads):
                value = manifest()
                value["managed"]["googleAds"] = google_ads
                with self.assertRaisesRegex(gtmctl.GtmCtlError, expected):
                    gtmctl.validate_manifest(value)

    def test_enabled_conversion_linker_is_protected_from_superseded_actions(self) -> None:
        for action in ("pauseTags", "deleteTags"):
            with self.subTest(action=action):
                value = manifest()
                value["superseded"][action] = [
                    "APP - Google Ads - Conversion Linker"
                ]
                with self.assertRaisesRegex(
                    gtmctl.GtmCtlError,
                    f"superseded.{action} overlaps managed resources",
                ):
                    gtmctl.validate_manifest(value)

    def test_enabled_conversion_linker_uses_boot_trigger_and_ad_consent(self) -> None:
        value = manifest()
        resources = gtmctl.desired_resources(value)
        tags = gtmctl.desired_tags(
            value,
            trigger_ids={
                resources.boot_trigger_name: "boot-trigger",
                resources.event_trigger_name: "event-trigger",
            },
        )

        linker = next(tag for tag in tags if tag["type"] == "gclidw")
        self.assertEqual(linker["name"], "APP - Google Ads - Conversion Linker")
        self.assertFalse(linker["paused"])
        self.assertEqual(linker["firingTriggerId"], ["boot-trigger"])
        self.assertEqual(linker["notes"], "Managed by gtmctl")
        self.assertEqual(
            linker["parameter"],
            [
                {
                    "type": "boolean",
                    "key": "enableCookieOverrides",
                    "value": "false",
                }
            ],
        )
        self.assertEqual(
            linker["consentSettings"],
            {
                "consentStatus": "needed",
                "consentType": {
                    "type": "list",
                    "list": [{"type": "template", "value": "ad_storage"}],
                },
            },
        )

    def test_enabled_conversion_linker_can_use_named_exact_event_trigger(self) -> None:
        value = provider_manifest()
        value["managed"]["googleAds"][
            "conversionLinkerTriggerName"
        ] = "provider-boot-production"

        gtmctl.validate_manifest(value)
        resources, tags = self._desired_provider_resources(value)
        exact_trigger_resource_name = dict(resources.exact_event_trigger_names)[
            "provider-boot-production"
        ]
        exact_trigger_id = next(
            f"trigger-{index}"
            for index, trigger in enumerate(resources.triggers, start=1)
            if trigger["name"] == exact_trigger_resource_name
        )

        linker = next(tag for tag in tags if tag["type"] == "gclidw")
        self.assertEqual(linker["firingTriggerId"], [exact_trigger_id])

    def test_exact_event_triggers_add_manifest_conditions_to_surface_guard(self) -> None:
        value = provider_manifest()

        gtmctl.validate_manifest(value)
        resources = gtmctl.desired_resources(value)

        self.assertEqual(
            dict(resources.exact_event_trigger_names),
            {
                "provider-boot-production": "APP - Exact event - provider-boot-production",
                "provider-purchase-production": (
                    "APP - Exact event - provider-purchase-production"
                ),
            },
        )
        purchase = next(
            trigger
            for trigger in resources.triggers
            if trigger["name"] == "APP - Exact event - provider-purchase-production"
        )
        self.assertEqual(
            purchase["customEventFilter"],
            [
                {
                    "type": "equals",
                    "parameter": [
                        {"type": "template", "key": "arg0", "value": "{{_event}}"},
                        {"type": "template", "key": "arg1", "value": "purchase"},
                    ],
                }
            ],
        )
        self.assertEqual(
            [condition["type"] for condition in purchase["filter"]],
            ["matchRegex", "equals", "equals"],
        )
        condition_values = [
            [parameter["value"] for parameter in condition["parameter"]]
            for condition in purchase["filter"]
        ]
        self.assertEqual(
            condition_values,
            [
                [
                    "{{APP - DLV - route_surface}}",
                    r"^(?:marketing|billing_confirmation)$",
                ],
                ["{{APP - DLV - page_environment}}", "production"],
                ["{{APP - DLV - conversion_name}}", "subscription_purchase"],
            ],
        )

    def test_exact_event_trigger_contract_is_strict(self) -> None:
        invalid_mutations = (
            (
                lambda value: value["managed"]["exactEventTriggers"][0].update(
                    {"unknown": True}
                ),
                "contains unknown fields",
            ),
            (
                lambda value: value["managed"]["exactEventTriggers"][0][
                    "conditions"
                ][0].update({"field": "missing_field"}),
                "must also be in managed.dataLayerFields",
            ),
            (
                lambda value: value["managed"]["exactEventTriggers"][0][
                    "conditions"
                ][0].update({"operator": "notEquals"}),
                "operator must be one of",
            ),
            (
                lambda value: value["managed"]["exactEventTriggers"][0][
                    "conditions"
                ][0].update({"operator": "matchRegex", "value": "["}),
                "invalid regular expression",
            ),
            (
                lambda value: value["managed"]["exactEventTriggers"].append(
                    copy.deepcopy(value["managed"]["exactEventTriggers"][0])
                ),
                "Duplicate exact event trigger name",
            ),
        )
        for mutate, expected in invalid_mutations:
            with self.subTest(expected=expected):
                value = provider_manifest()
                mutate(value)
                with self.assertRaisesRegex(gtmctl.GtmCtlError, expected):
                    gtmctl.validate_manifest(value)

    def test_event_parameter_allowlists_are_strict_and_disjoint(self) -> None:
        invalid_mutations = (
            (
                lambda value: value["managed"].update(
                    {"eventParameterAllowlists": []}
                ),
                "eventParameterAllowlists must not be empty",
            ),
            (
                lambda value: value["managed"]["eventParameterAllowlists"][0].update(
                    {"unknown": True}
                ),
                "contains unknown fields",
            ),
            (
                lambda value: value["managed"]["eventParameterAllowlists"].append(
                    copy.deepcopy(value["managed"]["eventParameterAllowlists"][0])
                ),
                "Duplicate event parameter allowlist name",
            ),
            (
                lambda value: value["managed"]["eventParameterAllowlists"][1][
                    "events"
                ].append("page_view"),
                "appears in more than one event parameter allowlist",
            ),
            (
                lambda value: value["managed"]["eventParameterAllowlists"][0][
                    "events"
                ].append("not_allowed"),
                "is not allowed by managed.allowedEventPattern",
            ),
            (
                lambda value: value["managed"]["eventParameterAllowlists"][0][
                    "parameters"
                ].append("missing_parameter"),
                "contains unknown event parameter",
            ),
        )
        for mutate, expected in invalid_mutations:
            with self.subTest(expected=expected):
                value = scoped_event_manifest()
                mutate(value)
                with self.assertRaisesRegex(gtmctl.GtmCtlError, expected):
                    gtmctl.validate_manifest(value)

    def test_scoped_ga4_tags_keep_sequential_event_parameters_isolated(self) -> None:
        value = scoped_event_manifest()
        gtmctl.validate_manifest(value)
        resources = gtmctl.desired_resources(value)
        trigger_ids = {
            trigger["name"]: f"trigger-{index}"
            for index, trigger in enumerate(resources.triggers, start=1)
        }
        tags = gtmctl.desired_tags(value, trigger_ids=trigger_ids)

        scoped_triggers = dict(resources.scoped_event_trigger_names)
        self.assertEqual(
            scoped_triggers,
            {
                "page-view": "APP - Event - GA4 - page-view",
                "marketing-interaction": (
                    "APP - Event - GA4 - marketing-interaction"
                ),
                "purchase": "APP - Event - GA4 - purchase",
            },
        )
        for allowlist in value["managed"]["eventParameterAllowlists"]:
            trigger = next(
                item
                for item in resources.triggers
                if item["name"] == scoped_triggers[allowlist["name"]]
            )
            self.assertEqual(
                [condition["type"] for condition in trigger["customEventFilter"]],
                ["matchRegex"],
            )
            self.assertEqual(
                trigger["customEventFilter"][0]["parameter"][1]["value"],
                gtmctl._exact_match_pattern(allowlist["events"]),
            )

        ga4_tags = {
            tag["name"]: tag
            for tag in tags
            if tag["type"] == "gaawe"
        }
        self.assertEqual(
            set(ga4_tags),
            {
                "APP - GA4 event - page-view",
                "APP - GA4 event - marketing-interaction",
                "APP - GA4 event - purchase",
            },
        )

        field_by_variable = {
            f"{{{{APP - DLV - {field}}}}}": field
            for field in value["managed"]["dataLayerFields"]
        }

        def resolved_parameters(
            tag_name: str,
            data_layer_state: dict[str, Any],
        ) -> dict[str, Any]:
            event_settings = next(
                item
                for item in ga4_tags[tag_name]["parameter"]
                if item.get("key") == "eventSettingsTable"
            )
            result: dict[str, Any] = {}
            for row in event_settings["list"]:
                row_values = {
                    item["key"]: item["value"]
                    for item in row["map"]
                }
                result[row_values["parameter"]] = data_layer_state[
                    field_by_variable[row_values["parameterValue"]]
                ]
            return result

        state: dict[str, Any] = {
            "app_id": "trace-learn",
            "page_environment": "production",
            "route_surface": "billing_confirmation",
            "page_location": "https://tracelearn.app/billing/success",
        }
        state.update(
            {
                "event": "purchase",
                "commercial_pathway": "core",
                "conversion_name": "trace_learn_core_purchase",
                "transaction_id": "checkout-123",
                "conversion_value": 29,
                "currency": "GBP",
            }
        )
        purchase_parameters = resolved_parameters(
            "APP - GA4 event - purchase",
            state,
        )
        self.assertEqual(purchase_parameters["transaction_id"], "checkout-123")
        self.assertEqual(purchase_parameters["value"], 29)
        self.assertEqual(purchase_parameters["currency"], "GBP")

        state.update(
            {
                "event": "marketing_cta_click",
                "route_surface": "marketing",
                "page_location": "https://tracelearn.app/pricing",
                "cta_id": "pricing_core",
                "cta_location": "pricing",
                "commercial_pathway": "core",
                "destination_path": "/sign-up",
            }
        )
        cta_parameters = resolved_parameters(
            "APP - GA4 event - marketing-interaction",
            state,
        )
        self.assertEqual(cta_parameters["cta_id"], "pricing_core")
        self.assertNotIn("transaction_id", cta_parameters)
        self.assertNotIn("value", cta_parameters)
        self.assertNotIn("currency", cta_parameters)

        state.update(
            {
                "event": "page_view",
                "page_location": "https://tracelearn.app/features",
            }
        )
        page_view_parameters = resolved_parameters(
            "APP - GA4 event - page-view",
            state,
        )
        self.assertEqual(
            page_view_parameters,
            {
                "app_id": "trace-learn",
                "page_environment": "production",
                "route_surface": "marketing",
                "page_location": "https://tracelearn.app/features",
            },
        )
        for stale_parameter in (
            "cta_id",
            "cta_location",
            "commercial_pathway",
            "destination_path",
            "conversion_name",
            "transaction_id",
            "value",
            "currency",
        ):
            self.assertNotIn(stale_parameter, page_view_parameters)

    def test_scoped_ga4_resources_can_retire_the_legacy_generic_pair(self) -> None:
        value = scoped_event_manifest()
        value["superseded"]["deleteTags"] = [
            "APP - GA4 event - Allowed marketing event"
        ]
        value["superseded"]["deleteTriggers"] = [
            "APP - Event - Allowed marketing events"
        ]
        gtmctl.validate_manifest(value)

        protected_resources = (
            ("deleteTags", "APP - GA4 event - page-view"),
            ("deleteTriggers", "APP - Event - GA4 - page-view"),
        )
        for action, name in protected_resources:
            with self.subTest(action=action, name=name):
                protected = scoped_event_manifest()
                protected["superseded"][action] = [name]
                with self.assertRaisesRegex(
                    gtmctl.GtmCtlError,
                    f"superseded.{action} overlaps managed resources",
                ):
                    gtmctl.validate_manifest(protected)

    def test_google_ads_conversion_uses_exact_bootstrapped_shape(self) -> None:
        resources, tags = self._desired_provider_resources()
        conversion = next(tag for tag in tags if tag["type"] == "awct")
        purchase_trigger_name = dict(resources.exact_event_trigger_names)[
            "provider-purchase-production"
        ]
        trigger_id = next(
            f"trigger-{index}"
            for index, trigger in enumerate(resources.triggers, start=1)
            if trigger["name"] == purchase_trigger_name
        )

        self.assertEqual(
            conversion,
            {
                "name": "APP - Google Ads - Conversion - purchase-production",
                "type": "awct",
                "paused": False,
                "parameter": [
                    {
                        "type": "boolean",
                        "key": "enableNewCustomerReporting",
                        "value": "false",
                    },
                    {
                        "type": "boolean",
                        "key": "enableConversionLinker",
                        "value": "true",
                    },
                    {
                        "type": "template",
                        "key": "orderId",
                        "value": "{{APP - DLV - transaction_id}}",
                    },
                    {
                        "type": "boolean",
                        "key": "enableProductReporting",
                        "value": "false",
                    },
                    {
                        "type": "template",
                        "key": "conversionValue",
                        "value": "{{APP - DLV - conversion_value}}",
                    },
                    {
                        "type": "boolean",
                        "key": "enableShippingData",
                        "value": "false",
                    },
                    {
                        "type": "template",
                        "key": "conversionId",
                        "value": "configured-conversion-id",
                    },
                    {
                        "type": "template",
                        "key": "currencyCode",
                        "value": "{{APP - DLV - currency}}",
                    },
                    {
                        "type": "template",
                        "key": "conversionLabel",
                        "value": "configured-conversion-label",
                    },
                    {"type": "boolean", "key": "rdp", "value": "false"},
                ],
                "firingTriggerId": [trigger_id],
                "consentSettings": {
                    "consentStatus": "needed",
                    "consentType": {
                        "type": "list",
                        "list": [
                            {"type": "template", "value": "ad_storage"},
                            {"type": "template", "value": "ad_user_data"},
                        ],
                    },
                },
                "notes": "Managed by gtmctl",
            },
        )

    def test_google_ads_supports_multiple_conversion_actions(self) -> None:
        value = provider_manifest()
        second = copy.deepcopy(
            value["managed"]["googleAds"]["conversionActions"][0]
        )
        second.update(
            {
                "name": "renewal-production",
                "conversionId": "second-conversion-id",
                "conversionLabel": "second-conversion-label",
            }
        )
        value["managed"]["googleAds"]["conversionActions"].append(second)

        gtmctl.validate_manifest(value)
        _, tags = self._desired_provider_resources(value)
        conversions = [tag for tag in tags if tag["type"] == "awct"]

        self.assertEqual(len(conversions), 2)
        self.assertEqual(
            {tag["name"] for tag in conversions},
            {
                "APP - Google Ads - Conversion - purchase-production",
                "APP - Google Ads - Conversion - renewal-production",
            },
        )

    def test_google_ads_conversion_validation_rejects_bad_references(self) -> None:
        invalid_mutations = (
            (
                lambda action: action.update({"triggerName": "unknown-trigger"}),
                "triggerName must reference managed.exactEventTriggers",
            ),
            (
                lambda action: action.update({"conversionValueField": "missing_field"}),
                "must also be in managed.dataLayerFields",
            ),
            (
                lambda action: action.update({"consentTypes": []}),
                "consentTypes must contain non-empty strings",
            ),
            (
                lambda action: action.update({"unexpected": True}),
                "contains unknown fields",
            ),
        )
        for mutate, expected in invalid_mutations:
            with self.subTest(expected=expected):
                value = provider_manifest()
                mutate(value["managed"]["googleAds"]["conversionActions"][0])
                with self.assertRaisesRegex(gtmctl.GtmCtlError, expected):
                    gtmctl.validate_manifest(value)

    def test_microsoft_uet_base_uses_exact_bootstrapped_shape_and_default_queue(self) -> None:
        resources, tags = self._desired_provider_resources()
        base = next(
            tag
            for tag in tags
            if tag["name"] == "APP - Microsoft Ads - UET base - production"
        )
        boot_trigger_name = dict(resources.exact_event_trigger_names)[
            "provider-boot-production"
        ]
        trigger_id = next(
            f"trigger-{index}"
            for index, trigger in enumerate(resources.triggers, start=1)
            if trigger["name"] == boot_trigger_name
        )

        self.assertEqual(base["type"], "baut")
        self.assertEqual(base["firingTriggerId"], [trigger_id])
        self.assertEqual(
            base["parameter"],
            [
                {"type": "boolean", "key": "c_navTimingApi", "value": "false"},
                {
                    "type": "template",
                    "key": "tagId",
                    "value": "configured-uet-tag-id",
                },
                {
                    "type": "boolean",
                    "key": "c_consentInheritGtm",
                    "value": "true",
                },
                {
                    "type": "boolean",
                    "key": "c_storeConvTrackCookies",
                    "value": "true",
                },
                {"type": "template", "key": "uetqName", "value": "uetq"},
                {
                    "type": "boolean",
                    "key": "c_removeQueryFromUrls",
                    "value": "true",
                },
                {
                    "type": "boolean",
                    "key": "c_disableAutoPageView",
                    "value": "false",
                },
                {
                    "type": "boolean",
                    "key": "c_enhancedConversion",
                    "value": "false",
                },
                {
                    "type": "boolean",
                    "key": "c_consentUpdates",
                    "value": "true",
                },
                {"type": "template", "key": "eventType", "value": "PAGE_LOAD"},
                {
                    "type": "boolean",
                    "key": "c_enableAutoSpaTracking",
                    "value": "true",
                },
            ],
        )
        self.assertEqual(
            base["consentSettings"]["consentType"]["list"],
            [{"type": "template", "value": "ad_storage"}],
        )

    def test_microsoft_uet_base_defaults_spa_tracking_off(self) -> None:
        value = provider_manifest()
        del value["managed"]["microsoftAds"]["baseTags"][0][
            "enableAutoSpaTracking"
        ]
        resources = gtmctl.desired_resources(value)
        tags = gtmctl.desired_tags(
            value,
            trigger_ids={
                trigger["name"]: f"trigger-{index}"
                for index, trigger in enumerate(resources.triggers, start=1)
            },
        )
        base = next(tag for tag in tags if tag["type"] == "baut")
        spa_setting = next(
            parameter
            for parameter in base["parameter"]
            if parameter.get("key") == "c_enableAutoSpaTracking"
        )
        self.assertEqual(spa_setting["value"], "false")

    def test_microsoft_uet_base_requires_boolean_spa_tracking(self) -> None:
        value = provider_manifest()
        value["managed"]["microsoftAds"]["baseTags"][0][
            "enableAutoSpaTracking"
        ] = "true"
        with self.assertRaisesRegex(
            gtmctl.GtmCtlError,
            "enableAutoSpaTracking must be a boolean",
        ):
            gtmctl.validate_manifest(value)

    def test_microsoft_uet_purchase_uses_exact_bootstrapped_shape(self) -> None:
        _, tags = self._desired_provider_resources()
        purchase = next(
            tag
            for tag in tags
            if tag["name"]
            == "APP - Microsoft Ads - UET conversion - purchase-production"
        )

        self.assertEqual(purchase["type"], "baut")
        self.assertEqual(
            purchase["parameter"],
            [
                {
                    "type": "template",
                    "key": "goalValue",
                    "value": "{{APP - DLV - conversion_value}}",
                },
                {
                    "type": "template",
                    "key": "p_currency",
                    "value": "{{APP - DLV - currency}}",
                },
                {
                    "type": "template",
                    "key": "eventCategory",
                    "value": "{{APP - DLV - conversion_name}}",
                },
                {"type": "template", "key": "uetqName", "value": "uetq"},
                {
                    "type": "template",
                    "key": "customEventAction",
                    "value": "purchase",
                },
                {"type": "template", "key": "eventType", "value": "CUSTOM"},
                {
                    "type": "list",
                    "key": "customParamTable",
                    "list": [
                        {
                            "type": "map",
                            "map": [
                                {
                                    "type": "template",
                                    "key": "customParamName",
                                    "value": "transaction_id",
                                },
                                {
                                    "type": "template",
                                    "key": "customParamValue",
                                    "value": "{{APP - DLV - transaction_id}}",
                                },
                            ],
                        }
                    ],
                },
                {
                    "type": "template",
                    "key": "eventLabel",
                    "value": "{{APP - DLV - event_label}}",
                },
            ],
        )

    def test_microsoft_uet_queue_is_configurable_and_must_match_base(self) -> None:
        value = provider_manifest()
        base = value["managed"]["microsoftAds"]["baseTags"][0]
        conversion = value["managed"]["microsoftAds"]["conversionActions"][0]
        base["uetqName"] = "project_uetq"
        conversion["uetqName"] = "project_uetq"

        gtmctl.validate_manifest(value)
        _, tags = self._desired_provider_resources(value)
        microsoft_tags = [tag for tag in tags if tag["type"] == "baut"]
        for tag in microsoft_tags:
            queue = next(
                parameter
                for parameter in tag["parameter"]
                if parameter.get("key") == "uetqName"
            )
            self.assertEqual(queue["value"], "project_uetq")

        conversion["uetqName"] = "unmanaged_queue"
        with self.assertRaisesRegex(
            gtmctl.GtmCtlError,
            "uetqName must match a managed Microsoft Ads base tag",
        ):
            gtmctl.validate_manifest(value)

    def test_microsoft_ads_supports_multiple_conversion_actions(self) -> None:
        value = provider_manifest()
        second = copy.deepcopy(
            value["managed"]["microsoftAds"]["conversionActions"][0]
        )
        second["name"] = "renewal-production"
        value["managed"]["microsoftAds"]["conversionActions"].append(second)

        gtmctl.validate_manifest(value)
        _, tags = self._desired_provider_resources(value)
        conversions = [
            tag
            for tag in tags
            if " - Microsoft Ads - UET conversion - " in tag["name"]
        ]

        self.assertEqual(len(conversions), 2)
        self.assertEqual(
            {tag["name"] for tag in conversions},
            {
                "APP - Microsoft Ads - UET conversion - purchase-production",
                "APP - Microsoft Ads - UET conversion - renewal-production",
            },
        )

    def test_provider_resources_are_protected_from_superseded_actions(self) -> None:
        protected = (
            (
                "pauseTags",
                "APP - Google Ads - Conversion - purchase-production",
            ),
            (
                "deleteTags",
                "APP - Microsoft Ads - UET base - production",
            ),
            (
                "deleteTags",
                "APP - Microsoft Ads - UET conversion - purchase-production",
            ),
            (
                "deleteTriggers",
                "APP - Exact event - provider-purchase-production",
            ),
        )
        for action, name in protected:
            with self.subTest(action=action, name=name):
                value = provider_manifest()
                value["superseded"][action] = [name]
                with self.assertRaisesRegex(
                    gtmctl.GtmCtlError,
                    f"superseded.{action} overlaps managed resources",
                ):
                    gtmctl.validate_manifest(value)

    def test_provider_sections_remain_optional(self) -> None:
        value = manifest()
        del value["managed"]["googleAds"]

        gtmctl.validate_manifest(value)
        resources = gtmctl.desired_resources(value)
        tags = gtmctl.desired_tags(
            value,
            trigger_ids={
                resources.boot_trigger_name: "boot-trigger",
                resources.event_trigger_name: "event-trigger",
            },
        )

        self.assertEqual({tag["type"] for tag in tags}, {"googtag", "gaawe"})

    def test_provider_arrays_reject_explicit_empty_values(self) -> None:
        mutations = (
            (
                lambda value: value["managed"].update({"exactEventTriggers": []}),
                "exactEventTriggers must not be empty",
            ),
            (
                lambda value: value["managed"]["googleAds"].update(
                    {"conversionActions": []}
                ),
                "googleAds.conversionActions must not be empty",
            ),
            (
                lambda value: value["managed"]["microsoftAds"].update(
                    {"baseTags": []}
                ),
                "microsoftAds.baseTags must not be empty",
            ),
            (
                lambda value: value["managed"]["microsoftAds"].update(
                    {"conversionActions": []}
                ),
                "microsoftAds.conversionActions must not be empty",
            ),
        )
        for mutate, expected in mutations:
            with self.subTest(expected=expected):
                value = provider_manifest()
                mutate(value)
                with self.assertRaisesRegex(gtmctl.GtmCtlError, expected):
                    gtmctl.validate_manifest(value)

    def test_example_and_schema_include_optional_conversion_linker_contract(self) -> None:
        example = json.loads(
            (MODULE_DIR / "example.manifest.json").read_text(encoding="utf-8")
        )
        schema = json.loads(
            (MODULE_DIR / "manifest.schema.json").read_text(encoding="utf-8")
        )

        gtmctl.validate_manifest(example)
        google_ads_schema = schema["properties"]["managed"]["properties"]["googleAds"]
        self.assertFalse(google_ads_schema["additionalProperties"])
        self.assertEqual(
            set(google_ads_schema["required"]),
            {"enableConversionLinker", "consentTypes"},
        )
        self.assertIn(
            "conversionLinkerTriggerName",
            google_ads_schema["properties"],
        )
        self.assertNotIn(
            "conversionLinkerTriggerName",
            google_ads_schema["required"],
        )
        advertising_trigger_name = example["managed"]["googleAds"][
            "conversionLinkerTriggerName"
        ]
        self.assertEqual(
            advertising_trigger_name,
            example["managed"]["microsoftAds"]["baseTags"][0]["triggerName"],
        )
        advertising_trigger = next(
            trigger
            for trigger in example["managed"]["exactEventTriggers"]
            if trigger["name"] == advertising_trigger_name
        )
        self.assertEqual(
            advertising_trigger["event"],
            "${GTM_ADVERTISING_BOOT_EVENT}",
        )
        self.assertNotIn(
            "googleAds",
            schema["properties"]["managed"]["required"],
        )

    def test_example_and_schema_include_event_parameter_allowlists(self) -> None:
        example = json.loads(
            (MODULE_DIR / "example.manifest.json").read_text(encoding="utf-8")
        )
        schema = json.loads(
            (MODULE_DIR / "manifest.schema.json").read_text(encoding="utf-8")
        )

        gtmctl.validate_manifest(example)
        managed_schema = schema["properties"]["managed"]
        allowlist_schema = managed_schema["properties"][
            "eventParameterAllowlists"
        ]
        self.assertNotIn("eventParameterAllowlists", managed_schema["required"])
        self.assertFalse(allowlist_schema["items"]["additionalProperties"])
        self.assertEqual(
            set(allowlist_schema["items"]["required"]),
            {"name", "events", "parameters"},
        )
        page_view = next(
            item
            for item in example["managed"]["eventParameterAllowlists"]
            if item["name"] == "page-view"
        )
        purchase = next(
            item
            for item in example["managed"]["eventParameterAllowlists"]
            if item["name"] == "purchase"
        )
        self.assertNotIn("cta_id", page_view["parameters"])
        self.assertNotIn("transaction_id", page_view["parameters"])
        self.assertIn("transaction_id", purchase["parameters"])
        self.assertIn("value", purchase["parameters"])
        self.assertIn("currency", purchase["parameters"])

    def test_example_and_schema_include_optional_provider_contracts(self) -> None:
        example = json.loads(
            (MODULE_DIR / "example.manifest.json").read_text(encoding="utf-8")
        )
        schema = json.loads(
            (MODULE_DIR / "manifest.schema.json").read_text(encoding="utf-8")
        )

        gtmctl.validate_manifest(example)
        managed_schema = schema["properties"]["managed"]
        exact_trigger_schema = managed_schema["properties"]["exactEventTriggers"]
        google_ads_schema = managed_schema["properties"]["googleAds"]
        microsoft_ads_schema = managed_schema["properties"]["microsoftAds"]

        self.assertNotIn("exactEventTriggers", managed_schema["required"])
        self.assertNotIn("microsoftAds", managed_schema["required"])
        self.assertFalse(exact_trigger_schema["items"]["additionalProperties"])
        self.assertFalse(
            google_ads_schema["properties"]["conversionActions"]["items"][
                "additionalProperties"
            ]
        )
        self.assertFalse(microsoft_ads_schema["additionalProperties"])
        self.assertEqual(
            microsoft_ads_schema["properties"]["baseTags"]["items"][
                "properties"
            ]["uetqName"]["default"],
            "uetq",
        )
        example_text = json.dumps(example)
        self.assertIn("${GOOGLE_ADS_PURCHASE_CONVERSION_ID}", example_text)
        self.assertIn("${MICROSOFT_ADS_UET_TAG_ID}", example_text)
        self.assertNotIn("configured-conversion-id", example_text)

    def test_requires_page_location_field_in_data_layer_fields(self) -> None:
        value = manifest()
        value["managed"]["dataLayerFields"].remove("page_location")
        with self.assertRaisesRegex(
            gtmctl.GtmCtlError,
            "managed.dataLayerFields must include 'page_location'",
        ):
            gtmctl.validate_manifest(value)

    def test_rejects_unknown_fields_at_every_manifest_object_boundary(self) -> None:
        mutations = (
            (lambda value: value.update({"microsoftAdss": {}}), "manifest"),
            (lambda value: value["target"].update({"accountId": "1"}), "target"),
            (lambda value: value["oauth"].update({"token": "secret"}), "oauth"),
            (lambda value: value["workspace"].update({"publish": False}), "workspace"),
            (lambda value: value["managed"].update({"microsoftAdss": {}}), "managed"),
            (
                lambda value: value["managed"]["measurementIdMappings"][0].update(
                    {"streamId": "1"}
                ),
                "managed.measurementIdMappings[0]",
            ),
            (
                lambda value: value["managed"]["eventParameters"][0].update(
                    {"defaultValue": "x"}
                ),
                "managed.eventParameters[0]",
            ),
            (
                lambda value: value["superseded"].update({"deleteFolders": []}),
                "superseded",
            ),
        )
        for mutate, location in mutations:
            with self.subTest(location=location):
                value = manifest()
                mutate(value)
                with self.assertRaisesRegex(
                    gtmctl.GtmCtlError,
                    rf"{re.escape(location)} contains unknown fields",
                ):
                    gtmctl.validate_manifest(value)

    def test_requires_integer_schema_version(self) -> None:
        for version in (True, 1.0, "1"):
            with self.subTest(version=version):
                value = manifest()
                value["schemaVersion"] = version
                with self.assertRaisesRegex(
                    gtmctl.GtmCtlError,
                    "schemaVersion must be the integer 1",
                ):
                    gtmctl.validate_manifest(value)

    def test_requires_anchored_allowed_event_pattern(self) -> None:
        for pattern in ("purchase", "^purchase", "purchase$"):
            with self.subTest(pattern=pattern):
                value = manifest()
                value["managed"]["allowedEventPattern"] = pattern
                with self.assertRaisesRegex(
                    gtmctl.GtmCtlError,
                    r"allowedEventPattern must be anchored with \^ and \$",
                ):
                    gtmctl.validate_manifest(value)

    def test_rejects_top_level_allowed_event_alternation(self) -> None:
        value = manifest()
        value["managed"]["allowedEventPattern"] = "^purchase|page_view$"
        with self.assertRaisesRegex(
            gtmctl.GtmCtlError,
            r"must group alternations so \^ and \$ apply to every branch",
        ):
            gtmctl.validate_manifest(value)

        for pattern in (
            "^(purchase|page_view)$",
            "^(?:purchase|page_view)$",
            r"^purchase\|page_view$",
            r"^[a-z|_]+$",
        ):
            with self.subTest(pattern=pattern):
                grouped = manifest()
                grouped["managed"]["allowedEventPattern"] = pattern
                gtmctl.validate_manifest(grouped)

    def test_rejects_duplicate_runtime_lists_declared_unique_by_schema(self) -> None:
        value = manifest()
        value["managed"]["ensureBuiltInVariables"] = ["event", "event"]
        with self.assertRaisesRegex(gtmctl.GtmCtlError, "must not contain duplicates"):
            gtmctl.validate_manifest(value)

        value = manifest()
        value["superseded"]["deleteTags"] = ["Legacy tag", "Legacy tag"]
        with self.assertRaisesRegex(gtmctl.GtmCtlError, "must not contain duplicates"):
            gtmctl.validate_manifest(value)

    def test_rejects_tag_listed_for_both_pause_and_delete(self) -> None:
        value = manifest()
        value["superseded"]["pauseTags"] = ["Legacy tag"]
        value["superseded"]["deleteTags"] = ["Legacy tag"]

        with self.assertRaisesRegex(
            gtmctl.GtmCtlError,
            r"superseded\.pauseTags and superseded\.deleteTags overlap: Legacy tag",
        ):
            gtmctl.validate_manifest(value)

    def test_requires_non_empty_unique_surface_values(self) -> None:
        for surface_values, expected in (
            ([], "must contain non-empty strings"),
            (["marketing", ""], "must contain non-empty strings"),
            (["marketing", "marketing"], "must not contain duplicates"),
        ):
            with self.subTest(surface_values=surface_values):
                value = manifest()
                value["managed"]["surfaceValues"] = surface_values
                with self.assertRaisesRegex(gtmctl.GtmCtlError, expected):
                    gtmctl.validate_manifest(value)

    def test_surface_filter_exactly_allows_each_configured_surface(self) -> None:
        resources = gtmctl.desired_resources(manifest())
        for trigger in resources.triggers:
            surface_filter = trigger["filter"][0]
            self.assertEqual(surface_filter["type"], "matchRegex")
            pattern = next(
                parameter["value"]
                for parameter in surface_filter["parameter"]
                if parameter["key"] == "arg1"
            )
            self.assertEqual(pattern, r"^(?:marketing|billing_confirmation)$")
            self.assertIsNotNone(re.fullmatch(pattern, "marketing"))
            self.assertIsNotNone(re.fullmatch(pattern, "billing_confirmation"))
            self.assertIsNone(re.fullmatch(pattern, "learner"))
            self.assertIsNone(re.fullmatch(pattern, "marketing-preview"))

    def test_google_tag_uses_configured_page_location_field(self) -> None:
        value = manifest()
        value["managed"]["pageLocationField"] = "sanitized_location"
        page_location_index = value["managed"]["dataLayerFields"].index(
            "page_location"
        )
        value["managed"]["dataLayerFields"][page_location_index] = (
            "sanitized_location"
        )
        resources = gtmctl.desired_resources(value)
        tags = gtmctl.desired_tags(
            value,
            trigger_ids={
                resources.boot_trigger_name: "boot-trigger",
                resources.event_trigger_name: "event-trigger",
            },
        )
        google_tag = next(tag for tag in tags if tag["type"] == "googtag")
        config_table = next(
            parameter
            for parameter in google_tag["parameter"]
            if parameter.get("key") == "configSettingsTable"
        )
        config = {
            next(entry["value"] for entry in row["map"] if entry["key"] == "parameter"):
            next(
                entry["value"]
                for entry in row["map"]
                if entry["key"] == "parameterValue"
            )
            for row in config_table["list"]
        }
        self.assertEqual(
            config["page_location"],
            "{{APP - DLV - sanitized_location}}",
        )

    def test_install_helper_creates_path_executable_without_copying_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            install_dir = Path(temp_dir) / "bin"
            env = dict(os.environ)
            env["GTMCTL_INSTALL_DIR"] = str(install_dir)
            completed = subprocess.run(
                [str(MODULE_DIR / "install.sh")],
                check=False,
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            installed = install_dir / "gtmctl"
            self.assertTrue(installed.is_symlink())
            self.assertEqual(installed.resolve(), (MODULE_DIR / "gtmctl").resolve())
            self.assertTrue(os.access(installed, os.X_OK))
            help_result = subprocess.run(
                [str(installed), "--help"],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(help_result.returncode, 0, help_result.stderr)
            self.assertIn("doctor", help_result.stdout)


class ControlPlaneHttpTests(unittest.TestCase):
    def test_retries_429_and_transient_5xx_with_a_bounded_attempt_count(self) -> None:
        with RunningMockServer() as mock:
            accounts_path = "/tagmanager/v2/accounts"
            mock.state.transient_failures[("GET", accounts_path)] = [429, 503]
            sleeps: list[float] = []
            http = gtmctl.JsonHttp(
                max_attempts=3,
                initial_backoff_seconds=0,
                max_backoff_seconds=0,
                sleep=sleeps.append,
            )
            control = gtmctl.GtmControlPlane(manifest(), mock.client(http=http))

            result = control.inventory()

            self.assertEqual(result["account"]["name"], "Example Account")
            self.assertEqual(
                result["liveVersion"],
                {
                    "present": True,
                    "id": "4",
                    "name": "Published baseline",
                    "counts": {"variables": 0, "triggers": 1, "tags": 1},
                },
            )
            account_requests = [
                item for item in mock.state.requests if item["path"] == accounts_path
            ]
            self.assertEqual(len(account_requests), 3)
            self.assertEqual(sleeps, [0.0, 0.0])

            mock.state.transient_failures[("GET", accounts_path)] = [503, 503, 503, 503]
            bounded_http = gtmctl.JsonHttp(
                max_attempts=3,
                initial_backoff_seconds=0,
                max_backoff_seconds=0,
                sleep=lambda _: None,
            )
            bounded_control = gtmctl.GtmControlPlane(
                manifest(),
                mock.client(http=bounded_http),
            )
            before = len(
                [item for item in mock.state.requests if item["path"] == accounts_path]
            )
            with self.assertRaisesRegex(gtmctl.GtmCtlError, "after 3 attempts"):
                bounded_control.inventory()
            after = len(
                [item for item in mock.state.requests if item["path"] == accounts_path]
            )
            self.assertEqual(after - before, 3)

    def test_default_retry_window_covers_a_short_quota_reset(self) -> None:
        http = gtmctl.JsonHttp(sleep=lambda _: None)
        self.assertEqual(http.max_attempts, 6)
        self.assertEqual(
            [http._backoff_seconds(attempt, None) for attempt in range(1, 6)],
            [1.0, 2.0, 4.0, 8.0, 16.0],
        )

    def test_mutating_requests_are_not_retried_automatically(self) -> None:
        with RunningMockServer() as mock:
            workspace_path = "/tagmanager/v2/accounts/1/containers/2/workspaces"
            mock.state.transient_failures[("POST", workspace_path)] = [503]
            control = gtmctl.GtmControlPlane(manifest(), mock.client())

            with self.assertRaisesRegex(gtmctl.GtmCtlError, "HTTP 503 for POST"):
                control.apply("review-workspace")

            workspace_posts = [
                item
                for item in mock.state.requests
                if item["method"] == "POST" and item["path"] == workspace_path
            ]
            self.assertEqual(len(workspace_posts), 1)
            self.assertEqual(set(mock.state.workspaces), {"3"})

    def test_oauth_token_exchange_remains_retryable(self) -> None:
        with RunningMockServer() as mock:
            mock.state.transient_failures[("POST", "/token")] = [503]
            control = gtmctl.GtmControlPlane(manifest(), mock.client())

            result = control.inventory()

            self.assertEqual(result["account"]["name"], "Example Account")
            token_posts = [
                item
                for item in mock.state.requests
                if item["method"] == "POST" and item["path"] == "/token"
            ]
            self.assertEqual(len(token_posts), 2)

    def test_plan_is_read_only_against_gtm_api(self) -> None:
        with RunningMockServer() as mock:
            control = gtmctl.GtmControlPlane(manifest(), mock.client())
            result = control.plan()
            self.assertEqual(result["workspace"]["name"], "Default Workspace")
            gtm_requests = [
                item for item in mock.state.requests if item["path"].startswith("/tagmanager/")
            ]
            self.assertTrue(gtm_requests)
            self.assertEqual({item["method"] for item in gtm_requests}, {"GET"})

    def test_apply_and_quick_preview_validate_without_publish(self) -> None:
        with RunningMockServer() as mock:
            control = gtmctl.GtmControlPlane(manifest(), mock.client())
            applied = control.apply("review-workspace")
            self.assertFalse(applied["publishAttempted"])
            self.assertNotIn("published", applied)
            self.assertEqual(applied["workspace"]["name"], "review-workspace")
            self.assertEqual(applied["builtInVariablesCreated"], ["event"])

            workspace = mock.state.workspaces["9"]
            listed = next(item for item in workspace["tags"] if item["name"] == "Legacy listed tag")
            unlisted = next(item for item in workspace["tags"] if item["name"] == "Legacy unlisted tag")
            self.assertTrue(listed["paused"])
            self.assertFalse(unlisted["paused"])

            event_tag = next(
                item for item in workspace["tags"] if item["name"] == "APP - GA4 event - Allowed marketing event"
            )
            self.assertEqual(
                [item["key"] for item in event_tag["parameter"]],
                [
                    "sendEcommerceData",
                    "eventSettingsTable",
                    "eventName",
                    "measurementIdOverride",
                ],
            )
            measurement_parameter = next(
                item for item in event_tag["parameter"] if item.get("key") == "measurementIdOverride"
            )
            self.assertEqual(
                measurement_parameter["value"],
                "{{APP - Lookup - GA4 Measurement ID}}",
            )
            self.assertNotIn("measurement-production", json.dumps(event_tag))
            send_ecommerce = next(
                item for item in event_tag["parameter"] if item.get("key") == "sendEcommerceData"
            )
            self.assertEqual(send_ecommerce, {"type": "boolean", "key": "sendEcommerceData", "value": "false"})
            event_settings = next(
                item for item in event_tag["parameter"] if item.get("key") == "eventSettingsTable"
            )
            self.assertTrue(event_settings["list"])
            for row in event_settings["list"]:
                self.assertEqual(
                    {entry["key"] for entry in row["map"]},
                    {"parameter", "parameterValue"},
                )

            google_tag = next(
                item for item in workspace["tags"] if item["name"] == "APP - Google tag - Public marketing"
            )
            self.assertFalse(google_tag["paused"])
            self.assertFalse(event_tag["paused"])
            config_table = next(
                item for item in google_tag["parameter"] if item.get("key") == "configSettingsTable"
            )
            config = {
                next(entry["value"] for entry in row["map"] if entry["key"] == "parameter"):
                next(entry["value"] for entry in row["map"] if entry["key"] == "parameterValue")
                for row in config_table["list"]
            }
            self.assertEqual(
                config,
                {
                    "send_page_view": "true",
                    "page_location": "{{APP - DLV - page_location}}",
                    "allow_google_signals": "false",
                    "allow_ad_personalization_signals": "false",
                },
            )

            conversion_linker = next(
                item
                for item in workspace["tags"]
                if item["name"] == "APP - Google Ads - Conversion Linker"
            )
            self.assertEqual(conversion_linker["type"], "gclidw")
            self.assertFalse(conversion_linker["paused"])
            self.assertEqual(
                conversion_linker["parameter"],
                [
                    {
                        "type": "boolean",
                        "key": "enableCookieOverrides",
                        "value": "false",
                    }
                ],
            )
            boot_trigger = next(
                item
                for item in workspace["triggers"]
                if item["name"] == "APP - Event - Marketing boot"
            )
            self.assertEqual(
                conversion_linker["firingTriggerId"],
                [boot_trigger["triggerId"]],
            )
            self.assertEqual(
                conversion_linker["consentSettings"]["consentType"]["list"],
                [{"type": "template", "value": "ad_storage"}],
            )

            page_location_variable = next(
                item
                for item in workspace["variables"]
                if item["name"] == "APP - DLV - page_location"
            )
            self.assertEqual(
                next(
                    parameter["value"]
                    for parameter in page_location_variable["parameter"]
                    if parameter["key"] == "name"
                ),
                "page_location",
            )

            allowed_trigger = next(
                item
                for item in workspace["triggers"]
                if item["name"] == "APP - Event - Allowed marketing events"
            )
            self.assertEqual(allowed_trigger["customEventFilter"][0]["type"], "matchRegex")

            data_layer_variable = next(
                item
                for item in workspace["variables"]
                if item["name"] == "APP - DLV - app_id"
            )
            self.assertEqual(
                [item["key"] for item in data_layer_variable["parameter"]],
                ["dataLayerVersion", "setDefaultValue", "name"],
            )

            validated = control.validate("review-workspace")
            self.assertTrue(validated["valid"])
            self.assertFalse(validated["publishAttempted"])
            self.assertNotIn("published", validated)
            self.assertEqual(validated["liveVersion"]["id"], "4")
            self.assertFalse(validated["compilerError"])

            mutating_requests = [
                item
                for item in mock.state.requests
                if item["method"] in ("POST", "PUT", "DELETE")
            ]
            for request in mutating_requests:
                path = request["path"].lower()
                self.assertNotIn("publish", path)
                self.assertNotIn("/versions", path)
                self.assertNotIn("create_version", path)
            request_paths = [item["path"].lower() for item in mock.state.requests]
            self.assertTrue(any(path.endswith(":quick_preview") for path in request_paths))

    def test_apply_reconciles_provider_tags_without_publish(self) -> None:
        with RunningMockServer() as mock:
            control = gtmctl.GtmControlPlane(provider_manifest(), mock.client())

            applied = control.apply("provider-review-workspace")

            workspace = mock.state.workspaces["9"]
            self.assertFalse(applied["publishAttempted"])
            self.assertNotIn("published", applied)
            self.assertEqual(
                {tag["type"] for tag in workspace["tags"]},
                {"html", "googtag", "gaawe", "gclidw", "awct", "baut"},
            )
            self.assertEqual(
                len([tag for tag in workspace["tags"] if tag["type"] == "baut"]),
                2,
            )
            exact_triggers = [
                trigger
                for trigger in workspace["triggers"]
                if " - Exact event - " in trigger["name"]
            ]
            self.assertEqual(len(exact_triggers), 2)
            trigger_ids = {
                trigger["name"]: trigger["triggerId"]
                for trigger in exact_triggers
            }
            google_conversion = next(
                tag for tag in workspace["tags"] if tag["type"] == "awct"
            )
            microsoft_conversion = next(
                tag
                for tag in workspace["tags"]
                if tag["name"]
                == "APP - Microsoft Ads - UET conversion - purchase-production"
            )
            expected_purchase_trigger = trigger_ids[
                "APP - Exact event - provider-purchase-production"
            ]
            self.assertEqual(
                google_conversion["firingTriggerId"],
                [expected_purchase_trigger],
            )
            self.assertEqual(
                microsoft_conversion["firingTriggerId"],
                [expected_purchase_trigger],
            )

            validated = control.validate("provider-review-workspace")
            self.assertTrue(validated["valid"])
            self.assertFalse(validated["publishAttempted"])
            self.assertNotIn("published", validated)
            for request in mock.state.requests:
                if request["method"] == "GET":
                    continue
                path = request["path"].lower()
                self.assertNotIn("publish", path)
                self.assertNotIn("/versions", path)
                self.assertNotIn("create_version", path)

    def test_apply_updates_existing_conversion_linker_without_publish(self) -> None:
        with RunningMockServer() as mock:
            mock.state.workspaces["3"]["tags"].append(
                {
                    "name": "APP - Google Ads - Conversion Linker",
                    "type": "gclidw",
                    "paused": True,
                    "parameter": [
                        {
                            "type": "boolean",
                            "key": "enableCookieOverrides",
                            "value": "true",
                        }
                    ],
                    "firingTriggerId": ["old-trigger"],
                    "consentSettings": {
                        "consentStatus": "needed",
                        "consentType": {
                            "type": "list",
                            "list": [
                                {"type": "template", "value": "analytics_storage"}
                            ],
                        },
                    },
                    "notes": "Old configuration",
                    "tagId": "7",
                    "path": "accounts/1/containers/2/workspaces/3/tags/7",
                    "fingerprint": "f-7",
                }
            )
            control = gtmctl.GtmControlPlane(manifest(), mock.client())

            applied = control.apply("review-workspace")

            linker_action = next(
                item
                for item in applied["managed"]["tags"]
                if item["name"] == "APP - Google Ads - Conversion Linker"
            )
            self.assertEqual(linker_action["action"], "update")
            saved = next(
                item
                for item in mock.state.workspaces["9"]["tags"]
                if item["name"] == "APP - Google Ads - Conversion Linker"
            )
            self.assertFalse(saved["paused"])
            self.assertEqual(saved["notes"], "Managed by gtmctl")
            self.assertEqual(saved["parameter"][0]["value"], "false")
            self.assertEqual(
                saved["consentSettings"]["consentType"]["list"],
                [{"type": "template", "value": "ad_storage"}],
            )
            tag_puts = [
                request
                for request in mock.state.requests
                if request["method"] == "PUT"
                and "/tags/" in request["path"]
            ]
            self.assertEqual(len(tag_puts), 2)
            self.assertFalse(applied["publishAttempted"])
            self.assertNotIn("published", applied)
            for request in mock.state.requests:
                if request["method"] == "GET":
                    continue
                path = request["path"].lower()
                self.assertNotIn("publish", path)
                self.assertNotIn("/versions", path)
                self.assertNotIn("create_version", path)

    def test_apply_refuses_to_reuse_workspace(self) -> None:
        with RunningMockServer() as mock:
            control = gtmctl.GtmControlPlane(manifest(), mock.client())
            with self.assertRaisesRegex(gtmctl.GtmCtlError, "Default Workspace cannot"):
                control.apply("Default Workspace")
            workspace_posts = [
                item
                for item in mock.state.requests
                if item["method"] == "POST"
                and item["path"] == "/tagmanager/v2/accounts/1/containers/2/workspaces"
            ]
            self.assertEqual(workspace_posts, [])

    def test_resume_reconciles_a_partial_workspace_idempotently(self) -> None:
        with RunningMockServer() as mock:
            value = manifest()
            value["superseded"]["deleteTags"] = ["Legacy deleted tag"]
            value["superseded"]["deleteTriggers"] = ["Legacy deleted trigger"]
            value["superseded"]["deleteVariables"] = ["Legacy deleted variable"]
            base = mock.state.workspaces["3"]
            base["tags"].append(
                {
                    "name": "Legacy deleted tag",
                    "type": "html",
                    "paused": False,
                    "tagId": "4",
                    "path": "accounts/1/containers/2/workspaces/3/tags/4",
                    "fingerprint": "f-4",
                }
            )
            base["triggers"].append(
                {
                    "name": "Legacy deleted trigger",
                    "type": "customEvent",
                    "triggerId": "5",
                    "path": "accounts/1/containers/2/workspaces/3/triggers/5",
                    "fingerprint": "f-5",
                }
            )
            base["variables"].append(
                {
                    "name": "Legacy deleted variable",
                    "type": "v",
                    "variableId": "6",
                    "path": "accounts/1/containers/2/workspaces/3/variables/6",
                    "fingerprint": "f-6",
                }
            )
            control = gtmctl.GtmControlPlane(value, mock.client())
            target = control.target()
            workspace = control.client.create_workspace(
                target,
                name="review-workspace",
                description=value["workspace"]["description"],
            )
            workspace_path = workspace["path"]
            first_variable = gtmctl.desired_resources(value).variables[0]
            control.client.create_resource(workspace_path, "variables", first_variable)

            partial = mock.state.workspaces["9"]
            next(item for item in partial["tags"] if item["name"] == "Legacy listed tag")[
                "paused"
            ] = True
            partial["tags"] = [
                item for item in partial["tags"] if item["name"] != "Legacy deleted tag"
            ]

            resumed = control.resume("review-workspace")

            variable_actions = {
                item["name"]: item["action"] for item in resumed["managed"]["variables"]
            }
            self.assertEqual(variable_actions[first_variable["name"]], "noop")
            self.assertIn("create", variable_actions.values())
            superseded_actions = {
                item["name"]: item["action"] for item in resumed["superseded"]
            }
            self.assertEqual(superseded_actions["Legacy listed tag"], "noop")
            self.assertEqual(superseded_actions["Legacy deleted tag"], "missing")
            self.assertEqual(superseded_actions["Legacy deleted trigger"], "delete")
            self.assertEqual(superseded_actions["Legacy deleted variable"], "delete")
            self.assertFalse(resumed["publishAttempted"])
            self.assertNotIn("published", resumed)

            resumed_again = control.resume("review-workspace")
            for items in resumed_again["managed"].values():
                self.assertTrue(items)
                self.assertEqual({item["action"] for item in items}, {"noop"})
            self.assertEqual(
                {item["action"] for item in resumed_again["superseded"]},
                {"noop", "missing"},
            )
            for kind in ("variables", "triggers", "tags"):
                names = [item["name"] for item in partial[kind]]
                self.assertEqual(len(names), len(set(names)))

            for request in mock.state.requests:
                if request["method"] == "GET":
                    continue
                path = request["path"].lower()
                self.assertNotIn("publish", path)
                self.assertNotIn("/versions", path)
                self.assertNotIn("create_version", path)

    def test_resume_refuses_default_or_wrong_description_before_mutation(self) -> None:
        with RunningMockServer() as mock:
            control = gtmctl.GtmControlPlane(manifest(), mock.client())
            with self.assertRaisesRegex(gtmctl.GtmCtlError, "Default Workspace cannot"):
                control.resume("Default Workspace")
            self.assertEqual(mock.state.requests, [])

            target = control.target()
            control.client.create_workspace(
                target,
                name="wrong-description",
                description="Created by someone else",
            )
            before = len(mock.state.requests)
            with self.assertRaisesRegex(gtmctl.GtmCtlError, "description does not match"):
                control.resume("wrong-description")
            mutations = [
                item
                for item in mock.state.requests[before:]
                if item["method"] in {"POST", "PUT", "DELETE"}
            ]
            self.assertEqual(mutations, [])

    def test_validate_rejects_quick_preview_compiler_error(self) -> None:
        with RunningMockServer() as mock:
            control = gtmctl.GtmControlPlane(manifest(), mock.client())
            control.apply("review-workspace")
            mock.state.compiler_error = True
            with self.assertRaisesRegex(gtmctl.GtmCtlError, '"compilerError": true'):
                control.validate("review-workspace")

    def test_validate_rejects_incomplete_quick_preview_responses(self) -> None:
        with RunningMockServer() as mock:
            control = gtmctl.GtmControlPlane(manifest(), mock.client())
            control.apply("review-workspace")

            for response, expected in (
                ({}, "Unexpected quick preview container version response"),
                (
                    {"compilerError": False},
                    "Unexpected quick preview container version response",
                ),
                (
                    {
                        "compilerError": "false",
                        "containerVersion": {"containerVersionId": "preview"},
                    },
                    "compilerError must be a boolean when present",
                ),
                ([], "Unexpected GTM quick preview response"),
            ):
                with self.subTest(response=response):
                    mock.state.quick_preview_response_override = response
                    with self.assertRaisesRegex(gtmctl.GtmCtlError, expected):
                        control.validate("review-workspace")

    def test_inventory_rejects_malformed_live_version_responses(self) -> None:
        with RunningMockServer() as mock:
            control = gtmctl.GtmControlPlane(manifest(), mock.client())

            for response, expected in (
                ({}, "did not contain containerVersionId"),
                (
                    {"containerVersionId": "4", "tag": {}},
                    "Live container version tag must be a list",
                ),
                ([], "Unexpected live container version response"),
            ):
                with self.subTest(response=response):
                    mock.state.live_version_response_override = response
                    with self.assertRaisesRegex(gtmctl.GtmCtlError, expected):
                        control.inventory()

    def test_validate_detects_behavior_bearing_fields_outside_manifest(self) -> None:
        with RunningMockServer() as mock:
            value = manifest()
            control = gtmctl.GtmControlPlane(value, mock.client())
            control.apply("review-workspace")
            workspace = mock.state.workspaces["9"]
            google_tag = next(
                item
                for item in workspace["tags"]
                if item["name"] == "APP - Google tag - Public marketing"
            )
            google_tag["blockingTriggerId"] = ["unexpected-blocker"]

            with self.assertRaisesRegex(
                gtmctl.GtmCtlError,
                '"action": "update"',
            ):
                control.validate("review-workspace")

            resumed = control.resume("review-workspace")
            google_action = next(
                item
                for item in resumed["managed"]["tags"]
                if item["name"] == "APP - Google tag - Public marketing"
            )
            self.assertEqual(google_action["action"], "update")
            saved_google_tag = next(
                item
                for item in workspace["tags"]
                if item["name"] == "APP - Google tag - Public marketing"
            )
            self.assertNotIn("blockingTriggerId", saved_google_tag)


if __name__ == "__main__":
    unittest.main()

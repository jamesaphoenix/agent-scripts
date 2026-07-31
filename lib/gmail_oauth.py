#!/usr/bin/env python3
"""Reusable Gmail OAuth helper for host-level scripts.

The module centralizes the Gmail OAuth profile for jamesaphoenix@googlemail.com
without storing secrets in git. It reads credentials from environment variables,
then macOS keychain services, then 1Password refs from config/mail-oauth.json.
"""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "mail-oauth.json"
DEFAULT_PROFILE = "jamesaphoenix-googlemail"
GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"


def _load_config(config_path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    path = Path(config_path) if config_path else Path(os.environ.get("MAIL_OAUTH_CONFIG", DEFAULT_CONFIG))
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _profile(profile: str | None = None, config_path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    selected = profile or os.environ.get("MAIL_OAUTH_PROFILE") or DEFAULT_PROFILE
    config = _load_config(config_path)
    try:
        return config["profiles"][selected]
    except KeyError as exc:
        raise SystemExit(f"Mail OAuth profile not found: {selected}") from exc


def _run_secret_command(command: list[str]) -> str | None:
    try:
        result = subprocess.run(command, capture_output=True, check=False, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value if value else None


def _keychain_value(service_name: str) -> str | None:
    return _run_secret_command(["security", "find-generic-password", "-s", service_name, "-w"])


def _op_value(ref: str) -> str | None:
    if os.environ.get("AGENT_SCRIPTS_MAIL_OAUTH_DISABLE_OP_FALLBACK") == "1":
        return None
    return _run_secret_command(["op", "read", ref])


def _credential(profile_config: dict[str, Any], key: str) -> str:
    env_name = profile_config.get("env", {}).get(key)
    if env_name:
        env_value = os.environ.get(env_name)
        if env_value:
            return env_value

    service_name = profile_config.get("keychainServices", {}).get(key)
    if service_name:
        value = _keychain_value(service_name)
        if value:
            return value

    for legacy_service in profile_config.get("legacyKeychainServices", {}).get(key, []):
        value = _keychain_value(legacy_service)
        if value:
            return value

    op_ref = profile_config.get("opRefs", {}).get(key)
    if op_ref:
        value = _op_value(op_ref)
        if value:
            return value

    raise SystemExit(
        f"Missing Gmail OAuth credential {key}. Run agent-scripts/scripts/cache-mail-oauth.sh."
    )


def access_token(profile: str | None = None, config_path: str | os.PathLike[str] | None = None) -> str:
    profile_config = _profile(profile, config_path)
    body = urllib.parse.urlencode(
        {
            "client_id": _credential(profile_config, "clientId"),
            "client_secret": _credential(profile_config, "clientSecret"),
            "refresh_token": _credential(profile_config, "refreshToken"),
            "grant_type": "refresh_token",
        }
    ).encode()
    request = urllib.request.Request("https://oauth2.googleapis.com/token", data=body)
    try:
        payload = json.load(urllib.request.urlopen(request, timeout=30))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise SystemExit("Gmail token refresh failed: " + detail) from exc
    return str(payload["access_token"])


def _get(url: str, token: str) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"Authorization": "Bearer " + token})
    return json.load(urllib.request.urlopen(request, timeout=60))


def search(query: str, token: str | None = None, limit: int = 10) -> tuple[list[dict[str, Any]], str]:
    tok = token or access_token()
    url = f"{GMAIL}/messages?q={urllib.parse.quote(query)}&maxResults={limit}"
    return list(_get(url, tok).get("messages", [])), tok


def get_message(message_id: str, token: str | None = None) -> tuple[dict[str, Any], str]:
    tok = token or access_token()
    return _get(f"{GMAIL}/messages/{message_id}?format=full", tok), tok


def _walk_parts(payload: dict[str, Any]):
    stack = [payload]
    while stack:
        part = stack.pop()
        yield part
        stack.extend(part.get("parts", []) or [])


def body_text(message: dict[str, Any]) -> str:
    output: list[str] = []
    for part in _walk_parts(message.get("payload", {})):
        if part.get("mimeType", "").startswith("text/") and part.get("body", {}).get("data"):
            encoded = part["body"]["data"] + "=="
            output.append(base64.urlsafe_b64decode(encoded).decode("utf-8", "replace"))
    return "\n".join(output)


def _download(url: str, output_path: str, token: str | None = None) -> bool:
    headers = {"Authorization": "Bearer " + token} if token else {}
    request = urllib.request.Request(url, headers=headers)
    data = urllib.request.urlopen(request, timeout=60).read()
    with open(output_path, "wb") as handle:
        handle.write(data)
    return data[:5] == b"%PDF-"


def message_pdf(message: dict[str, Any], output_path: str, token: str) -> str | None:
    pdfs = [
        part
        for part in _walk_parts(message.get("payload", {}))
        if part.get("filename", "").lower().endswith(".pdf")
        and part.get("body", {}).get("attachmentId")
    ]
    if pdfs:
        pdfs.sort(key=lambda part: 0 if "invoice" in part["filename"].lower() else 1)
        part = pdfs[0]
        attachment = _get(
            f"{GMAIL}/messages/{message['id']}/attachments/{part['body']['attachmentId']}",
            token,
        )
        raw = base64.urlsafe_b64decode(attachment["data"] + "==")
        with open(output_path, "wb") as handle:
            handle.write(raw)
        return "attachment" if raw[:5] == b"%PDF-" else "attachment(notpdf)"

    body = body_text(message)
    links = re.findall(r"https://[^\s\"'<>)]+", body)

    def usable(url: str) -> bool:
        return "email.stripe.com" not in url.lower()

    for url in links:
        lowered = url.lower()
        if usable(url) and ("files.stripe.com/files/" in lowered or lowered.endswith("/pdf") or "/pdf?" in lowered):
            try:
                if _download(url, output_path):
                    return "direct_pdf"
            except Exception:
                pass

    for url in links:
        if usable(url) and "stripe.com" in url.lower() and ("/invoice/" in url or "/receipts/" in url):
            try:
                html = urllib.request.urlopen(urllib.request.Request(url), timeout=60).read().decode(
                    "utf-8",
                    "replace",
                )
                match = re.search(r'(https://[^"\']+/pdf[^"\']*)', html) or re.search(
                    r'"invoice_pdf"\s*:\s*"([^"]+)"',
                    html,
                )
                if match and _download(match.group(1).replace("\\/", "/"), output_path):
                    return "stripe_hosted_pdf"
            except Exception:
                pass

    match = re.search(r"https://[^\s\"'<>)]+\.pdf", body, re.I)
    if match and usable(match.group(0)) and _download(match.group(0), output_path):
        return "body_link"
    return None


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "token":
        print("access token OK (len %d)" % len(access_token()))
    elif command == "search":
        messages, _ = search(sys.argv[2], limit=int(sys.argv[3]) if len(sys.argv) > 3 else 10)
        print(json.dumps(messages, indent=2))
    elif command == "fetch":
        msg, tok = get_message(sys.argv[2])
        print(message_pdf(msg, sys.argv[3], tok))
    else:
        print(__doc__)

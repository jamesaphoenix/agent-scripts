#!/usr/bin/env python3
"""Use refreshable ADC for fleet image pulls, discarding ephemeral Docker logins."""
import json
import os
import subprocess
import sys

REGISTRY = "europe-west3-docker.pkg.dev"
HELPER = "/usr/local/bin/docker-credential-gcr"


def validate_registry(value):
    if value not in (REGISTRY, "https://" + REGISTRY):
        raise ValueError("Unsupported fleet registry")


def main():
    if len(sys.argv) != 2:
        raise ValueError("Expected a Docker credential operation")
    operation = sys.argv[1]
    data = sys.stdin.read(65537)
    if len(data) > 65536:
        raise ValueError("Credential input exceeds limit")
    if operation == "store":
        credentials = json.loads(data)
        validate_registry(credentials.get("ServerURL"))
        if credentials.get("Username") != "oauth2accesstoken" or not credentials.get("Secret"):
            raise ValueError("Only ephemeral OAuth logins are accepted")
        # The host ADC remains the credential source. Never persist an expiring token.
        return
    if operation == "erase":
        validate_registry(data.strip())
        return
    if operation == "list":
        print("{}")
        return
    if operation != "get":
        raise ValueError("Unsupported Docker credential operation")
    validate_registry(data.strip())
    env = dict(os.environ)
    env["GOOGLE_APPLICATION_CREDENTIALS"] = "/root/.config/gcloud/application_default_credentials.json"
    subprocess.run([HELPER, "get"], input=data, text=True, env=env, check=True, timeout=30)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, json.JSONDecodeError, subprocess.SubprocessError):
        print("Fleet registry credential operation failed", file=sys.stderr)
        raise SystemExit(1)

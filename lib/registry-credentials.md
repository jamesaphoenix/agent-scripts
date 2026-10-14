# Hetzner image registry authentication

The fleet uses the official `docker-credential-gcr` v2.2.1 binary, verified against its release SHA256. Root's private ADC file contains an existing service-account key. The account needs Artifact Registry Reader on the two application repositories only.

Run `scripts/install-hetzner-registry-helper.sh EXISTING_ADC_KEY WRAPPER_SOURCE` as root on Hetzner, passing `lib/docker-credential-jud-gcr.py` as the wrapper source. The installer preserves the old Docker configuration and any previous ADC in a private rollback directory.

Docker uses `jud-gcr` for `europe-west3-docker.pkg.dev`. The wrapper delegates reads to the official helper, which obtains fresh credentials. It accepts and discards ephemeral `oauth2accesstoken` logins from existing deployment scripts, so these logins cannot replace the refreshable credential source. Other registries are rejected. Existing unrelated registry configuration is retained. No credentials are printed or committed.

Validate an actual immutable image pull and a subsequent deployment login followed by another pull. Do not display the helper's `get` output, since it contains the access token.

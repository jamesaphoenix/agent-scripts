# Artifact Registry image retention

The production repositories serve both staging and production releases. Keep
the newest 3 versions of every package, delete untagged versions after 7 days,
and delete older versions after 21 days. Tags beginning `nas-retain-` protect
currently deployed and observed healthy rollback digests, plus Octospark native
compute images, even when newer staging builds push them outside the newest 3.
A keep policy wins over a delete policy.

`pin_nas_images.py` reads each NAS current receipt and the maintenance job's
healthy release ledger, then moves a bounded set of per-environment tags to
the current and two preceding distinct digests. Run it with `--apply` for each
app and environment before enabling the more aggressive cleanup. Octospark
native compute pins are set from the Studio and Hetzner release proofs. The
Octospark and Smart Cub deployment workflows update these tags after releases.

Apply each JSON file to its matching repository in `europe-west3`:

```sh
gcloud artifacts repositories set-cleanup-policies tx-agent-kit --project=octospark-prod --location=europe-west3 --policy=config/artifact-registry-cleanup/octospark-prod.json --no-dry-run
gcloud artifacts repositories set-cleanup-policies trace-learn --project=tracelearn-prod --location=europe-west3 --policy=config/artifact-registry-cleanup/tracelearn-prod.json --no-dry-run
gcloud artifacts repositories set-cleanup-policies tx-agent-kit --project=octospark-staging --location=europe-west3 --policy=config/artifact-registry-cleanup/octospark-staging-legacy.json --no-dry-run
```

Octospark's older `octospark-staging/tx-agent-kit` repository is not used by
current NAS releases. Its 14-day tagged / 1-day untagged cleanup remains, with
its minimum reduced from 10 to 3 per package.
`tracelearn-staging` and `gowith-solar-prod` have no enabled Artifact Registry
API or image repository; Solar ROI transfers a saved image directly to the NAS.

Before changing the age or minimum count, compare the candidate versions with
NAS current and observed healthy rollback receipts, and check native renderer
and media runtime deployments. Artifact Registry applies cleanup in a periodic
background job, so repository size does not fall immediately after a change.

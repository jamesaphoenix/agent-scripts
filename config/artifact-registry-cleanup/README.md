# Artifact Registry image retention

The production repositories serve both staging and production releases. Keep
the newest 50 versions of every package, delete untagged versions after 14 days,
and delete older versions after 60 days. A keep policy wins over a delete policy.

Apply each JSON file to its matching repository in `europe-west3`:

```sh
gcloud artifacts repositories set-cleanup-policies tx-agent-kit --project=octospark-prod --location=europe-west3 --policy=config/artifact-registry-cleanup/octospark-prod.json --no-dry-run
gcloud artifacts repositories set-cleanup-policies trace-learn --project=tracelearn-prod --location=europe-west3 --policy=config/artifact-registry-cleanup/tracelearn-prod.json --no-dry-run
```

Octospark's older `octospark-staging/tx-agent-kit` repository is not used by
current NAS releases. Its existing active 14-day and newest-10 policy remains.
`tracelearn-staging` and `gowith-solar-prod` have no enabled Artifact Registry
API or image repository; Solar ROI transfers a saved image directly to the NAS.

Before changing the age or minimum count, compare the candidate versions with
NAS current and observed healthy rollback receipts, and check native renderer
and media runtime deployments. Artifact Registry applies cleanup in a periodic
background job, so repository size does not fall immediately after a change.

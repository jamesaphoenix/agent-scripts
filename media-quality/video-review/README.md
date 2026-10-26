# Smart Cub desktop video reviewer

The MacBook desktop entry is `Smart Cub Video Review.html`, which opens
`http://127.0.0.1:18762/`. `Start Smart Cub Video Review.command` reconnects the
SSH tunnel and opens the player after sleep/restart or loss of connection.
Tailscale and the Mac Studio must be online. No media is copied to the MacBook.

The private, loopback-only Studio service is registered as
`smartcub-video-review` / `com.jud.smartcub-video-review`. Install only this task
with `bash scripts/install-launchd-tasks.sh --task smartcub-video-review`.
The service only streams the selected manifest files and stores human notes. It
does not render, scan, publish, or change the delivery pointer or approvals.

The player supports 1, 1.5, 2, 2.5 and 3 times speed, title/skill search, type,
aspect and status filters, decisions, timestamped notes and JSON export. Playback
speed and current selection persist in the browser. Human review decisions are
scoped to the exact output hash and saved atomically on Studio in:

```
/Users/jamesphoenix/render-tmp/smart-cub-refresh-20261016/workflow/cta-11plus-routing-20261021/human-review/reviews.json
```

An append-only `reviews.history.jsonl` alongside it records changes. Failed writes
show an error and retain the latest draft in browser storage. Export reviews for
a portable JSON backup. A human “good” decision never publishes or automatically
changes the batch manifest's launch approval.

The server implements byte ranges for seeking, permits only selected media,
binds to localhost, rejects unrecognised Host headers and cross-origin writes.
No third party scripts, packages, secrets or paid APIs are used.

```
python3 -m unittest discover -s tests -p test_video_review.py
```

Validation: three server integration tests passed locally and on Studio; the
live tunnel returns all 586 entries and a valid 206 byte-range response for video.
The UI script passes `node --check`. Native/browser automation was unavailable
in the creating session, so no browser-rendered visual validation is claimed.

To stop the review server later, use `launchctl bootout` for its own label only.
The earlier render/automated-scan service remains disabled at the user's request.

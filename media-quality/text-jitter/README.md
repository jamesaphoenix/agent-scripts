# Text jitter review harness

Find text that jumps, oscillates or keeps drifting after it should be still. Runs offline on Mac Studio. No model, ElevenLabs, Gemini or other paid API calls.

The motivating regression is `p1-what-121-means-tall.mp4`, 36-38 seconds. `p1-regression.json` records the affected original hash and a reviewed text region. Do not use old output approval as evidence that a changed output passes.

## Install and run

Requirements: Python 3.11+, FFmpeg/ffprobe, Tesseract with English data, and the Python requirements in this folder. Keep dependencies in a dedicated virtual environment. The Studio's existing `/opt/homebrew/bin/tesseract` is sufficient.

```sh
python3 -m venv .venv-text-jitter
.venv-text-jitter/bin/python -m pip install -r media-quality/text-jitter/requirements.txt

# Automatic discovery across one complete film
.venv-text-jitter/bin/python media-quality/text-jitter/text_jitter.py \
  /absolute/path/video.mp4 --out /absolute/path/new-review-folder

# Strict regression on the reported example or a candidate replacement
.venv-text-jitter/bin/python media-quality/text-jitter/text_jitter.py \
  /absolute/path/p1-what-121-means-tall.mp4 \
  --regions media-quality/text-jitter/p1-regression.json \
  --out /absolute/path/new-p1-regression-folder

# Scan the current 586-output delivery manifest sequentially. Pilot with --limit 5.
.venv-text-jitter/bin/python media-quality/text-jitter/text_jitter.py \
  --manifest /absolute/path/delivery/current/manifest.json \
  --out /absolute/path/new-batch-review-folder --limit 5
```

Open the output `index.html`. It contains timestamped findings and short evidence clips. `report.json` contains frame numbers, displacement traces in native pixels, match confidence, input SHA256, configuration and implementation SHA256. Batch `index.json` checkpoints each completed video and distinguishes errors from findings. Reports never overwrite existing folders. Inputs are read only.

Delivery-manifest SHA256 values are checked when supplied. Add the report path and hash to that exact output's visual-review evidence. A finding, lost tracking, source mismatch or missing coverage blocks an unattended text-stability pass; a reviewer must resolve it or document the intended movement. Run the strict p1 regression after layout edits as well as automatic discovery on affected complete exports. This helper does not publish, select or approve media.

Exit codes: **0** means no findings in the text regions actually tracked, **1** means findings or unscorable tracking need review, **2** means a configuration/decode error or no usable text coverage. None is whole-video visual approval. The harness does not write the video-review ledger or select delivery files.

## How it works

OCR probes every 0.75 seconds find lines of text. A line must be recognised in three successive probes before it is tracked. Matching text is followed through every native decoded frame in that interval, allowing 0.25 seconds after the first probe for an entrance. OCR itself is not the movement measurement. A fixed image of the letters is matched against nearby frame crops using [OpenCV template matching](https://docs.opencv.org/4.x/d4/dc6/tutorial_py_template_matching.html). This avoids treating changes to a neighbouring coloured rule as a movement of the text. OCR uses [Tesseract TSV output](https://tesseract-ocr.github.io/tessdoc/Command-Line-Usage.html).

Declared static regions bypass discovery and track the supplied box during the requested interval. Regions longer than two seconds are checked in overlapping two-second windows. The real p1 case is a strict region test.

The default threshold is 1.5 pixels of total displacement or 1.5 pixels between adjacent frames. Slow drift therefore counts as a finding too. Subpixel correlation peaks reduce rounding artefacts. Every frame must retain a confident match. A changed, scaled, lost or out-of-range glyph produces an unscorable interval, never a clean result. Blank regions do not pass. Native pixel thresholds can be set with `--range-px`, `--step-px` and `--search-px` or per declared region.

## Intended movement and coverage

Automatic findings are candidates for review. The harness cannot infer whether a designer wanted a zoom, moving label or animated title. Record a reviewed exclusion in the region configuration, including its reason:

```json
{
  "exclusions": [
    {"start": 0.0, "end": 0.7, "box": [100, 100, 700, 180], "reason": "Reviewed title entrance"}
  ]
}
```

An exclusion only applies if its interval and optional rectangle fully contain the measured interval and text box. It does not suppress unrelated text elsewhere. For reliable release checks, declare the exact settled intervals instead of excluding a whole scene.

Limitations: discovery can miss text that OCR cannot recognise, very short captions, changed lines and movement outside the search radius. Constant-frame-rate media are required; nominal and average frame rates and available frame counts are checked. For arbitrary external video, verify CFR or normalise a separate review copy first. Camera movement also moves text and will be flagged. Text scaling/deformation can cause an unscorable result rather than a precise displacement. Colour, legibility, teaching correctness, audio and lip-sync require their other checks.

Decoding is streamed; only approximately 1.5-2 seconds of grey frames are held. Runs are sequential, use one OpenCV/FFmpeg worker and nice 10, and evidence creation stops below 50 GiB free. At most 12 evidence clips per video are written by default; omitted clip counts are explicit, and all numeric findings remain in JSON. Configure `--evidence-limit 0` for measurement-only batch scans. Keep reports and final exports; remove only task-owned intermediate fixtures when no longer needed.

## Regression tests

Run on Mac Studio, since the integration test generates and decodes fixture video:

```sh
.venv-text-jitter/bin/python -m unittest discover -s media-quality/text-jitter -v
```

The tests cover stable text beside a flickering rule, a one-frame jump, repeated jitter, slow drift, blank regions, replaced text, lost tracking, reasoned exclusions, subpixel stability and an encoded MP4 at native frame cadence. Also run the real p1 regression on both its affected original and each proposed replacement. A synthetic pass alone is insufficient.

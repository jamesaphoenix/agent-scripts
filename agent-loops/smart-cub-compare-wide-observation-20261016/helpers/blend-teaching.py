#!/usr/bin/env python3
"""Plan or create social teaching derivatives. Rendering is restricted to Mac Studio.

Plan anywhere:
  python3 scripts/blend-teaching.py --source-root /path/to/rebuilt-videos
Execute on Studio after CTA review:
  python3 scripts/blend-teaching.py --source-root /path/to/rebuilt-videos \
    --cta-root out --output-root out/teaching --execute
Or use Studio's normalised inputs:
  python3 scripts/blend-teaching.py --source-root inputs/teaching \
    --source-layout normalized --execute

Never replaces published teaching media. Successful renders still require visual/audio review.
Source paths are relative to --source-root; all 19 CTAs are catalogued, but tutor sales
CTAs are intentionally excluded from the assignments for these teaching videos.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import socket
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timezone

APP = Path(__file__).resolve().parent.parent
FPS = 30
RATE = 48000
MIN_FREE_BYTES = 50 * 1024**3
DIMENSIONS = {"wide": (1920, 1080), "tall": (1080, 1920)}


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def fingerprint(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def require_studio(hostname: str | None = None) -> None:
    host = socket.gethostname() if hostname is None else hostname
    # Actual Studio: JamessMacStudio.fritz.box. Tailscale: jamess-mac-studio.
    if re.sub(r"[^a-z0-9]", "", host.split(".")[0].lower()) != "jamessmacstudio":
        raise ValueError(f"Rendering is restricted to Mac Studio; current host: {host}")


def resolve_source(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if Path(relative).is_absolute() or not path.is_relative_to(root.resolve()):
        raise ValueError(f"Source path escapes source root: {relative}")
    return path


def teaching_source(row: dict, aspect: str, root: Path, layout: str) -> Path:
    relative = (f"{row['skillKey']}/{aspect}.mp4" if layout == "normalized"
                else row["media"][aspect]["relativePath"])
    return resolve_source(root, relative)


def validate_manifest(data: dict) -> None:
    if data.get("schemaVersion") != 1 or len(data.get("videos", [])) != 144:
        raise ValueError("Expected the complete 144-video input manifest, schemaVersion 1")
    ctas = {cta["id"]: cta for cta in data["ctaLibrary"]}
    if (len(ctas) != len(data["ctaLibrary"])
            or any(not re.fullmatch(r"cta-sc-\d{2}", key) for key in ctas)):
        raise ValueError("Duplicate or unsafe CTA IDs")
    ids = [row["skillKey"] for row in data["videos"]]
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate teaching skill IDs")
    for row in data["videos"]:
        if not re.fullmatch(r"[a-z0-9.-]+", row["skillKey"]):
            raise ValueError("Unsafe teaching skill ID")
        cta = ctas.get(row["ctaId"])
        if not cta or cta["audience"] not in ("both", "parents"):
            raise ValueError(f"Unsuitable teaching CTA for {row['skillKey']}")
        for aspect, dimensions in DIMENSIONS.items():
            source = row["media"][aspect]
            if not re.fullmatch(r"[a-f0-9]{64}", source["sha256"]):
                raise ValueError(f"Invalid source checksum: {row['skillKey']}")
            if (source["width"], source["height"]) != dimensions:
                raise ValueError(f"Unexpected source dimensions: {row['skillKey']}")
            resolve_source(Path("/manifest-source"), source["relativePath"])


def probe(path: Path) -> dict:
    require_studio()
    return json.loads(subprocess.check_output([
        "ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)
    ], text=True))


def media_info(metadata: dict, aspect: str) -> dict:
    videos = [s for s in metadata["streams"] if s["codec_type"] == "video"
              and not s.get("disposition", {}).get("attached_pic")]
    audios = [s for s in metadata["streams"] if s["codec_type"] == "audio"]
    if len(videos) != 1 or len(audios) != 1:
        raise ValueError("Expected exactly one video and one audio stream")
    video, audio = videos[0], audios[0]
    if (video["width"], video["height"]) != DIMENSIONS[aspect]:
        raise ValueError(f"Source/CTA does not match {aspect} dimensions")
    rotation = [video.get("tags", {}).get("rotate", 0)]
    rotation += [side.get("rotation", 0) for side in video.get("side_data_list", [])]
    if any(float(value) % 360 for value in rotation):
        raise ValueError("Rotated input needs explicit review before blending")
    durations = []
    for container in [metadata.get("format", {}), video, audio]:
        value = container.get("duration")
        if value not in (None, "N/A"):
            durations.append(float(value))
    if not durations or any(not math.isfinite(d) or d <= 0 for d in durations):
        raise ValueError("Missing or invalid media duration")
    # The longest audio/video/container duration wins. Pad the shorter stream;
    # never use -shortest or the teaching voice can be cut at the splice.
    frames = math.ceil(max(durations) * FPS - 1e-7)
    return {"durationSeconds": frames / FPS, "frames": frames,
            "videoStream": video["index"], "audioStream": audio["index"],
            "inputFrameRate": video.get("avg_frame_rate"),
            "sampleRate": audio.get("sample_rate"), "channels": audio.get("channels")}


def output_reservation(seconds: float) -> int:
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("Invalid output duration for disk admission")
    # Deliberately generous safety ceiling, not an expected bitrate. The actual
    # ffmpeg file limit is below the reservation to leave container-write room.
    return math.ceil(seconds * 64_192_000 / 8) + 64 * 1024**2


def require_disk_space(free_bytes: int, reservation: int) -> None:
    if free_bytes - reservation < MIN_FREE_BYTES:
        raise ValueError("Candidate would breach the 50 GiB free-space floor")


def ffmpeg_command(source: Path, cta: Path, output: Path, infos: list[dict],
                   encoder: str, output_limit: int | None = None) -> list[str]:
    filters = []
    for index, info in enumerate(infos):
        duration = f"{info['durationSeconds']:.9f}"
        filters += [
            f"[{index}:{info['videoStream']}]setpts=PTS-STARTPTS,fps={FPS},"
            f"setsar=1,format=yuv420p,tpad=stop_mode=clone:stop_duration={duration},"
            f"trim=duration={duration},setpts=PTS-STARTPTS[v{index}]",
            f"[{index}:{info['audioStream']}]asetpts=PTS-STARTPTS,aresample={RATE},"
            f"aformat=sample_fmts=fltp:channel_layouts=stereo,apad,"
            f"atrim=duration={duration},asetpts=PTS-STARTPTS[a{index}]",
        ]
    filters.append("[v0][a0][v1][a1]concat=n=2:v=1:a=1[v][a]")
    video_args = (["-c:v", "h264_videotoolbox", "-b:v", "12M", "-allow_sw", "0"]
                  if encoder == "videotoolbox" else
                  ["-c:v", "libx264", "-preset", "medium", "-crf", "18"])
    return ["ffmpeg", "-hide_banner", "-nostdin", "-v", "warning", "-y",
            "-i", str(source), "-i", str(cta), "-filter_complex", ";".join(filters),
            "-map", "[v]", "-map", "[a]", *video_args, "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-ar", str(RATE), "-ac", "2",
            "-movflags", "+faststart", "-map_metadata", "-1",
            *(["-fs", str(output_limit)] if output_limit is not None else []), str(output)]


def can_resume(output: Path, receipt_path: Path, dependency_hash: str) -> bool:
    if not output.is_file() or not receipt_path.is_file():
        return False
    try:
        receipt = json.loads(receipt_path.read_text())
        return (receipt["status"] == "rendered-needs-review"
                and receipt["dependencyHash"] == dependency_hash
                and receipt["output"]["sha256"] == sha256(output))
    except (OSError, ValueError, KeyError, TypeError):
        return False


def atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def execute_job(row: dict, aspect: str, args: argparse.Namespace, tool_version: str) -> None:
    require_studio()
    source_def = row["media"][aspect]
    source = teaching_source(row, aspect, args.source_root, args.source_layout)
    cta = args.cta_root.resolve() / f"{row['ctaId']}-{aspect}.mp4"
    if source.stat().st_size != source_def["bytes"] or sha256(source) != source_def["sha256"]:
        raise ValueError(f"Teaching source checksum mismatch: {source}")
    cta_hash = sha256(cta)
    dependencies = {"source": source_def, "ctaId": row["ctaId"], "ctaSha256": cta_hash,
                    "aspect": aspect, "encoder": args.encoder, "fps": FPS,
                    "sampleRate": RATE, "scriptSha256": sha256(Path(__file__)),
                    "ffmpegVersion": tool_version}
    dependency_hash = fingerprint(dependencies)
    output = args.output_root.resolve() / f"{row['skillKey']}-{aspect}-cta.mp4"
    if output in (source, cta):
        raise ValueError("Derivative output must not replace a source or CTA input")
    receipt_path = output.with_suffix(".receipt.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    lock = output.with_suffix(".lock")
    # Exclusive creation prevents two dispatches from replacing the same output.
    try:
        lock_fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise ValueError(f"Job is already locked: {lock}. Inspect owner before removing a stale lock.")
    temporary = output.with_name(f".{output.stem}.{uuid.uuid4().hex}.partial.mp4")
    try:
        with os.fdopen(lock_fd, "w") as owner:
            json.dump({"pid": os.getpid(), "hostname": socket.gethostname()}, owner)
        if can_resume(output, receipt_path, dependency_hash):
            print(f"SKIP verified {output.name}", flush=True)
            return
        infos = [media_info(probe(path), aspect) for path in [source, cta]]
        expected = sum(item["durationSeconds"] for item in infos)
        reservation = output_reservation(expected)
        require_disk_space(shutil.disk_usage(output.parent).free, reservation)
        output_limit = reservation - 16 * 1024**2
        subprocess.run(ffmpeg_command(source, cta, temporary, infos, args.encoder,
                                     output_limit=output_limit), check=True)
        metadata = probe(temporary)
        media_info(metadata, aspect)
        expected = sum(item["durationSeconds"] for item in infos)
        actual = float(metadata["format"]["duration"])
        if abs(actual - expected) > 0.09:
            raise ValueError(f"Output duration {actual} differs from full inputs {expected}")
        video = next(s for s in metadata["streams"] if s["codec_type"] == "video")
        audio = next(s for s in metadata["streams"] if s["codec_type"] == "audio")
        if (video["avg_frame_rate"] != "30/1" or int(audio["sample_rate"]) != RATE
                or audio["channels"] != 2):
            raise ValueError("Unexpected output frame rate or audio format")
        # Check sources again so an in-place CTA re-render cannot receive a stale receipt.
        if sha256(source) != source_def["sha256"] or sha256(cta) != cta_hash:
            raise ValueError("Input changed during rendering; output discarded")
        receipt = {"schemaVersion": 1, "status": "rendered-needs-review",
                   "renderedAt": datetime.now(timezone.utc).isoformat(),
                   "host": socket.gethostname(), "skillKey": row["skillKey"],
                   "dependencyHash": dependency_hash, "dependencies": dependencies,
                   "sourcePath": str(source), "ctaPath": str(cta), "inputs": infos,
                   "storageAdmission": {"minimumFreeBytes": MIN_FREE_BYTES,
                                        "reservedBytes": reservation, "outputByteLimit": output_limit},
                   "output": {"path": str(output), "sha256": sha256(temporary),
                              "bytes": temporary.stat().st_size, "durationSeconds": actual,
                              "expectedDurationSeconds": expected, **output_format(aspect)}}
        os.replace(temporary, output)
        atomic_json(receipt_path, receipt)
        print(f"RENDERED needs review {output.name}", flush=True)
    finally:
        temporary.unlink(missing_ok=True)
        lock.unlink(missing_ok=True)


def output_format(aspect: str) -> dict:
    width, height = DIMENSIONS[aspect]
    return {"width": width, "height": height, "fps": FPS, "sampleRate": RATE}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=APP / "data/teaching-social-inputs.json")
    parser.add_argument("--assignments", type=Path, default=APP / "data/teaching-cta-assignments.json")
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--source-layout", choices=["rebuilt", "normalized"], default="rebuilt",
                        help="rebuilt: manifest relativePath; normalized: <skillKey>/<aspect>.mp4")
    parser.add_argument("--cta-root", type=Path, default=APP / "out")
    parser.add_argument("--output-root", type=Path, default=APP / "out/teaching")
    parser.add_argument("--id", action="append", dest="ids", help="Repeat to select skill IDs")
    parser.add_argument("--aspect", choices=["wide", "tall", "both"], default="both")
    parser.add_argument("--encoder", choices=["libx264", "videotoolbox"], default="libx264")
    parser.add_argument("--execute", action="store_true", help="Render on Studio; otherwise only print plan")
    args = parser.parse_args()
    data = json.loads(args.manifest.read_text())
    assignments = json.loads(args.assignments.read_text())
    if set(assignments) != {row["skillKey"] for row in data["videos"]}:
        raise ValueError("Teaching CTA assignment IDs must match the full input manifest")
    for row in data["videos"]:
        row["ctaId"] = assignments[row["skillKey"]]
    validate_manifest(data)
    rows = [row for row in data["videos"] if not args.ids or row["skillKey"] in args.ids]
    unknown = set(args.ids or []) - {row["skillKey"] for row in rows}
    if unknown:
        parser.error(f"Unknown IDs: {sorted(unknown)}")
    aspects = list(DIMENSIONS) if args.aspect == "both" else [args.aspect]
    if not args.execute:
        print(json.dumps({"status": "planned-no-rendering", "jobs": [
            {"skillKey": row["skillKey"], "aspect": aspect,
             "source": str(teaching_source(row, aspect, args.source_root, args.source_layout)),
             "sourceSha256": row["media"][aspect]["sha256"], "ctaId": row["ctaId"],
             "cta": str(args.cta_root / f"{row['ctaId']}-{aspect}.mp4"),
             "output": str(args.output_root / f"{row['skillKey']}-{aspect}-cta.mp4")}
            for row in rows for aspect in aspects]}, indent=2))
        return
    require_studio()
    version = subprocess.check_output(["ffmpeg", "-version"], text=True)
    for row in rows:
        for aspect in aspects:
            execute_job(row, aspect, args, version)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        sys.exit(str(error))

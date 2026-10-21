#!/usr/bin/env python3
"""Exact future workload commands; default is print-only on either Mac."""
import argparse
import json
import os
import shlex
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MODEL = ("/Users/jamesphoenix/models/Qwen3.8-Flash-Next/UD-Q3_K_XL/"
         "Qwen3.8-Flash-Next-UD-Q3_K_XL-00001-of-00003.gguf")
SERVER = "/opt/homebrew/bin/llama-server"
BENCH = "/opt/homebrew/bin/llama-bench"
SESSIONS = ("cache8", "cache32", "checkpoints", "batch-short",
            "long2048", "long4096", "gpu", "gpu-server", "threads", "custom")


def bench(batch, ubatch, lengths, cpu=True):
    args = [BENCH, "-m", MODEL, "-ngl", "999", "-fa", "on",
            "-ctk", "q8_0", "-ctv", "q8_0", "-t", "12",
            "-b", batch, "-ub", ubatch, "-p", lengths, "-n", "128",
            "-r", "2", "-o", "jsonl", "--progress"]
    args += ["-ot", "per_layer_token_embd=CPU" if cpu else "per_layer_token_embd=MTL0"]
    if not cpu:
        args += ["-lzm", "off"]
    return list(map(str, args))


def server(cache=8192, checkpoint=8192, threads=None, cpu=True):
    args = [SERVER, "--model", MODEL, "--alias", "qwen3.8-flash-next",
            "--host", "127.0.0.1", "--port", "8083", "--parallel", "2",
            "--kv-unified-per-slot", "131072", "--n-gpu-layers", "999",
            "--flash-attn", "on", "--cont-batching", "--cache-reuse", "256",
            "--cache-type-k", "q8_0", "--cache-type-v", "q8_0",
            "--fit-target", "12288", "--metrics", "--jinja", "--no-webui",
            "--reasoning-budget", "4096", "--reasoning-budget-message",
            "Time to answer. Stop reasoning and give the code now.",
            "--reasoning-effort", "medium", "-ot", "per_layer_token_embd=CPU",
            "--temp", "1.0", "--top-p", "0.95", "--top-k", "20", "--min-p", "0",
            "-b", "2048", "-ub", "512", "--cache-ram", str(cache),
            "--ctx-checkpoints", "32", "--checkpoint-min-step", str(checkpoint),
            "--log-verbosity", "4"]
    if threads is not None:
        args += ["--threads-batch", str(threads)]
    if not cpu:
        args[args.index("-ot") + 1] = "per_layer_token_embd=MTL0"
        args += ["--lazy-mode", "off"]
    return args


def jobs(session):
    if session == "custom":
        spec = json.loads(Path(os.environ["LLAMA_BENCH_SPEC"]).read_text())
        if spec["kind"] == "bench":
            command = bench(spec["batch"], spec["ubatch"], spec.get("lengths", "512,8192,32768"), spec.get("cpu", True))
            command[command.index("-r") + 1] = str(spec.get("repetitions", 5))
            if "depth" in spec:
                command += ["-d", str(spec["depth"])]
            if spec.get("verbose"):
                command += ["-v"]
            return [("bench", spec["name"], command)]
        command = server(cache=spec.get("cache", 8192), checkpoint=spec.get("checkpoint", 8192),
                         threads=spec.get("threads"), cpu=spec.get("cpu", True))
        command[command.index("-b") + 1] = str(spec.get("batch", 2048))
        command[command.index("-ub") + 1] = str(spec.get("ubatch", 512))
        if spec.get("lazy_mode")=="off" and "--lazy-mode" not in command:
            command += ["--lazy-mode", "off"]
        return [("structured" if spec.get("structured") else "server", spec["name"], command)]
    if session == "batch-short":
        return [("bench", "short", bench("2048,4096", "512,1024,2048", "512,8192"))]
    if session.startswith("long"):
        return [("bench", session, bench(session[4:], "512,1024,2048", "32768"))]
    if session == "gpu":
        resident=bench(2048,512,"512,8192,32768")+["-lzm","off"]
        return [("bench", "cpu-control", bench(2048, 512, "512,8192,32768")),
                ("bench", "cpu-resident", resident),
                ("bench", "gpu-table", bench(2048, 512, "512,8192,32768", cpu=False))]
    if session == "threads":
        return [("server", "tb12", server(threads=12)),
                ("server", "tb16", server(threads=16))]
    if session == "checkpoints":
        return [("server", "checkpoint2048", server(cache=8192, checkpoint=2048))]
    if session == "gpu-server":
        return [("server", "gpu-full-context", server(cpu=False))]
    return [("server", session, server(cache=8192 if session == "cache8" else 32768))]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session", choices=SESSIONS)
    parser.add_argument("--out", type=Path, default=Path("results"))
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    spec = json.loads(Path(os.environ["LLAMA_BENCH_SPEC"]).read_text()) if args.session=="custom" else {}
    probe_lengths=spec.get("probe_lengths", "8192" if args.session=="threads" else "8192,32768")
    churn_count=str(spec.get("churn_count",2))
    for kind, label, command in jobs(args.session):
        structured_command=[sys.executable,str(ROOT/"structured-probe.py"),"--out",str(args.out/(label+"-structured")),
                            "--churn-count",str(spec.get("structured_churn_count",spec.get("churn_count",2)))]
        if spec.get("capacity"): structured_command+=["--capacity"]
        print(shlex.join(command), flush=True)
        if kind == "structured":
            print(shlex.join(structured_command),flush=True)
        if kind == "server":
            print(shlex.join([sys.executable, str(ROOT / "cache-probe.py"),
                              "--out", str(args.out / (label + "-cache")),
                              "--lengths", probe_lengths, "--churn-count", churn_count]), flush=True)
            print(shlex.join([sys.executable, str(ROOT / "chat-probe.py"),
                              "--out", str(args.out / (label + "-chat"))]), flush=True)
            if spec.get("also_structured"):
                print(shlex.join(structured_command),flush=True)
        if not args.execute:
            continue
        if os.environ.get("LLAMA_BENCH_WINDOW_ACTIVE") != "1":
            raise RuntimeError("Execution requires the approved run-window.py controller")
        args.out.mkdir(parents=True, exist_ok=True)
        with (args.out / (label + ".stdout")).open("w") as stdout, \
             (args.out / (label + ".stderr")).open("w") as stderr:
            if kind == "bench":
                subprocess.run(command, stdout=stdout, stderr=stderr, check=True)
                continue
            # Inherit the controller's worker process group, including probe.
            process = subprocess.Popen(command, stdout=stdout, stderr=stderr)
            try:
                deadline = time.monotonic() + 150
                while True:
                    if process.poll() is not None:
                        raise RuntimeError("Temporary server exited during load")
                    try:
                        with urllib.request.urlopen("http://127.0.0.1:8083/health", timeout=2) as response:
                            if json.load(response).get("status") == "ok":
                                break
                    except Exception:
                        pass
                    if time.monotonic() > deadline:
                        raise RuntimeError("Temporary server health timeout")
                    time.sleep(1)
                if kind == "structured":
                    subprocess.run(structured_command,check=True)
                    continue
                subprocess.run([sys.executable, str(ROOT / "cache-probe.py"),
                                "--out", str(args.out / (label + "-cache")),
                                "--lengths", probe_lengths, "--churn-count", churn_count],
                               check=True)
                subprocess.run([sys.executable, str(ROOT / "chat-probe.py"),
                                "--out", str(args.out / (label + "-chat"))], check=True)
                if spec.get("also_structured"):
                    subprocess.run(structured_command,check=True)
            finally:
                process.send_signal(signal.SIGTERM)
                try:
                    process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Synthetic prefix/churn probe. Refuses the live port. Run only during approval.

Uses native /completion so prompt_n and timings have a stable schema. This
isolates cache behaviour; the plan also calls for a chat-template smoke check.
"""
import argparse
import hashlib
import json
import time
import urllib.request
from pathlib import Path


BASE = "http://127.0.0.1:8083"


def post(path, body):
    request = urllib.request.Request(BASE + path, json.dumps(body).encode(),
                                     {"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=180) as response:
        return json.load(response)


def tokens(text):
    return post("/tokenize", {"content": text, "add_special": False})["tokens"]


def probe(out, label, prompt, cache=True):
    body = {"prompt": prompt, "id_slot": 0, "cache_prompt": cache,
            "n_predict": 128, "ignore_eos": True, "temperature": 0,
            "seed": 1234, "stream": True, "return_tokens": True}
    (out / (label + ".request.json")).write_text(json.dumps(body))
    request = urllib.request.Request(BASE + "/completion", json.dumps(body).encode(),
                                     {"Content-Type": "application/json"})
    start = time.monotonic()
    first_data = first_token = None
    final = None
    generated = []
    with (out / (label + ".sse.jsonl")).open("w") as raw:
        with urllib.request.urlopen(request, timeout=180) as response:
            for line in response:
                if not line.startswith(b"data:"):
                    continue
                payload = line[5:].strip()
                if payload == b"[DONE]":
                    continue
                event = json.loads(payload)
                elapsed = time.monotonic() - start
                raw.write(json.dumps({"elapsed_s": elapsed, "event": event}) + "\n")
                raw.flush()
                if first_data is None:
                    first_data = elapsed
                if event.get("tokens"):
                    generated.extend(event["tokens"])
                if (event.get("tokens") or event.get("content")) and first_token is None:
                    first_token = elapsed
                if event.get("error"):
                    raise RuntimeError(event["error"])
                if event.get("stop"):
                    final = event
    if final is None or "timings" not in final or first_token is None:
        raise RuntimeError("No final timings or generated token: " + label)
    result = {"label": label, "input_tokens": len(prompt),
              "input_sha256": hashlib.sha256(json.dumps(prompt).encode()).hexdigest(),
              "first_sse_s": first_data, "ttft_s": first_token,
              "wall_s": time.monotonic() - start, "timings": final["timings"],
              "tokens_evaluated": final.get("tokens_evaluated"),
              "tokens_cached": final.get("tokens_cached"),
              "tokens_predicted": final.get("tokens_predicted"),
              "truncated": final.get("truncated")}
    with (out / "summary.jsonl").open("a") as handle:
        handle.write(json.dumps(result) + "\n")
    print(json.dumps(result), flush=True)
    if final.get("truncated"):
        raise RuntimeError("Truncated prompt invalidates comparison")
    if not generated:
        raise RuntimeError("No generated token IDs for exact append test")
    if len(generated) != final.get("tokens_predicted"):
        raise RuntimeError("Generated token IDs do not match predicted count")
    return generated


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--lengths", default="8192,32768")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    for length in map(int, args.lengths.split(",")):
        # Stable serialized coding-agent workload. No real source or user data.
        # Deliberately different first tokens make B/C evict A's slot history.
        filler = tokens("\n".join(
            f"// file src/module_{i}.ts\nexport const item_{i} = {{id: {i}, enabled: true}};"
            for i in range(5000)))
        suffix = tokens("\n<|im_end|>\n<|im_start|>assistant\n")
        prompts = {}
        for name in ("A", "B", "C"):
            header = tokens(f"Session {name} {length}\n<|im_start|>system\n"
                            "You are a coding agent. Tools: read_file(path), edit_file(path,old,new), "
                            "run_tests(target). Read files before editing.\n<|im_end|>\n"
                            "<|im_start|>user\nReview these synthetic files:\n")
            prompts[name] = (header + filler[:length - len(header) - len(suffix)] + suffix)
            if len(prompts[name]) != length:
                raise RuntimeError("Insufficient fixture tokens")
        prefix = prompts["A"]
        stem = f"pp{length}"
        generated = probe(args.out, stem + "-cold-A", prefix)
        appended = prefix + generated + tokens("\n<|im_end|>\n<|im_start|>user\n"
                                              "Now add a regression test.\n<|im_end|>\n"
                                              "<|im_start|>assistant\n")
        middle = appended.copy()
        replacement = tokens("CHANGED")
        position = length // 2
        middle[position:position + len(replacement)] = replacement
        probe(args.out, stem + "-append-A", appended)
        probe(args.out, stem + "-middle-A", middle)
        probe(args.out, stem + "-churn-B", prompts["B"])
        probe(args.out, stem + "-churn-C", prompts["C"])
        probe(args.out, stem + "-return-A", appended)
        probe(args.out, stem + "-uncached-control", appended, cache=False)


if __name__ == "__main__":
    main()

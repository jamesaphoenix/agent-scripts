#!/usr/bin/env python3
"""Structured coding-chat checkpoint probe on temporary port 8083 only."""
import argparse
import copy
import json
import time
import urllib.request
from pathlib import Path


def request(out, label, messages, cache=True):
    body = {"model": "qwen3.8-flash-next", "messages": messages,
            "tools": [{"type": "function", "function": {"name": "read_file",
                "parameters": {"type": "object", "properties": {"path": {"type": "string"}},
                               "required": ["path"]}}}],
            "tool_choice": "none", "max_tokens": 128, "temperature": 0, "seed": 1234,
            "cache_prompt": cache, "stream": True, "stream_options": {"include_usage": True}}
    (out / (label + ".request.json")).write_text(json.dumps(body))
    req = urllib.request.Request("http://127.0.0.1:8083/v1/chat/completions",
        json.dumps(body).encode(), {"Content-Type": "application/json"})
    start = time.monotonic()
    ttft = visible = None
    content = reasoning = ""
    timings = usage = None
    with (out / (label + ".sse.jsonl")).open("w") as log:
        with urllib.request.urlopen(req, timeout=180) as response:
            for line in response:
                if not line.startswith(b"data:") or line[5:].strip() == b"[DONE]":
                    continue
                event = json.loads(line[5:].strip())
                elapsed = time.monotonic() - start
                log.write(json.dumps({"elapsed_s": elapsed, "event": event}) + "\n")
                log.flush()
                if event.get("error"):
                    raise RuntimeError(event["error"])
                for choice in event.get("choices", []):
                    delta = choice.get("delta", {})
                    text = delta.get("content") or ""
                    thought = delta.get("reasoning_content") or ""
                    content += text
                    reasoning += thought
                    if (text or thought) and ttft is None:
                        ttft = elapsed
                    if text and visible is None:
                        visible = elapsed
                timings = event.get("timings") or timings
                usage = event.get("usage") or usage
    if ttft is None or not timings:
        raise RuntimeError("Missing chat token/timing evidence")
    row = {"label": label, "ttft_s": ttft, "first_visible_s": visible,
           "wall_s": time.monotonic() - start, "timings": timings, "usage": usage}
    with (out / "summary.jsonl").open("a") as handle:
        handle.write(json.dumps(row) + "\n")
    print(json.dumps(row), flush=True)
    return {"role": "assistant", "content": content, "reasoning_content": reasoning}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--churn-count",type=int,default=2)
    parser.add_argument("--capacity",action="store_true",help="Keep A unchanged before churn to isolate retention capacity")
    args = parser.parse_args()
    if not 2<=args.churn_count<=12: parser.error("churn count must be 2..12")
    args.out.mkdir(parents=True, exist_ok=False)
    messages = [{"role": "system", "content": "Session A. You are a coding agent. Read before editing."}]
    for block in range(8):
        messages.append({"role": "user", "content": f"Review module {block}:\n" + "\n".join(
            f"export const item_{block}_{i} = {{id: {i}, enabled: true, label: 'fixture {i}'}};"
            for i in range(200))})
        if block < 7:
            messages.append({"role": "assistant", "content": f"Module {block} received."})
    reply = request(args.out, "structured-cold-A", messages)
    appended = messages + [reply, {"role": "user", "content": "Now suggest a regression test."}]
    request(args.out, "structured-append-A", appended)
    middle = copy.deepcopy(appended)
    middle[9]["content"] = middle[9]["content"].replace("enabled: true", "enabled: false", 1)
    if not args.capacity: request(args.out, "structured-middle-A", middle)
    for tag in [chr(ord("B")+i) for i in range(args.churn_count)]:
        unrelated = copy.deepcopy(messages)
        unrelated[0]["content"] = unrelated[0]["content"].replace("Session A", "Session " + tag)
        request(args.out, "structured-churn-" + tag, unrelated)
    request(args.out, "structured-return-A", appended)
    request(args.out, "structured-uncached-control", appended, cache=False)


if __name__ == "__main__":
    main()

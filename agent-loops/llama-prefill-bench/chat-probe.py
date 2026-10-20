#!/usr/bin/env python3
"""One synthetic pi-shaped chat pair, restricted to the temporary server."""
import argparse
import json
import time
import urllib.request
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    messages = [{"role": "system", "content": "You are a coding agent. Read before editing."},
                {"role": "user", "content": "Review these synthetic declarations and acknowledge.\n" +
                 "\n".join(f"export const fixture_{i} = {{id: {i}}};" for i in range(500))}]
    tools = [{"type": "function", "function": {"name": "read_file", "description": "Read a file",
             "parameters": {"type": "object", "properties": {"path": {"type": "string"}},
                            "required": ["path"]}}}]
    for turn in (1, 2):
        body = {"model": "qwen3.8-flash-next", "messages": messages, "tools": tools,
                "tool_choice": "none", "max_tokens": 128, "temperature": 0, "seed": 1234,
                "cache_prompt": True, "stream": True, "stream_options": {"include_usage": True}}
        (args.out / f"turn{turn}.request.json").write_text(json.dumps(body))
        request = urllib.request.Request("http://127.0.0.1:8083/v1/chat/completions",
                                         json.dumps(body).encode(), {"Content-Type": "application/json"})
        started = time.monotonic()
        token_ttft = visible_ttft = None
        content = reasoning = ""
        timings = usage = None
        with (args.out / f"turn{turn}.sse.jsonl").open("w") as log:
            with urllib.request.urlopen(request, timeout=180) as response:
                for line in response:
                    if not line.startswith(b"data:") or line[5:].strip() == b"[DONE]":
                        continue
                    event = json.loads(line[5:].strip())
                    elapsed = time.monotonic() - started
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
                        if (text or thought) and token_ttft is None:
                            token_ttft = elapsed
                        if text and visible_ttft is None:
                            visible_ttft = elapsed
                    timings = event.get("timings") or timings
                    usage = event.get("usage") or usage
        result = {"turn": turn, "ttft_s": token_ttft, "first_visible_s": visible_ttft,
                  "timings": timings, "usage": usage, "wall_s": time.monotonic() - started}
        (args.out / f"turn{turn}.summary.json").write_text(json.dumps(result, indent=2))
        print(json.dumps(result), flush=True)
        if token_ttft is None:
            raise RuntimeError("Chat stream had no generated text or reasoning")
        messages += [{"role": "assistant", "content": content, "reasoning_content": reasoning},
                     {"role": "user", "content": "Now suggest a regression test."}]


if __name__ == "__main__":
    main()

import { dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { Type } from "typebox";
import { getAgentDir, type ExtensionAPI, type ExtensionContext } from "@earendil-works/pi-coding-agent";
import {
  createKeyResolver,
  defaultLegacyPaths,
  makeRealRunOp,
  opRefFrom,
  readFileIfPresent,
  type KeyResolution,
} from "./key.ts";

/**
 * web-search extension for pi
 * --------------------------------
 * Registers two model tools backed by the Brave Search API:
 *   - web_search : query -> ranked results (title, url, snippet)
 *   - web_fetch  : url   -> cleaned page text (truncated)
 *
 * API key resolution order (lazy, on the first web_search call, cached in memory):
 *   1. BRAVE_API_KEY environment variable
 *   2. `op read "op://api-keys/Brave Search/credential"` via the 1Password service account
 *      (bounded by PI_WEB_SEARCH_OP_TIMEOUT_MS, default 10s; override the ref with BRAVE_API_KEY_OP_REF)
 *   3. deprecated: a plaintext `brave.key` next to this extension or in ~/.pi/agent/
 *
 * Source of truth: agent-scripts/pi-extensions/web-search (symlinked by dotfiles/install.sh).
 */

const here = dirname(fileURLToPath(import.meta.url));

const getBraveKey = createKeyResolver({
  env: process.env,
  runOp: makeRealRunOp(process.env),
  readFile: readFileIfPresent,
  legacyPaths: defaultLegacyPaths(here, getAgentDir()),
});

let warnedDeprecated = false;

async function braveKey(ctx: ExtensionContext | undefined): Promise<KeyResolution> {
  const res = await getBraveKey();
  if (res.deprecated && !warnedDeprecated) {
    warnedDeprecated = true;
    const msg = `web-search: ${res.notes[res.notes.length - 1]}`;
    if (ctx?.hasUI) ctx.ui.notify(msg, "warning");
    else process.stderr.write(`${msg}\n`);
  }
  return res;
}

const BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search";
const MAX_FETCH_CHARS = 30_000;
const MAX_RESULT_CHARS = 12_000;

function missingKeyMessage(res: KeyResolution): string {
  return (
    "Brave Search API key not found. Set BRAVE_API_KEY, or make sure the 1Password service account " +
    `can read ${opRefFrom(process.env)}.` +
    (res.notes.length ? `\nDetails: ${res.notes.join("; ")}` : "")
  );
}

/** Strip HTML to readable text (no external deps). */
function htmlToText(html: string): string {
  let text = html;
  // Drop scripts, styles, noscript, comments, svg
  text = text.replace(/<script[\s\S]*?<\/script>/gi, " ");
  text = text.replace(/<style[\s\S]*?<\/style>/gi, " ");
  text = text.replace(/<noscript[\s\S]*?<\/noscript>/gi, " ");
  text = text.replace(/<svg[\s\S]*?<\/svg>/gi, " ");
  text = text.replace(/<!--[\s\S]*?-->/g, " ");
  // Headings / list items / paragraphs -> newlines
  text = text.replace(/<\/(h[1-6]|p|div|li|tr|section|article|br)>/gi, "\n");
  text = text.replace(/<li[^>]*>/gi, "  - ");
  // Remove remaining tags
  text = text.replace(/<[^>]+>/g, " ");
  // Decode common entities
  text = text
    .replace(/&nbsp;/g, " ")
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#39;|&apos;/g, "'");
  // Normalize whitespace
  text = text.replace(/[ \t]+/g, " ");
  text = text.replace(/\n\s*\n\s*\n+/g, "\n\n");
  return text.trim();
}

type BraveResult = {
  title?: string;
  url?: string;
  description?: string;
};

export default function (pi: ExtensionAPI) {
  pi.registerTool({
    name: "web_search",
    label: "Web Search",
    description: "Search the web using Brave Search and return ranked results (title, url, snippet).",
    promptSnippet: "Search the web for up-to-date information",
    promptGuidelines: [
      "Use web_search when you need current or external information that is not in the local files or your training data.",
    ],
    parameters: Type.Object({
      query: Type.String({ description: "The search query" }),
      count: Type.Optional(Type.Number({ description: "Number of results (1-20, default 8)" })),
    }),
    async execute(_toolCallId, params, signal, _onUpdate, ctx) {
      const keyRes = await braveKey(ctx);
      const BRAVE_KEY = keyRes.key;
      if (!BRAVE_KEY) {
        return { content: [{ type: "text", text: missingKeyMessage(keyRes) }], isError: true };
      }

      const count = Math.max(1, Math.min(20, Math.round(params.count ?? 8)));

      const url = new URL(BRAVE_ENDPOINT);
      url.searchParams.set("q", params.query);
      url.searchParams.set("count", String(count));

      let res: Response;
      try {
        res = await fetch(url, {
          headers: {
            Accept: "application/json",
            "Accept-Encoding": "gzip",
            "X-Subscription-Token": BRAVE_KEY,
          },
          signal,
        });
      } catch (err) {
        const aborted = signal?.aborted;
        return {
          content: [{ type: "text", text: aborted ? "Search cancelled." : `Search request failed: ${String(err)}` }],
          isError: true,
        };
      }

      if (!res.ok) {
        const body = await res.text().catch(() => "");
        return {
          content: [
            { type: "text", text: `Brave Search error ${res.status} ${res.statusText}: ${body.slice(0, 500)}` },
          ],
          isError: true,
        };
      }

      const data = (await res.json()) as { web?: { results?: BraveResult[] } };
      const results = data.web?.results ?? [];

      if (results.length === 0) {
        return { content: [{ type: "text", text: `No results for: ${params.query}` }] };
      }

      const lines = results.slice(0, count).map((r, i) => {
        const parts = [`${i + 1}. ${r.title ?? "(untitled)"}`, `   URL: ${r.url ?? ""}`];
        if (r.description) parts.push(`   ${r.description}`);
        return parts.join("\n");
      });

      let out = `Web search results for: ${params.query}\n\n${lines.join("\n\n")}`;
      if (out.length > MAX_RESULT_CHARS) out = out.slice(0, MAX_RESULT_CHARS) + "\n\n[truncated]";

      return { content: [{ type: "text", text: out }] };
    },
  });

  pi.registerTool({
    name: "web_fetch",
    label: "Web Fetch",
    description: "Fetch a URL and return its content as cleaned, truncated text.",
    promptSnippet: "Fetch and read the content of a web page",
    promptGuidelines: [
      "Use web_fetch after web_search to read the full content of a specific result URL.",
    ],
    parameters: Type.Object({
      url: Type.String({ description: "The absolute URL to fetch (http/https)" }),
    }),
    async execute(_toolCallId, params, signal, onUpdate) {
      let target: URL;
      try {
        target = new URL(params.url);
      } catch {
        return { content: [{ type: "text", text: `Invalid URL: ${params.url}` }], isError: true };
      }
      if (target.protocol !== "http:" && target.protocol !== "https:") {
        return { content: [{ type: "text", text: `Only http/https URLs are supported.` }], isError: true };
      }

      onUpdate?.({ content: [{ type: "text", text: `Fetching ${params.url}...` }] });

      let res: Response;
      try {
        res = await fetch(target, {
          redirect: "follow",
          signal,
          headers: {
            "User-Agent":
              "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
            Accept: "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
          },
        });
      } catch (err) {
        const aborted = signal?.aborted;
        return {
          content: [{ type: "text", text: aborted ? "Fetch cancelled." : `Fetch failed: ${String(err)}` }],
          isError: true,
        };
      }

      const contentType = res.headers.get("content-type") ?? "";
      const body = await res.text();

      let text: string;
      if (contentType.includes("application/json")) {
        try {
          text = JSON.stringify(JSON.parse(body), null, 2);
        } catch {
          text = body;
        }
      } else if (contentType.includes("text/html") || /<\s*html/i.test(body)) {
        text = htmlToText(body);
      } else {
        text = body;
      }

      const statusLine = `Fetched ${params.url} (${res.status} ${res.statusText})\n\n`;
      let out = statusLine + text;
      if (out.length > MAX_FETCH_CHARS) out = out.slice(0, MAX_FETCH_CHARS) + "\n\n[truncated]";

      if (!res.ok) {
        return { content: [{ type: "text", text: out }], isError: true };
      }
      return { content: [{ type: "text", text: out }] };
    },
  });
}

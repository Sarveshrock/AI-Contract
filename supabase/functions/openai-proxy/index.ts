// ContractLens — OpenAI proxy (Supabase Edge Function).
//
// Why: an OpenAI key on every desktop is an exfiltration risk. With this function the desktop app sends its
// *Supabase user JWT* as the "API key" to  <project>/functions/v1/openai-proxy  (OPENAI_BASE_URL) and the
// OpenAI key stays in server-side secrets. The OpenAI SDK works unchanged: it calls
//   POST {base_url}/chat/completions   and   POST {base_url}/embeddings
//
// Deploy:  supabase functions deploy openai-proxy
//          supabase secrets set OPENAI_API_KEY=sk-...   (never commit it)
//
// Guarantees: authenticated users only, allowlisted paths and models, bounded request size and output
// tokens, per-user rate limit (best effort, per instance), and no request/response bodies are logged.

import { createClient } from "jsr:@supabase/supabase-js@2";

const OPENAI_BASE = "https://api.openai.com/v1";
const ALLOWED_PATHS = new Set(["/chat/completions", "/embeddings"]);
const ALLOWED_MODELS = new Set(
  (Deno.env.get("ALLOWED_MODELS") ??
    "gpt-4o,gpt-4o-mini,gpt-4.1,gpt-4.1-mini,text-embedding-3-small,text-embedding-3-large").split(",").map((m) => m.trim()),
);
const MAX_BODY_BYTES = 2_000_000;
const MAX_OUTPUT_TOKENS = 8192;
const RATE_LIMIT_PER_MINUTE = Number(Deno.env.get("RATE_LIMIT_PER_MINUTE") ?? "60");

const hits = new Map<string, number[]>();

function rateLimited(userId: string): boolean {
  const now = Date.now();
  const recent = (hits.get(userId) ?? []).filter((t) => now - t < 60_000);
  recent.push(now);
  hits.set(userId, recent);
  return recent.length > RATE_LIMIT_PER_MINUTE;
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

Deno.serve(async (req: Request): Promise<Response> => {
  if (req.method !== "POST") return json(405, { error: { message: "POST only" } });

  const authHeader = req.headers.get("Authorization");
  if (!authHeader?.startsWith("Bearer ")) return json(401, { error: { message: "Missing bearer token" } });

  const supabase = createClient(Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_ANON_KEY")!, {
    global: { headers: { Authorization: authHeader } },
  });
  const { data: { user }, error } = await supabase.auth.getUser();
  if (error || !user) return json(401, { error: { message: "Invalid or expired session" } });

  const path = new URL(req.url).pathname.replace(/^.*\/openai-proxy/, "");
  if (!ALLOWED_PATHS.has(path)) return json(404, { error: { message: "Path not allowed" } });
  if (rateLimited(user.id)) return json(429, { error: { message: "Rate limit exceeded" } });

  const raw = await req.text();
  if (raw.length > MAX_BODY_BYTES) return json(413, { error: { message: "Request too large" } });

  let payload: Record<string, unknown>;
  try {
    payload = JSON.parse(raw);
  } catch {
    return json(400, { error: { message: "Invalid JSON" } });
  }
  if (typeof payload.model !== "string" || !ALLOWED_MODELS.has(payload.model)) {
    return json(400, { error: { message: "Model not allowed" } });
  }
  if (payload.stream === true) return json(400, { error: { message: "Streaming is not supported" } });
  for (const key of ["max_tokens", "max_completion_tokens"]) {
    if (typeof payload[key] === "number" && (payload[key] as number) > MAX_OUTPUT_TOKENS) payload[key] = MAX_OUTPUT_TOKENS;
  }

  const upstream = await fetch(`${OPENAI_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${Deno.env.get("OPENAI_API_KEY")!}` },
    body: JSON.stringify(payload),
  });
  const text = await upstream.text();
  // Metadata only: never log prompts, contract text or completions.
  console.log(JSON.stringify({ user: user.id, path, model: payload.model, status: upstream.status, bytes: text.length }));
  return new Response(text, { status: upstream.status, headers: { "Content-Type": "application/json" } });
});

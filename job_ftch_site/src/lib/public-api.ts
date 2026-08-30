export type PublicJob = {
  id: string;
  title: string;
  company: string;
  location: string;
  workMode: string;
  source: string;
  publishedAt: string;
  url: string;
};

export async function readPublicData(path: "/sources" | "/jobs") {
  const base = process.env.JOB_FTCH_PUBLIC_API_BASE_URL?.trim();
  if (!base) return [];
  const endpoint = path === "/sources" ? "/public/tenants/ai_jobs/sources.json" : "/public/tenants/ai_jobs/jobs.json?limit=1000";
  const response = await fetch(new URL(endpoint, `${base.replace(/\/$/, "")}/`), {
    headers: process.env.JOB_FTCH_PUBLIC_API_TOKEN ? { Authorization: `Bearer ${process.env.JOB_FTCH_PUBLIC_API_TOKEN}` } : undefined,
    next: { revalidate: 15 },
  });
  if (!response.ok) throw new Error(`Public catalog upstream returned ${response.status}`);
  const payload = await response.json() as { sources?: unknown[]; jobs?: unknown[] };
  return path === "/sources" ? payload.sources ?? [] : payload.jobs ?? [];
}

// ponytail: process-local limiter/cache keeps the public site dependency-free;
// use Redis or the upstream gateway when deploying more than one web replica.
const buckets = new Map<string, { startedAt: number; count: number }>();
export function allowRequest(key: string, limit = 60, windowMs = 60_000) {
  const now = Date.now();
  const bucket = buckets.get(key);
  if (!bucket || now - bucket.startedAt >= windowMs) {
    buckets.set(key, { startedAt: now, count: 1 });
    return true;
  }
  if (bucket.count >= limit) return false;
  bucket.count += 1;
  return true;
}

export function requestKey(request: Request) {
  return request.headers.get("x-forwarded-for")?.split(",")[0]?.trim() || "anonymous";
}

import { allowRequest, readPublicData, requestKey } from "@/lib/public-api";

export async function GET(request: Request) {
  if (!allowRequest(`jobs:${requestKey(request)}`)) return Response.json({ error: "rate_limited" }, { status: 429, headers: { "Retry-After": "60" } });
  const url = new URL(request.url);
  const limit = Math.min(Math.max(Number(url.searchParams.get("limit") || 100), 1), 1000);
  try {
    const jobs = (await readPublicData("/jobs")) as unknown[];
    return Response.json({ data: jobs.slice(0, limit), meta: { tenant: "ai_jobs", source: "postgres" } }, { headers: { "Cache-Control": "public, max-age=15, stale-while-revalidate=60" } });
  } catch {
    return Response.json({ error: "catalog_unavailable" }, { status: 503, headers: { "Cache-Control": "no-store" } });
  }
}

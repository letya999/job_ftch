import { allowRequest, readPublicData, requestKey } from "@/lib/public-api";

export async function GET(request: Request) {
  if (!allowRequest(`sources:${requestKey(request)}`)) return Response.json({ error: "rate_limited" }, { status: 429, headers: { "Retry-After": "60" } });
  try {
    return Response.json({ data: await readPublicData("/sources"), meta: { tenant: "ai_jobs" } }, { headers: { "Cache-Control": "public, max-age=15, stale-while-revalidate=60" } });
  } catch {
    return Response.json({ error: "catalog_unavailable" }, { status: 503, headers: { "Cache-Control": "no-store" } });
  }
}

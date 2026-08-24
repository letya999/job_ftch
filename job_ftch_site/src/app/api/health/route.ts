export function GET() {
  return Response.json({ status: "ok", service: "job-ftch-site" }, { headers: { "Cache-Control": "no-store" } });
}

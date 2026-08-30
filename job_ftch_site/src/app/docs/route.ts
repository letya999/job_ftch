import { redirect } from "next/navigation";
export function GET() { redirect(process.env.NEXT_PUBLIC_DOCS_URL || "https://letya999.github.io/job_ftch/"); }

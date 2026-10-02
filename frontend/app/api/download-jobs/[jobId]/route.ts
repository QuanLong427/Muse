import { BACKEND_URL } from "@/app/lib/config";

export const dynamic = "force-dynamic";

type Context = { params: Promise<{ jobId: string }> };

export async function GET(request: Request, context: Context) {
  const { jobId } = await context.params;
  const query = new URL(request.url).search;
  try {
    const response = await fetch(
      `${BACKEND_URL}/api/download-jobs/${encodeURIComponent(jobId)}${query}`,
      { cache: "no-store" }
    );
    return Response.json(await response.json(), { status: response.status });
  } catch {
    return Response.json({ detail: "Backend unavailable" }, { status: 502 });
  }
}

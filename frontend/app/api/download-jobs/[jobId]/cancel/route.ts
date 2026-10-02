import { BACKEND_URL } from "@/app/lib/config";

type Context = { params: Promise<{ jobId: string }> };

export async function POST(request: Request, context: Context) {
  const { jobId } = await context.params;
  try {
    const response = await fetch(
      `${BACKEND_URL}/api/download-jobs/${encodeURIComponent(jobId)}/cancel`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: await request.text(),
      }
    );
    return Response.json(await response.json(), { status: response.status });
  } catch {
    return Response.json({ detail: "Backend unavailable" }, { status: 502 });
  }
}

import { BACKEND_URL } from "@/app/lib/config";

export async function POST(request: Request, context: { params: Promise<{ draftId: string }> }) {
  const { draftId } = await context.params;
  try {
    const response = await fetch(`${BACKEND_URL}/api/playlist-drafts/${encodeURIComponent(draftId)}/confirm`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: await request.text(), cache: "no-store",
    });
    return Response.json(await response.json(), { status: response.status });
  } catch {
    return Response.json({ detail: "Backend unavailable" }, { status: 502 });
  }
}

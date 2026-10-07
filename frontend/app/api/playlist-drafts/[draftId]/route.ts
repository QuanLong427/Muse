import { BACKEND_URL } from "@/app/lib/config";

export const dynamic = "force-dynamic";
type Context = { params: Promise<{ draftId: string }> };

async function proxy(request: Request, context: Context) {
  const { draftId } = await context.params;
  try {
    const response = await fetch(`${BACKEND_URL}/api/playlist-drafts/${encodeURIComponent(draftId)}${new URL(request.url).search}`, {
      method: request.method,
      headers: { "Content-Type": "application/json" },
      ...(request.method === "PATCH" ? { body: await request.text() } : {}),
      cache: "no-store",
    });
    return Response.json(await response.json(), { status: response.status });
  } catch {
    return Response.json({ detail: "Backend unavailable" }, { status: 502 });
  }
}
export const GET = proxy;
export const PATCH = proxy;

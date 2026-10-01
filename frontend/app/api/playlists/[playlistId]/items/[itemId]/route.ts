import { BACKEND_URL } from "@/app/lib/config";

export const dynamic = "force-dynamic";

type Context = { params: Promise<{ playlistId: string; itemId: string }> };

export async function DELETE(request: Request, context: Context) {
  const { playlistId, itemId } = await context.params;
  const query = new URL(request.url).search;
  try {
    const response = await fetch(
      `${BACKEND_URL}/api/playlists/${encodeURIComponent(playlistId)}/items/${encodeURIComponent(itemId)}${query}`,
      { method: "DELETE" }
    );
    return Response.json(await response.json(), { status: response.status });
  } catch {
    return Response.json({ detail: "Backend unavailable" }, { status: 502 });
  }
}

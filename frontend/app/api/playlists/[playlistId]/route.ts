import { BACKEND_URL } from "@/app/lib/config";

export const dynamic = "force-dynamic";

type Context = { params: Promise<{ playlistId: string }> };

export async function GET(request: Request, context: Context) {
  const { playlistId } = await context.params;
  const query = new URL(request.url).search;
  try {
    const response = await fetch(
      `${BACKEND_URL}/api/playlists/${encodeURIComponent(playlistId)}${query}`,
      { cache: "no-store" }
    );
    return Response.json(await response.json(), { status: response.status });
  } catch {
    return Response.json({ detail: "Backend unavailable" }, { status: 502 });
  }
}

export async function PATCH(request: Request, context: Context) {
  const { playlistId } = await context.params;
  try {
    const response = await fetch(
      `${BACKEND_URL}/api/playlists/${encodeURIComponent(playlistId)}`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: await request.text(),
      }
    );
    return Response.json(await response.json(), { status: response.status });
  } catch {
    return Response.json({ detail: "Backend unavailable" }, { status: 502 });
  }
}

export async function DELETE(request: Request, context: Context) {
  const { playlistId } = await context.params;
  const query = new URL(request.url).search;
  try {
    const response = await fetch(
      `${BACKEND_URL}/api/playlists/${encodeURIComponent(playlistId)}${query}`,
      { method: "DELETE" }
    );
    return Response.json(await response.json(), { status: response.status });
  } catch {
    return Response.json({ detail: "Backend unavailable" }, { status: 502 });
  }
}

import { BACKEND_URL } from "@/app/lib/config";

export const dynamic = "force-dynamic";

type Context = { params: Promise<{ playlistId: string }> };

async function proxy(request: Request, context: Context, method: "POST" | "PUT") {
  const { playlistId } = await context.params;
  try {
    const response = await fetch(
      `${BACKEND_URL}/api/playlists/${encodeURIComponent(playlistId)}/items`,
      {
        method,
        headers: { "Content-Type": "application/json" },
        body: await request.text(),
      }
    );
    return Response.json(await response.json(), { status: response.status });
  } catch {
    return Response.json({ detail: "Backend unavailable" }, { status: 502 });
  }
}

export async function POST(request: Request, context: Context) {
  return proxy(request, context, "POST");
}

export async function PUT(request: Request, context: Context) {
  return proxy(request, context, "PUT");
}

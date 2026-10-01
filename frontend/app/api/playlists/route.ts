import { BACKEND_URL } from "@/app/lib/config";

export const dynamic = "force-dynamic";

export async function GET(request: Request) {
  const query = new URL(request.url).search;
  try {
    const response = await fetch(`${BACKEND_URL}/api/playlists${query}`, {
      cache: "no-store",
    });
    return Response.json(await response.json(), { status: response.status });
  } catch {
    return Response.json({ detail: "Backend unavailable" }, { status: 502 });
  }
}

export async function POST(request: Request) {
  try {
    const response = await fetch(`${BACKEND_URL}/api/playlists`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: await request.text(),
    });
    return Response.json(await response.json(), { status: response.status });
  } catch {
    return Response.json({ detail: "Backend unavailable" }, { status: 502 });
  }
}

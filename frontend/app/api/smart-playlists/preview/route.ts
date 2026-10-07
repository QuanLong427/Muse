import { BACKEND_URL } from "@/app/lib/config";

export const dynamic = "force-dynamic";

export async function POST(request: Request) {
  try {
    const response = await fetch(`${BACKEND_URL}/api/smart-playlists/preview`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: await request.text(),
      cache: "no-store",
    });
    return Response.json(await response.json(), { status: response.status });
  } catch {
    return Response.json({ detail: "Backend unavailable" }, { status: 502 });
  }
}

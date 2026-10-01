import { BACKEND_URL } from "@/app/lib/config";

export const dynamic = "force-dynamic";

export async function GET(request: Request) {
  try {
    const url = new URL(request.url);
    const trackId = url.searchParams.get("track_id");
    if (!trackId) {
      return Response.json({ error: "track_id is required" }, { status: 400 });
    }
    const res = await fetch(
      `${BACKEND_URL}/api/tracks/by-id?track_id=${encodeURIComponent(trackId)}`,
      { cache: "no-store" }
    );
    const data = await res.json();
    return Response.json(data, { status: res.status });
  } catch {
    return Response.json({ error: "Backend unavailable" }, { status: 502 });
  }
}

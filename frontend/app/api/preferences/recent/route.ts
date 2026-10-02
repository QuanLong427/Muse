import { BACKEND_URL } from "@/app/lib/config";

export async function GET(request: Request) {
  try {
    const source = new URL(request.url);
    const query = source.searchParams.toString();
    const response = await fetch(
      `${BACKEND_URL}/api/preferences/recent${query ? `?${query}` : ""}`,
      { cache: "no-store" }
    );
    return Response.json(await response.json(), { status: response.status });
  } catch {
    return Response.json({ detail: "Backend unavailable" }, { status: 502 });
  }
}

import { BACKEND_URL } from "@/app/lib/config";

export async function POST(
  request: Request,
  context: RouteContext<"/api/playback-session/actions/[actionId]/ack">
) {
  try {
    const { actionId } = await context.params;
    const body = await request.json();
    const response = await fetch(
      `${BACKEND_URL}/api/playback-session/actions/${encodeURIComponent(actionId)}/ack`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }
    );
    return Response.json(await response.json(), { status: response.status });
  } catch {
    return Response.json({ detail: "Backend unavailable" }, { status: 502 });
  }
}

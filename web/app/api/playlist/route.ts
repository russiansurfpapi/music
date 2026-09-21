import { NextRequest, NextResponse } from "next/server";
import { cookies } from "next/headers";
import { getSetSpotifyIds } from "@/lib/db";
import { createPlaylist, refreshAccessToken } from "@/lib/spotify";

export async function POST(request: NextRequest) {
  const cookieStore = cookies();
  let accessToken = cookieStore.get("spotify_access_token")?.value;
  const refreshToken = cookieStore.get("spotify_refresh_token")?.value;

  if (!accessToken && refreshToken) {
    const refreshed = await refreshAccessToken(refreshToken);
    if (refreshed) accessToken = refreshed.access_token;
  }

  if (!accessToken) {
    return NextResponse.json(
      { error: "Not authenticated. Please connect Spotify first." },
      { status: 401 }
    );
  }

  const body = await request.json();
  const { setId, name } = body;

  if (!setId || !name) {
    return NextResponse.json({ error: "Missing setId or name" }, { status: 400 });
  }

  const trackIds = getSetSpotifyIds(setId);

  if (trackIds.length === 0) {
    return NextResponse.json({ error: "No resolved tracks in this set" }, { status: 400 });
  }

  const result = await createPlaylist(accessToken, name, trackIds);

  if ("error" in result) {
    return NextResponse.json(result, { status: 500 });
  }

  return NextResponse.json(result);
}

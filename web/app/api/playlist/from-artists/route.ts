import { NextRequest, NextResponse } from "next/server";
import { cookies } from "next/headers";
import { createPlaylist, spotifyFetch, refreshAccessToken } from "@/lib/spotify";

export const maxDuration = 60;

async function getToken(): Promise<string | null> {
  const cookieStore = cookies();
  let token = cookieStore.get("spotify_access_token")?.value;
  if (!token) {
    const refresh = cookieStore.get("spotify_refresh_token")?.value;
    if (refresh) {
      const refreshed = await refreshAccessToken(refresh);
      if (refreshed) token = refreshed.access_token;
    }
  }
  return token || null;
}

export async function POST(request: NextRequest) {
  const token = await getToken();
  if (!token) {
    return NextResponse.json({ error: "Not authenticated. Connect Spotify first." }, { status: 401 });
  }

  const { artists, name, tracksPerArtist = 5 } = await request.json();
  if (!artists?.length) {
    return NextResponse.json({ error: "No artists provided" }, { status: 400 });
  }

  const trackIds: string[] = [];
  const notFound: string[] = [];

  for (const artist of artists as string[]) {
    // Search for artist's tracks using artist: filter (artist_top_tracks returns 403 in dev mode)
    const res = await spotifyFetch(
      `/search?${new URLSearchParams({
        q: `artist:${artist}`,
        type: "track",
        limit: String(Math.min(tracksPerArtist, 10)),
      })}`,
      token
    );

    if (res.status === 429) {
      return NextResponse.json({
        error: `Spotify rate limited. Try again in ${res.headers.get("Retry-After") || "a few"} seconds.`,
      }, { status: 429 });
    }

    if (!res.ok) continue;

    const data = await res.json();
    const tracks = data.tracks?.items || [];
    if (tracks.length === 0) {
      notFound.push(artist);
      continue;
    }

    for (const track of tracks.slice(0, tracksPerArtist)) {
      trackIds.push(track.id);
    }

    // Rate limit: 4s between searches
    await new Promise((r) => setTimeout(r, 4000));
  }

  if (trackIds.length === 0) {
    return NextResponse.json({ error: "No tracks found for any artist" }, { status: 404 });
  }

  const playlistName = name || `${(artists as string[]).slice(0, 3).join(", ")} Sampler`;
  const result = await createPlaylist(token, playlistName, trackIds);
  if ("error" in result) {
    return NextResponse.json(result, { status: 500 });
  }

  return NextResponse.json({ ...result, notFound });
}

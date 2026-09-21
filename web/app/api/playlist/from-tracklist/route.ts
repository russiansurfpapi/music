import { NextRequest, NextResponse } from "next/server";
import { cookies } from "next/headers";
import { createPlaylist, spotifyFetch, refreshAccessToken } from "@/lib/spotify";

export const maxDuration = 120;

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

function parseLine(line: string): { artist: string; title: string } | null {
  // Try common separators: " - ", " — ", " – ", " | "
  for (const sep of [" - ", " — ", " – ", " | "]) {
    const idx = line.indexOf(sep);
    if (idx > 0) {
      return {
        artist: line.slice(0, idx).trim(),
        title: line.slice(idx + sep.length).trim(),
      };
    }
  }
  return null;
}

export async function POST(request: NextRequest) {
  const token = await getToken();
  if (!token) {
    return NextResponse.json({ error: "Not authenticated. Connect Spotify first." }, { status: 401 });
  }

  const { lines, name } = await request.json();
  if (!lines?.length) {
    return NextResponse.json({ error: "No tracks provided" }, { status: 400 });
  }

  const trackIds: string[] = [];
  const resolved: string[] = [];
  const notFound: string[] = [];

  for (const line of lines as string[]) {
    const parsed = parseLine(line);
    if (!parsed) {
      notFound.push(line);
      continue;
    }

    const q = `track:${parsed.title} artist:${parsed.artist}`;
    const res = await spotifyFetch(
      `/search?${new URLSearchParams({ q, type: "track", limit: "1" })}`,
      token
    );

    if (res.status === 429) {
      // Return partial result on rate limit
      if (trackIds.length > 0) {
        const playlistName = name || "Tracklist";
        const result = await createPlaylist(token, playlistName, trackIds);
        if ("error" in result) return NextResponse.json(result, { status: 500 });
        return NextResponse.json({
          ...result,
          notFound,
          warning: `Rate limited after ${resolved.length}/${lines.length} tracks`,
        });
      }
      return NextResponse.json({ error: "Spotify rate limited" }, { status: 429 });
    }

    if (!res.ok) continue;

    const data = await res.json();
    const track = data.tracks?.items?.[0];
    if (track) {
      trackIds.push(track.id);
      resolved.push(`${parsed.artist} - ${parsed.title}`);
    } else {
      notFound.push(line);
    }

    await new Promise((r) => setTimeout(r, 4000));
  }

  if (trackIds.length === 0) {
    return NextResponse.json({ error: "No tracks found on Spotify" }, { status: 404 });
  }

  const playlistName = name || "Tracklist";
  const result = await createPlaylist(token, playlistName, trackIds);
  if ("error" in result) return NextResponse.json(result, { status: 500 });

  return NextResponse.json({ ...result, notFound });
}

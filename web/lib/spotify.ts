import { cookies } from "next/headers";

const TOKEN_ENDPOINT = "https://accounts.spotify.com/api/token";
const API_BASE = "https://api.spotify.com/v1";

export function getSpotifyTokens() {
  const cookieStore = cookies();
  return {
    accessToken: cookieStore.get("spotify_access_token")?.value || null,
    refreshToken: cookieStore.get("spotify_refresh_token")?.value || null,
  };
}

export function isAuthenticated(): boolean {
  return !!getSpotifyTokens().accessToken;
}

export async function refreshAccessToken(refreshToken: string): Promise<{
  access_token: string;
  expires_in: number;
} | null> {
  const res = await fetch(TOKEN_ENDPOINT, {
    method: "POST",
    headers: {
      "Content-Type": "application/x-www-form-urlencoded",
      Authorization: `Basic ${Buffer.from(
        `${process.env.SPOTIFY_CLIENT_ID}:${process.env.SPOTIFY_CLIENT_SECRET}`
      ).toString("base64")}`,
    },
    body: new URLSearchParams({
      grant_type: "refresh_token",
      refresh_token: refreshToken,
    }),
  });
  if (!res.ok) return null;
  return res.json();
}

export async function spotifyFetch(
  path: string,
  token: string,
  options: RequestInit = {}
) {
  return fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
      ...options.headers,
    },
  });
}

export async function createPlaylist(
  token: string,
  name: string,
  trackIds: string[]
): Promise<{ url: string; id: string; trackCount: number } | { error: string }> {
  // Get user ID
  const meRes = await spotifyFetch("/me", token);
  if (!meRes.ok) return { error: "Failed to get user profile" };
  const me = await meRes.json();

  // Create playlist (dev mode: must use /me/playlists, not /users/{id}/playlists)
  const createRes = await fetch(`${API_BASE}/me/playlists`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ name, public: false }),
  });
  if (!createRes.ok) {
    const err = await createRes.text();
    return { error: `Failed to create playlist: ${err}` };
  }
  const playlist = await createRes.json();

  // Add tracks in batches of 100
  const validIds = trackIds.filter(
    (id) => id && !id.startsWith("lfm:") && !id.startsWith("fp:") && /^[a-zA-Z0-9]{22}$/.test(id)
  );
  const uris = validIds.map((id) => `spotify:track:${id}`);

  for (let i = 0; i < uris.length; i += 100) {
    const batch = uris.slice(i, i + 100);
    await spotifyFetch(`/playlists/${playlist.id}/tracks`, token, {
      method: "POST",
      body: JSON.stringify({ uris: batch }),
    });
    if (i + 100 < uris.length) await new Promise((r) => setTimeout(r, 300));
  }

  return {
    url: playlist.external_urls?.spotify || `https://open.spotify.com/playlist/${playlist.id}`,
    id: playlist.id,
    trackCount: validIds.length,
  };
}

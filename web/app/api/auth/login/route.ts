import { NextResponse } from "next/server";

export async function GET() {
  const params = new URLSearchParams({
    response_type: "code",
    client_id: process.env.SPOTIFY_CLIENT_ID || "",
    scope: "playlist-modify-public playlist-modify-private user-read-private",
    redirect_uri: process.env.SPOTIFY_REDIRECT_URI || "http://localhost:3000/api/auth/callback",
    show_dialog: "true",
  });

  return NextResponse.redirect(
    `https://accounts.spotify.com/authorize?${params.toString()}`
  );
}

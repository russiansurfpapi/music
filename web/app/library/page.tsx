import { getAllTracks, getGenreDistribution, getSubgenreList, type LibraryTrack } from "@/lib/db";
import { LibraryBrowser } from "./library-browser";

export const dynamic = "force-dynamic";

export default function LibraryPage() {
  const tracks = getAllTracks();
  const genres = getGenreDistribution();
  const subgenres = getSubgenreList();

  return (
    <LibraryBrowser
      tracks={tracks}
      genres={genres}
      subgenres={subgenres}
    />
  );
}

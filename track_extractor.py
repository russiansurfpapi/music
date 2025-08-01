# import os
# import re
# import csv
# from bs4 import BeautifulSoup
# from typing import List, Tuple, Union

# def extract_tracks_from_html(html_content: str, source_file: str) -> List[Tuple[str, str, str]]:
#     """
#     Extracts (title, artist, source_file) tuples from the provided HTML content.
#     This version handles both 1001Tracklists and Beatport-style HTML formats.
#     """
#     soup = BeautifulSoup(html_content, "html.parser")
#     tracks = []

#     # Try 1001Tracklists format
#     tl_rows = soup.select(".trackFormat__text")
#     if tl_rows:
#         for row in tl_rows:
#             text = row.get_text(strip=True)
#             if " - " in text:
#                 artist, title = text.split(" - ", 1)
#                 tracks.append((title.strip(), artist.strip(), source_file))
#         return tracks

#     # Try Beatport-style format with metadata in <meta> tags
#     tl_divs = soup.select("div[itemtype='http://schema.org/MusicRecording']")
#     for div in tl_divs:
#         title = div.find("meta", {"itemprop": "name"})
#         artist = div.find("meta", {"itemprop": "byArtist"})
#         if title and artist:
#             title_text = title.get("content", "").strip()
#             artist_text = artist.get("content", "").strip()
#             if title_text and artist_text:
#                 tracks.append((title_text, artist_text, source_file))

#     # Fallback: try parsing visible text blocks with " - "
#     all_text = soup.get_text("\n", strip=True)
#     for line in all_text.splitlines():
#         if " - " in line and len(line.split(" - ")) == 2:
#             artist, title = line.split(" - ", 1)
#             if 2 < len(title.strip()) < 150:  # crude filter
#                 tracks.append((title.strip(), artist.strip(), source_file))

#     return tracks

# def extract_tracks_from_path(input_path: Union[str, os.PathLike], output_csv_path: str):
#     """
#     Process a single HTML file or a folder of HTML files and save (title, artist, source_file) tuples to CSV.
#     """
#     all_tracks = []
#     if os.path.isdir(input_path):
#         for file in os.listdir(input_path):
#             if file.endswith(".html"):
#                 with open(os.path.join(input_path, file), "r", encoding="utf-8") as f:
#                     html = f.read()
#                     tracks = extract_tracks_from_html(html, file)
#                     all_tracks.extend(tracks)
#     elif input_path.endswith(".html"):
#         with open(input_path, "r", encoding="utf-8") as f:
#             html = f.read()
#             tracks = extract_tracks_from_html(html, os.path.basename(input_path))
#             all_tracks.extend(tracks)

#     with open(output_csv_path, "w", newline="", encoding="utf-8") as f:
#         writer = csv.writer(f)
#         writer.writerow(["title", "artist", "source_file"])
#         writer.writerows(all_tracks)

#     print(f"✅ Saved {len(all_tracks)} tracks to {output_csv_path}")

# if __name__ == "__main__":
#     import argparse

#     parser = argparse.ArgumentParser()
#     parser.add_argument("--input", required=True, help="Path to HTML file or folder of HTML files")
#     parser.add_argument("--output", default="tracks_output.csv", help="Output CSV file path")
#     args = parser.parse_args()

#     extract_tracks_from_path(args.input, args.output)

import os
import re
import csv
from bs4 import BeautifulSoup
from typing import List, Tuple, Union

def extract_tracks_from_html(html_content: str, source_file: str) -> List[Tuple[str, str, str]]:
    """
    Extracts (title, artist, source_file) tuples from the provided HTML content.
    This version handles both 1001Tracklists and Beatport-style HTML formats.
    """
    soup = BeautifulSoup(html_content, "html.parser")
    tracks = []

    # Try 1001Tracklists format
    tl_rows = soup.select(".trackFormat__text")
    if tl_rows:
        for row in tl_rows:
            text = row.get_text(strip=True)
            if " - " in text:
                artist, title = text.split(" - ", 1)
                tracks.append((title.strip(), artist.strip(), source_file))
        return tracks

    # Try Beatport-style format with metadata in <meta> tags
    tl_divs = soup.select("div[itemtype='http://schema.org/MusicRecording']")
    for div in tl_divs:
        title = div.find("meta", {"itemprop": "name"})
        artist = div.find("meta", {"itemprop": "byArtist"})
        if title and artist:
            title_text = title.get("content", "").strip()
            artist_text = artist.get("content", "").strip()
            if title_text and artist_text:
                tracks.append((title_text, artist_text, source_file))

    # Fallback: try parsing visible text blocks with " - "
    all_text = soup.get_text("\n", strip=True)
    for line in all_text.splitlines():
        if " - " in line and len(line.split(" - ")) == 2:
            artist, title = line.split(" - ", 1)
            if 2 < len(title.strip()) < 150:  # crude filter
                tracks.append((title.strip(), artist.strip(), source_file))

    return tracks

def clean_redundant_artist_from_title(tracks: List[Tuple[str, str, str]]) -> List[Tuple[str, str, str]]:
    """
    Remove artist name from beginning of title if duplicated, e.g.:
    title = "DJ Boring - N15", artist = "DJ Boring" → title = "N15"
    """
    cleaned_tracks = []
    for title, artist, source_file in tracks:
        pattern = re.compile(rf"^{re.escape(artist)}\s*[-:|]\s*", re.IGNORECASE)
        cleaned_title = re.sub(pattern, "", title).strip()
        cleaned_tracks.append((cleaned_title, artist, source_file))
    return cleaned_tracks

def extract_tracks_from_path(input_path: Union[str, os.PathLike], output_csv_path: str):
    """
    Process a single HTML file or a folder of HTML files and save (title, artist, source_file) tuples to CSV.
    """
    all_tracks = []
    if os.path.isdir(input_path):
        for file in os.listdir(input_path):
            if file.endswith(".html"):
                with open(os.path.join(input_path, file), "r", encoding="utf-8") as f:
                    html = f.read()
                    tracks = extract_tracks_from_html(html, file)
                    all_tracks.extend(tracks)
    elif input_path.endswith(".html"):
        with open(input_path, "r", encoding="utf-8") as f:
            html = f.read()
            tracks = extract_tracks_from_html(html, os.path.basename(input_path))
            all_tracks.extend(tracks)

    # Clean titles before saving
    all_tracks = clean_redundant_artist_from_title(all_tracks)

    with open(output_csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["title", "artist", "source_file"])
        writer.writerows(all_tracks)

    print(f"✅ Saved {len(all_tracks)} tracks to {output_csv_path}")

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Path to HTML file or folder of HTML files")
    parser.add_argument("--output", default="tracks_output.csv", help="Output CSV file path")
    args = parser.parse_args()

    extract_tracks_from_path(args.input, args.output)


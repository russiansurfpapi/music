import os
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY1"))

def clean_track_name(dirty_title: str) -> str:
    """
    Uses OpenAI to extract the canonical song name from a messy DJ set listing.
    It ignores labels, remix info, catalog codes, and extraneous metadata.
    """

    few_shot_prompt = """You are a music researcher helping clean up DJ tracklist titles.
Extract the canonical track name, ignoring the label (in brackets), remix/version notes, or catalog metadata.

Examples:
Input: "Jeff Mills - The Bells [PURPOSE MAKER]"
Output: The Bells

Input: "Da Hool - Meet Her At The Love Parade [KOSMO]"
Output: Meet Her At The Love Parade

Input: "Ayla - Ayla (Taucher Remix) [POSITIVA]"
Output: Ayla

Input: "Lock 'n Load - Blow Ya Mind (Club Caviar Radio Edit) [SEISMIC]"
Output: Blow Ya Mind

Input: "Darude - Sandstorm [NEO RECORDS / ROBBINS]"
Output: Sandstorm

Input: "Binary Finary - 1998 (Paul van Dyk Remix) [POSITIVA]"
Output: 1998

Input: "Laurent Garnier - Crispy Bacon [F COM]"
Output: Crispy Bacon

Input: "Malin Genie - Lust Crazed Muck Men [VIGENERE]"
Output: Lust Crazed Muck Men

Input: "Renato Figoli - Ocho Al Puma [GUMPTION RECORDINGS]"
Output: Ocho Al Puma

Input: "Eddie Fowkes - Forever [REKIDS]"
Output: Forever

Input: "The Prodigy - Charly [XL]"
Output: Charly

Input: "Camisra - Let Me Show You [ALTRA MODA]"
Output: Let Me Show You

Input: "Mathew Jonson - Panna Cotta [ITISWHATITIS (CLONE)]"
Output: Panna Cotta

Now extract the canonical track name from the following:

Input: "{dirty_title}"
Output:"""

    prompt = few_shot_prompt.format(dirty_title=dirty_title)

    response = client.chat.completions.create(
        model="gpt-4",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
    )

    cleaned = response.choices[0].message.content.strip().strip('"')
    print(f"[🧼] Cleaned track name: '{cleaned}' from '{dirty_title}'")
    return cleaned

def song_chooser(track_name, spotify_track_options, prompt_suffix=""):
    """
    Uses GPT to pick the most likely correct Spotify track given a cleaned title and options.

    Args:
        track_name (str): Cleaned track name.
        spotify_track_options (list): List of Spotify track objects.
        prompt_suffix (str): Optional extra prompt context.

    Returns:
        dict: Chosen Spotify track object.
    """
    options_text = "\n".join([
        f"{i+1}. {t['name']} by {', '.join([a['name'] for a in t['artists']])} (Popularity: {t['popularity']})"
        for i, t in enumerate(spotify_track_options)
    ])

    system_prompt = (
        "You are a music researcher helping select the correct Spotify track based on title and artist.\n"
        "When multiple tracks have the same title, choose the most relevant or popular one.\n"
        "Return ONLY the number of the correct track from the list.\n"
    ) + prompt_suffix

    user_prompt = (
        f"Given the cleaned track title '{track_name}', choose the best match from this list:\n\n"
        f"{options_text}\n\n"
        "Return only the number (e.g., 2) of the correct track."
    )

    try:
        response = client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
        )
        selected_index = int(response.choices[0].message.content.strip()) - 1
        return spotify_track_options[selected_index]
    except Exception as e:
        print(f"[!] Error selecting track: {e}")
        return None

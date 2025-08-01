import os
import sys
import logging
import requests
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

BRIGHTDATA_API_KEY = os.getenv("BRIGHT_DATA_API_KEY")
BRIGHTDATA_ZONE = os.getenv("BRIGHTDATA_ZONE")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def fetch_html(url, save_path="brightdata_tracklist.html"):
    logger.info(f"🌐 Fetching rendered HTML via Bright Data for {url}")
    response = requests.post(
        "https://api.brightdata.com/request",
        headers={"Authorization": f"Bearer {BRIGHTDATA_API_KEY}"},
        json={"zone": BRIGHTDATA_ZONE, "url": url, "format": "raw", "render": True}
    )
    if response.status_code == 200:
        html = response.text
        with open(save_path, "w", encoding="utf-8") as f:
            f.write(html)
        logger.info(f"✅ HTML saved to {save_path}")
        return html
    else:
        logger.error(f"❌ Bright Data fetch failed: {response.status_code}\n{response.text[:300]}")
        return None

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python3 fetch_html.py <1001tracklists_url>")
        sys.exit(1)

    url = sys.argv[1]
    fetch_html(url)

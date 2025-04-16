import requests
from bs4 import BeautifulSoup
from pathlib import Path
import difflib
import json
import time
import logging
from urllib.parse import urlparse, urlunparse
  


# ------------------ CONFIG ------------------

SEARCH_KEYWORDS = ["bike"]
KNOWN_BIKES = ["Trek" ,"Carrera", "Specialised"]
LOCATION_MODE = "edinburgh"  # "edinburgh" or "anywhere"
ENABLE_EBAY = True                 #runs the ebay scraper
ENABLE_GUMTREE = True              #runs the gumtree scraper
CLEAR_CACHE_ON_START = True        #clears the cache, enabling the same matches to be printed
ENABLE_LOGGING = True             #mostly for debugging, explains the process
EBAY_RATIO = 0.2                   #tweak for how strict the search is for ebay
GUMTREE_RATIO = 0.05                #tweak for how strict the search is for gumtree 

CACHE_DIR = Path("~/.cache").expanduser()
CACHE_DIR.mkdir(parents=True, exist_ok=True)
SEEN_EBAY = CACHE_DIR / "seen_ebay.json"
SEEN_GUMTREE = CACHE_DIR / "seen_gumtree.txt"

HEADERS = {                        #the profile we mimic
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-GB,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
}


ebay_matches = []
gumtree_matches = []
# ------------------ LOGGING SETUP ------------------
if ENABLE_LOGGING:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S")
else:
    logging.basicConfig(level=logging.CRITICAL)


# ---------- Shared matching logic -----------

def is_match(title, website=None):
    title = title.lower()
    for known in KNOWN_BIKES:
        ratio = difflib.SequenceMatcher(None, title, known.lower()).ratio()
        RATIO = EBAY_RATIO if website == "ebay" else GUMTREE_RATIO
        if ratio > RATIO:
            return True

    if website == "gumtree":
        for keyword in SEARCH_KEYWORDS:
            if keyword.lower() in title:
                return True

    return False

def is_nearby(location):
    logging.info(f"Location found: {location}")
    if LOCATION_MODE == "anywhere":
        logging.info("Location mode is 'anywhere' — accepting location")
        return True
    return "edinburgh" in location.lower()

def normalize_ebay_url(link):
    parsed = urlparse(link)
    # Remove query and fragment
    return urlunparse(parsed._replace(query="", fragment=""))

# ------------------ eBay --------------------

def load_ebay_seen():
    if SEEN_EBAY.exists():
        with open(SEEN_EBAY, "r") as f:
            return set(json.load(f))
    return set()

def save_ebay_seen(seen):
    with open(SEEN_EBAY, "w") as f:
        json.dump(list(seen), f)

def fetch_ebay_results():
    query = "+".join(SEARCH_KEYWORDS)
    url = "https://www.ebay.co.uk/sch/i.html"
    params = {
        "_nkw": query,
        "_sop": "10",
        "LH_ItemCondition": "3",
        "LH_PrefLoc": "1",
        "_ipg": "100"
    }
    logging.info("Fetching eBay results...")
    res = requests.get(url, params=params, headers=HEADERS)
    res.raise_for_status()
    return res.text

def process_ebay():
    html = fetch_ebay_results()
    soup = BeautifulSoup(html, "html.parser")
    results = soup.select(".s-item")
    logging.info(f"Found {len(results)} eBay listings")

    seen = load_ebay_seen()
    logging.info(f"{len(seen)} previously seen eBay listings")
    new_seen = set(seen)

    for idx, item in enumerate(results):
        logging.info(f"Processing item {idx + 1}/{len(results)}")

        link_tag = item.select_one("a.s-item__link")
        title_tag = item.select_one(".s-item__title")
        loc_tag = item.select_one(".s-item__location")

        if not link_tag or not title_tag:
            logging.info("Missing link or title: skipping")
            continue

        link = normalize_ebay_url(link_tag["href"])
        title = title_tag.text.strip()
        if loc_tag:
            location = loc_tag.text.strip()
        else:
            location = "Unknown"

        if link in seen:
            logging.info("Already seen: skipping")
            continue

        matched = is_match(title, website="ebay")
        located = is_nearby(location)

        if matched and located:
            title = title[11:]
            ebay_matches.append((title, link))

        new_seen.add(link)

    save_ebay_seen(new_seen)
    time.sleep(2.1)

# ---------------- Gumtree -------------------

def load_gumtree_seen():
    if SEEN_GUMTREE.exists():
        with open(SEEN_GUMTREE, "r") as f:
            return set(line.strip() for line in f)
    return set()

def mark_gumtree_seen(url):
    with open(SEEN_GUMTREE, "a") as f:
        f.write(url + "\n")

def fetch_gumtree_results():
    query = "+".join(SEARCH_KEYWORDS)
    base_url = "https://www.gumtree.com/search"
    location = "edinburgh" if LOCATION_MODE == "edinburgh" else "united-kingdom"
    url = f"{base_url}?search_category=bicycles&search_location={location}&q={query}"
    logging.info("Fetching Gumtree results...")
    res = requests.get(url, headers=HEADERS)
    res.raise_for_status()
    return res.text

def parse_gumtree(html):
    soup = BeautifulSoup(html, "html.parser")

    # Gumtree listing links are now usually under <a class="listing-link ...">
    items = soup.find_all("a", href=True)

    logging.info(f"Found {len(items)} <a> tags")
    results = []

    for item in items:
        href = item.get("href")
        title = item.get("aria-label", "").strip()

        if not href:
            continue

        # Look only at actual ad listings
        if not href.startswith("/p/"):
            continue

        full_link = "https://www.gumtree.com" + href
        if not title:
            title = item.text.strip()

        if title and "/p/" in href:
            results.append({
                "title": title,
                "link": full_link
            })

    logging.info(f"Parsed {len(results)} real Gumtree listings")
    return results

def process_gumtree():
    html = fetch_gumtree_results()
    results = parse_gumtree(html)
    seen = load_gumtree_seen()
    logging.info(f"{len(seen)} previously seen Gumtree listings")

    for idx, item in enumerate(results):
        logging.info(f"Processing Gumtree item {idx + 1}/{len(results)}")
        if item["link"] in seen:
            logging.info("Already seen: skipping")
            continue

        if is_match(item["title"], website="gumtree"):
            gumtree_matches.append((item["title"], item["link"]))

        mark_gumtree_seen(item["link"])
        time.sleep(2.1)

# ------------------ Main --------------------

def clear_cache():
    if SEEN_EBAY.exists():
        SEEN_EBAY.unlink()
        logging.info("Cleared eBay cache")
    if SEEN_GUMTREE.exists():
        SEEN_GUMTREE.unlink()
        logging.info("Cleared Gumtree cache")

def main():
    logging.info("Starting bike scraper...")

    if CLEAR_CACHE_ON_START:
        logging.info("CLEAR_CACHE_ON_START is True — clearing caches")
        clear_cache()

    if ENABLE_EBAY:
        process_ebay()
    if ENABLE_GUMTREE:
        process_gumtree()
    if ebay_matches:
        print(f"All ebay matches!")
    for title, link in ebay_matches:
        print(title)
        print(link + "\n")
    
    if gumtree_matches:
        print(f"All gumtree matches!")
    for title, link in gumtree_matches:
        print(title)
        print(link + "\n")
    logging.info("Scraper finished!")

if __name__ == "__main__":
    main()
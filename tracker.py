import csv
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
CSV_PATH = ROOT / "data" / "price_history.csv"

HEADERS = [
    "Timestamp UTC",
    "Product",
    "Target weight",
    "Page product name",
    "Price INR",
    "Availability",
    "Status",
    "Product URL",
]

REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; AgriPriceTracker/1.0)",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-IN,en;q=0.9",
}


def parse_price(value):
    if value is None:
        return None
    match = re.search(r"\d+(?:,\d{3})*(?:\.\d{1,2})?", str(value))
    if not match:
        return None
    try:
        return float(match.group().replace(",", ""))
    except ValueError:
        return None


def extract_product(html):
    soup = BeautifulSoup(html, "html.parser")
    name = None
    price = None
    availability = ""

    # Try structured product data first.
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            payload = json.loads(script.string or script.get_text())
        except (json.JSONDecodeError, TypeError):
            continue

        objects = payload if isinstance(payload, list) else [payload]
        for item in objects:
            if not isinstance(item, dict):
                continue
            if item.get("@type") == "Product" or "Product" in item.get("@type", []):
                name = name or item.get("name")
                offers = item.get("offers", {})
                if isinstance(offers, list):
                    offers = offers[0] if offers else {}
                if isinstance(offers, dict):
                    price = price or parse_price(offers.get("price"))
                    availability = availability or offers.get("availability", "")

    # Try common page metadata.
    if price is None:
        for selector, attribute in (
            ('meta[itemprop="price"]', "content"),
            ('meta[property="product:price:amount"]', "content"),
        ):
            element = soup.select_one(selector)
            if element:
                price = parse_price(element.get(attribute))
                if price is not None:
                    break

    if not name:
        element = soup.select_one('meta[property="og:title"]') or soup.select_one("h1")
        if element:
            name = element.get("content") or element.get_text(" ", strip=True)

    # Fallback to a rupee price visible in the page HTML.
    if price is None:
        match = re.search(r"(?:₹|INR)\s*([0-9,]+(?:\.\d{1,2})?)", html)
        if match:
            price = parse_price(match.group(1))

    return name or "", price, availability


def fetch_product(url):
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in {
        "blinkit.com",
        "www.blinkit.com",
    }:
        raise ValueError("URL must be an HTTPS Blinkit URL")

    response = requests.get(
        url,
        headers=REQUEST_HEADERS,
        timeout=(10, 25),
    )
    response.raise_for_status()
    return extract_product(response.text)


def main():
    with CONFIG_PATH.open(encoding="utf-8") as file:
        config = json.load(file)

    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    file_exists = CSV_PATH.exists() and CSV_PATH.stat().st_size > 0
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")

    with CSV_PATH.open("a", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        if not file_exists:
            writer.writerow(HEADERS)

        for product in config.get("products", []):
            url = str(product.get("product_url", "")).strip()
            page_name, price, availability = "", "", ""
            status = "OK"

            if not url or "PASTE_VERIFIED" in url:
                status = "CONFIGURE PRODUCT URL"
            else:
                try:
                    page_name, fetched_price, availability = fetch_product(url)
                    if fetched_price is None:
                        status = "PRICE NOT FOUND"
                    else:
                        price = fetched_price
                except (requests.RequestException, ValueError) as error:
                    status = f"FETCH ERROR: {str(error)[:150]}"

                # Keep requests sequential and modest.
                time.sleep(2)

            writer.writerow([
                timestamp,
                product.get("name", ""),
                product.get("target_weight", ""),
                page_name,
                price,
                availability,
                status,
                url,
            ])
            print(f"{product.get('name', 'Product')}: {status}")

    print(f"Appended results to {CSV_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()

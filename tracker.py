import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import gspread
import requests
from bs4 import BeautifulSoup
from google.oauth2.service_account import Credentials

CONFIG_PATH = Path(__file__).with_name("config.json")

HEADERS = [
    "Timestamp UTC",
    "Location",
    "PIN code",
    "Product Name",
    "Target Weight",
    "Page Product Name",
    "Price (INR)",
    "Availability",
    "Status",
    "Product URL",
]

HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; AgriPriceTracker/1.0)",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-IN,en;q=0.9",
}

def load_config():
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        config = json.load(f)
    return config

def parse_price(text):
    if text is None:
        return None
    cleaned = re.sub(r"[^0-9.]", "", str(text).replace(",", ""))
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None

def extract_price_and_name(html):
    soup = BeautifulSoup(html, "html.parser")

    # Try meta tags
    price = None
    product_name = None
    availability = None

    meta_price = soup.select_one('meta[itemprop="price"]')
    if meta_price:
        price = parse_price(meta_price.get("content"))

    og_title = soup.select_one('meta[property="og:title"]')
    if og_title:
        product_name = og_title.get("content")

    if not product_name:
        h1 = soup.select_one("h1")
        if h1:
            product_name = h1.get_text(" ", strip=True)

    # Try common text patterns
    if price is None:
        match = re.search(r"₹\\s*([0-9]+(?:,[0-9]{3})*(?:\\.\\d{1,2})?)", html)
        if match:
            price = parse_price(match.group(0))

    # Try text for availability
    if "out of stock" in html.lower():
        availability = "Out of stock"
    elif "in stock" in html.lower():
        availability = "In stock"
    else:
        availability = "Unknown"

    return product_name, price, availability

def fetch_blinkit(url):
    response = requests.get(url, headers=HTTP_HEADERS, timeout=20)
    response.raise_for_status()
    return extract_price_and_name(response.text)

def connect_sheet():
    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    sheet_id = os.environ.get("GOOGLE_SHEET_ID")

    if not creds_json or not sheet_id:
        raise ValueError("Set GOOGLE_CREDENTIALS_JSON and GOOGLE_SHEET_ID in GitHub secrets.")

    info = json.loads(creds_json)
    credentials = Credentials.from_service_account_info(info, scopes=["https://www.googleapis.com/auth/spreadsheets"])
    client = gspread.authorize(credentials)
    spreadsheet = client.open_by_key(sheet_id)
    worksheet = spreadsheet.sheet1

    if not worksheet.get_all_values():
        worksheet.append_row(HEADERS)

    return worksheet

def main():
    config = load_config()
    worksheet = connect_sheet()
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    rows = []

    for location in config["locations"]:
        for product in config["products"]:
            url = product.get("product_url", "").strip()
            status = "OK"
            page_name = ""
            price = ""
            availability = ""

            if not url:
                status = "NO URL"
            else:
                try:
                    page_name, fetched_price, availability = fetch_blinkit(url)
                    if fetched_price is not None:
                        price = fetched_price
                    else:
                        status = "PRICE NOT FOUND"
                except Exception as e:
                    status = f"FETCH ERROR: {str(e)[:150]}"

            rows.append([
                timestamp,
                location.get("name", ""),
                location.get("pin_code", ""),
                product.get("name", ""),
                product.get("target_weight", ""),
                page_name,
                price,
                availability,
                status,
                url,
            ])

            time.sleep(2)

    if rows:
        worksheet.append_rows(rows, value_input_option="USER_ENTERED")

    print(f"Added {len(rows)} rows to Google Sheet.")
    return 0

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Tracker failed: {exc}")
        sys.exit(1)

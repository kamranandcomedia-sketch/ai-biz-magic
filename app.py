# =============================================================================
#  FREE TOOLKIT: Lead Extractor Pro + AI Image Generator
#  Streamlit app for Hugging Face Spaces (free tier, no paid API keys)
# =============================================================================
#
#  Optional Space "Secrets" (Settings -> Variables and secrets):
#    LICENSE_KEY_SHA256   SHA-256 hash of your premium license key. If not set,
#                         the built-in default hash is used.
#    POLLINATIONS_TOKEN   Free token from auth.pollinations.ai. Optional; gives
#                         higher rate limits for the image generator.
# =============================================================================

import hmac
import hashlib
import io
import os
import random
import re
import time
from datetime import datetime
from urllib.parse import quote

import pandas as pd
import requests
import streamlit as st

# -----------------------------------------------------------------------------
# PAGE SETUP
# -----------------------------------------------------------------------------
st.set_page_config(
    page_title="Lead Extractor Pro & Free AI Image Generator",
    page_icon="🚀",
    layout="wide",
)

# Hide Streamlit's default menu, toolbar and footer for a clean, branded look
st.markdown(
    """
    <style>
        #MainMenu, footer, [data-testid="stToolbar"] {visibility: hidden;}
        .hero {padding: 26px 30px; border-radius: 16px; color: #fff; margin-bottom: 18px;
               background: linear-gradient(135deg, #4f46e5, #9333ea);}
        .hero h1 {margin: 0; font-size: 2rem;}
        .hero p {margin: 6px 0 0 0; opacity: .92;}
        .trial {padding: 14px 18px; border-radius: 12px; margin-bottom: 14px;
                background: linear-gradient(135deg, #fef3c7, #fde68a); color: #78350f;
                border: 1px solid #fcd34d;}
        .upgrade {padding: 16px 20px; border-radius: 12px; margin: 14px 0; color: #fff;
                  background: linear-gradient(135deg, #2563eb, #7c3aed);}
    </style>
    <div class="hero">
        <h1>🚀 Free Business Toolkit</h1>
        <p>Find business leads worldwide and generate AI images, all in one place.</p>
    </div>
    """,
    unsafe_allow_html=True,
)

# -----------------------------------------------------------------------------
# SETTINGS
# -----------------------------------------------------------------------------
FREE_LEAD_LIMIT = 20
PREMIUM_LEAD_LIMIT = 3000

# SHA-256 of the license key. Override it with the LICENSE_KEY_SHA256 secret.
DEFAULT_KEY_HASH = "128fd1347a8c028ad28135cfd44c26ddff82913bfc84d21aaa00735c8915fcd6"
KEY_HASH = os.environ.get("LICENSE_KEY_SHA256", DEFAULT_KEY_HASH).strip().lower()

USER_AGENT = "FreeLeadToolkit/1.0 (public business directory research)"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
POLLINATIONS_URL = "https://image.pollinations.ai/prompt/"

# Common keywords mapped to OpenStreetMap tags. Others fall back to a name search.
CATEGORY_MAP = {
    "restaurant": [("amenity", "restaurant")],
    "cafe": [("amenity", "cafe")],
    "coffee": [("amenity", "cafe")],
    "bar": [("amenity", "bar"), ("amenity", "pub")],
    "fast food": [("amenity", "fast_food")],
    "bakery": [("shop", "bakery")],
    "dentist": [("amenity", "dentist"), ("healthcare", "dentist")],
    "doctor": [("amenity", "doctors"), ("healthcare", "doctor")],
    "clinic": [("amenity", "clinic"), ("healthcare", "clinic")],
    "hospital": [("amenity", "hospital")],
    "pharmacy": [("amenity", "pharmacy")],
    "gym": [("leisure", "fitness_centre")],
    "hotel": [("tourism", "hotel")],
    "school": [("amenity", "school")],
    "bank": [("amenity", "bank")],
    "lawyer": [("office", "lawyer")],
    "accountant": [("office", "accountant")],
    "real estate": [("office", "estate_agent")],
    "salon": [("shop", "hairdresser"), ("shop", "beauty")],
    "barber": [("shop", "hairdresser")],
    "supermarket": [("shop", "supermarket")],
    "car repair": [("shop", "car_repair")],
    "mechanic": [("shop", "car_repair")],
    "car dealer": [("shop", "car")],
    "clothing": [("shop", "clothes")],
    "electronics": [("shop", "electronics")],
    "furniture": [("shop", "furniture")],
    "jewelry": [("shop", "jewelry")],
    "travel agency": [("shop", "travel_agency"), ("office", "travel_agent")],
    "veterinary": [("amenity", "veterinary")],
    "laundry": [("shop", "laundry")],
}


# =============================================================================
# TOOL 1: LEAD EXTRACTOR (OpenStreetMap: Nominatim + Overpass)
# =============================================================================
def is_premium(key: str) -> bool:
    """Compare the SHA-256 hash of the entered key with the stored hash."""
    entered = hashlib.sha256(key.strip().encode()).hexdigest()
    return hmac.compare_digest(entered, KEY_HASH)


@st.cache_data(ttl=3600, show_spinner=False)
def geocode(location: str):
    """Turn 'Karachi, Pakistan' into a bounding box (south, west, north, east)."""
    resp = requests.get(
        NOMINATIM_URL,
        params={"q": location, "format": "json", "limit": 1},
        headers={"User-Agent": USER_AGENT},
        timeout=30,
    )
    resp.raise_for_status()
    results = resp.json()
    if not results:
        raise ValueError(f"Could not find '{location}'. Try a city name.")
    south, north, west, east = map(float, results[0]["boundingbox"])
    return south, west, north, east


def build_query(keyword: str, bbox, fetch_limit: int) -> str:
    """Build the Overpass query for the keyword inside the bounding box."""
    s, w, n, e = bbox
    area = f"({s},{w},{n},{e})"
    key = keyword.strip().lower()

    tags = CATEGORY_MAP.get(key)
    if not tags:
        for name, mapped in CATEGORY_MAP.items():
            if name in key or key in name:
                tags = mapped
                break

    lines = []
    if tags:
        for k, v in tags:
            lines.append(f'  nwr["{k}"="{v}"]["name"]{area};')
    safe = re.sub(r'["\\]', "", keyword.strip())  # sanitize user text
    lines.append(f'  nwr["name"~"{safe}",i]{area};')

    body = "\n".join(lines)
    return f"[out:json][timeout:90];\n(\n{body}\n);\nout center tags {fetch_limit};"


@st.cache_data(ttl=3600, show_spinner=False)
def run_overpass(query: str):
    """Send the query to Overpass, trying a backup server if the first fails."""
    last_error = None
    for url in OVERPASS_URLS:
        try:
            resp = requests.post(
                url, data={"data": query}, headers={"User-Agent": USER_AGENT}, timeout=120
            )
            if resp.status_code == 200:
                return resp.json().get("elements", [])
            last_error = f"status {resp.status_code}"
        except Exception as err:
            last_error = str(err)
        time.sleep(2)
    raise RuntimeError(
        f"The map servers are busy or the area is too large ({last_error}). "
        "Try a smaller location such as a city."
    )


def format_address(tags: dict) -> str:
    """Combine address parts into one readable line."""
    if tags.get("addr:full"):
        return tags["addr:full"]
    street = " ".join(filter(None, [tags.get("addr:housenumber"), tags.get("addr:street")]))
    parts = [
        street,
        tags.get("addr:suburb"),
        tags.get("addr:city"),
        tags.get("addr:state"),
        tags.get("addr:postcode"),
        tags.get("addr:country"),
    ]
    return ", ".join(p for p in parts if p)


def extract_leads(keyword: str, location: str, limit: int) -> pd.DataFrame:
    """Return a clean DataFrame of leads (best contact details first)."""
    bbox = geocode(location)
    fetch_limit = min(limit * 3, 9000)  # fetch extra, then keep the best ones
    elements = run_overpass(build_query(keyword, bbox, fetch_limit))

    rows, seen = [], set()
    for el in elements:
        tags = el.get("tags", {})
        name = tags.get("name")
        if not name:
            continue
        phone = tags.get("phone") or tags.get("contact:phone") or tags.get("contact:mobile") or ""
        website = tags.get("website") or tags.get("contact:website") or tags.get("url") or ""
        email = tags.get("email") or tags.get("contact:email") or ""
        address = format_address(tags)

        fingerprint = (name.lower(), address.lower())
        if fingerprint in seen:
            continue
        seen.add(fingerprint)

        rows.append({
            "Business Name": name,
            "Phone Number": phone,
            "Full Address": address,
            "Website": website,
            "Email": email,
        })

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    score = (
        (df["Phone Number"] != "").astype(int)
        + (df["Website"] != "").astype(int)
        + (df["Email"] != "").astype(int)
    )
    df = df.assign(_score=score).sort_values("_score", ascending=False).drop(columns="_score")
    return df.head(limit).reset_index(drop=True)


def to_excel_bytes(df: pd.DataFrame) -> bytes:
    """Convert a DataFrame into an in-memory Excel file."""
    buffer = io.BytesIO()
    df.to_excel(buffer, index=False, engine="openpyxl")
    return buffer.getvalue()


def render_lead_tool():
    st.markdown(
        f"""<div class="trial">🎁 <b>2-Month Free Trial</b>: extract up to
        <b>{FREE_LEAD_LIMIT} leads per search</b> for free.
        Upgrade to <b>Premium</b> for up to <b>{PREMIUM_LEAD_LIMIT:,} leads</b> per search.</div>""",
        unsafe_allow_html=True,
    )

    with st.form("lead_form"):
        license_key = st.text_input(
            "🔑 License Key", type="password", placeholder="Leave empty for the free version"
        )
        col1, col2 = st.columns(2)
        keyword = col1.text_input("🏷️ Industry / Keyword", placeholder="e.g. dentist, restaurant, gym")
        location = col2.text_input("📍 Location / City", placeholder="e.g. Karachi, Pakistan")
        submitted = st.form_submit_button("🔍 Extract Leads", type="primary", use_container_width=True)

    if submitted:
        keyword, location = keyword.strip(), location.strip()
        if not keyword or not location:
            st.warning("Please fill in both the Industry and the Location.")
        elif license_key.strip() and not is_premium(license_key):
            st.error("Invalid license key. Clear the box to use the free version.")
        else:
            premium = bool(license_key.strip())
            limit = PREMIUM_LEAD_LIMIT if premium else FREE_LEAD_LIMIT
            try:
                with st.spinner(f"Searching for '{keyword}' in '{location}'..."):
                    df = extract_leads(keyword, location, limit)
                if df.empty:
                    st.session_state.pop("leads", None)
                    st.info("No businesses found. Try a different keyword or a bigger city.")
                else:
                    st.session_state["leads"] = {
                        "df": df,
                        "csv": df.to_csv(index=False).encode("utf-8-sig"),
                        "xlsx": to_excel_bytes(df),
                        "premium": premium,
                        "stamp": datetime.now().strftime("%Y%m%d_%H%M%S"),
                    }
            except Exception as err:
                st.error(f"Something went wrong: {err}")

    # Results stay on screen (stored in session) so the download clicks don't erase them
    result = st.session_state.get("leads")
    if result:
        df = result["df"]
        st.success(f"✅ Found {len(df)} leads ({'Premium' if result['premium'] else 'Free trial'})")
        st.dataframe(df, use_container_width=True, hide_index=True)

        d1, d2 = st.columns(2)
        d1.download_button(
            "⬇️ Download CSV", result["csv"], file_name=f"leads_{result['stamp']}.csv",
            mime="text/csv", use_container_width=True,
        )
        d2.download_button(
            "⬇️ Download Excel", result["xlsx"], file_name=f"leads_{result['stamp']}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )

        if not result["premium"] and len(df) >= FREE_LEAD_LIMIT:
            st.markdown(
                f"""<div class="upgrade"><b>Free limit reached.</b> Upgrade to Premium to export up to
                {PREMIUM_LEAD_LIMIT:,} leads per search.</div>""",
                unsafe_allow_html=True,
            )


# =============================================================================
# TOOL 2: FREE AI IMAGE GENERATOR (Pollinations)
# =============================================================================
SIZES = {
    "Square (1024 x 1024)": (1024, 1024),
    "Landscape (1344 x 768)": (1344, 768),
    "Portrait (768 x 1344)": (768, 1344),
}


def generate_image(prompt: str, width: int, height: int):
    """Request an image from Pollinations. Returns (image_bytes, content_type)."""
    params = {
        "width": width,
        "height": height,
        "model": "flux",
        "nologo": "true",
        "seed": random.randint(1, 1_000_000),
    }
    headers = {"User-Agent": USER_AGENT}
    token = os.environ.get("POLLINATIONS_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    url = POLLINATIONS_URL + quote(prompt, safe="")
    last_status = None
    for _ in range(3):  # the free tier is rate-limited, so retry politely
        resp = requests.get(url, params=params, headers=headers, timeout=120)
        content_type = resp.headers.get("content-type", "")
        if resp.status_code == 200 and content_type.startswith("image/"):
            return resp.content, content_type
        last_status = resp.status_code
        if resp.status_code == 429:
            time.sleep(16)
            continue
        break
    raise RuntimeError(
        f"The image server is busy right now (status {last_status}). Please try again in a minute."
    )


def render_image_tool():
    st.subheader("🎨 Turn your words into images, free")

    prompt = st.text_area(
        "Describe your image",
        placeholder="e.g. A futuristic cyberpunk city at night, neon lights, rain, ultra detailed",
        max_chars=500,
        height=100,
    )
    size_label = st.selectbox("Image size", list(SIZES.keys()))
    clicked = st.button("✨ Generate Image", type="primary", use_container_width=True)

    if clicked:
        if not prompt.strip():
            st.warning("Please type a prompt first.")
        else:
            width, height = SIZES[size_label]
            try:
                with st.spinner("Creating your image... this can take 10-30 seconds."):
                    img_bytes, content_type = generate_image(prompt.strip(), width, height)
                st.session_state["image"] = {
                    "bytes": img_bytes,
                    "type": content_type,
                    "prompt": prompt.strip(),
                }
            except Exception as err:
                st.error(str(err))

    image = st.session_state.get("image")
    if image:
        st.image(image["bytes"], caption=image["prompt"], use_container_width=True)
        ext = "png" if "png" in image["type"] else "jpg"
        st.download_button(
            "⬇️ Download Image", image["bytes"], file_name=f"ai_image.{ext}",
            mime=image["type"], use_container_width=True,
        )


# =============================================================================
# MAIN LAYOUT
# =============================================================================
tab_leads, tab_images = st.tabs(["📊 Lead Extractor Pro", "🎨 Free AI Image Generator"])

with tab_leads:
    render_lead_tool()

with tab_images:
    render_image_tool()

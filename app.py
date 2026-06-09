import streamlit as st
import requests
from bs4 import BeautifulSoup
from PIL import Image
from io import BytesIO
import json
import zipfile
import re
import uuid

# ─── CONFIG ───────────────────────────────────────────────────────────────
MAX_STICKERS_PER_PACK = 30
STICKER_SIZE = (512, 512)
TRAY_SIZE = (96, 96)
MAX_FILE_SIZE_KB = 100

st.set_page_config(
    page_title="WA Sticker Converter",
    page_icon="🎨",
    layout="centered"
)

# ─── STYLES ───────────────────────────────────────────────────────────────
st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;600;700&display=swap');

    html, body, [class*="css"] {
        font-family: 'Space Grotesk', sans-serif;
    }
    .main .block-container {
        max-width: 800px;
        padding-top: 2rem;
    }
    h1 {
        background: linear-gradient(90deg, #00d2ff, #7b2ff7);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        font-size: 2.4rem !important;
    }
    h3 { color: #a8b2d1 !important; }

    div[data-testid="stFileUploader"] {
        border: 2px dashed #7b2ff7;
        border-radius: 12px;
        padding: 1rem;
        background: rgba(123, 47, 247, 0.05);
    }
    .stButton > button {
        background: linear-gradient(90deg, #7b2ff7, #00d2ff);
        color: white;
        border: none;
        border-radius: 8px;
        padding: 0.6rem 2rem;
        font-weight: 600;
        transition: all 0.3s ease;
    }
    .stButton > button:hover {
        transform: translateY(-2px);
        box-shadow: 0 4px 20px rgba(123, 47, 247, 0.4);
    }
    .stDownloadButton > button {
        background: linear-gradient(90deg, #00d2ff, #7b2ff7);
        color: white;
        border-radius: 8px;
        font-weight: 600;
    }
    .pack-card {
        background: rgba(255,255,255,0.03);
        border: 1px solid rgba(123,47,247,0.3);
        border-radius: 12px;
        padding: 1rem;
        margin: 0.5rem 0;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# ─── HELPER FUNCTIONS ─────────────────────────────────────────────────────

def extract_sticker_urls_from_page(url):
    """Scrape sticker image URLs from stickers.wiki or similar."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    }
    try:
        resp = requests.get(url, headers=headers, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        st.error("Gagal mengakses URL: " + str(e))
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    image_urls = []

    # STRATEGY 1: <img> tags
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src") or img.get("data-lazy-src") or ""
        if not src:
            continue
        if any(ext in src.lower() for ext in [".webp", ".png", ".jpg", ".jpeg"]):
            skip_words = ["logo", "icon", "banner", "avatar", "favicon"]
            if not any(w in src.lower() for w in skip_words):
                if src.startswith("//"):
                    src = "https:" + src
                elif src.startswith("/"):
                    from urllib.parse import urlparse
                    parsed = urlparse(url)
                    src = parsed.scheme + "://" + parsed.netloc + src
                image_urls.append(src)

    # STRATEGY 2: <a> tags linking to images
    for a_tag in soup.find_all("a"):
        href = a_tag.get("href", "")
        if any(ext in href.lower() for ext in [".webp", ".png", ".jpg", ".jpeg"]):
            if href.startswith("//"):
                href = "https:" + href
            elif href.startswith("/"):
                from urllib.parse import urlparse
                parsed = urlparse(url)
                href = parsed.scheme + "://" + parsed.netloc + href
            image_urls.append(href)

    # STRATEGY 3: Background images in style attributes
    for elem in soup.find_all(style=True):
        style_text = elem["style"]
        urls_in_style = re.findall(r'url$$["\']?(.*?)["\']?$$', style_text)
        for u in urls_in_style:
            if any(ext in u.lower() for ext in [".webp", ".png", ".jpg", ".jpeg"]):
                if u.startswith("//"):
                    u = "https:" + u
                image_urls.append(u)

    # STRATEGY 4: JSON / script data
    for script in soup.find_all("script"):
        text = script.string or ""
        json_urls = re.findall(r'https?://[^\s"\'\\]+\.(?:webp|png|jpg|jpeg)', text)
        image_urls.extend(json_urls)

    # Deduplicate
    seen = set()
    unique = []
    for u in image_urls:
        clean = u.split("?")[0]
        if clean not in seen:
            seen.add(clean)
            unique.append(u)

    return unique


def download_image(url, headers):
    """Download image from URL, return PIL Image or None."""
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        img = Image.open(BytesIO(resp.content))
        return img
    except Exception:
        return None


def process_to_sticker(img, size=STICKER_SIZE):
    """Convert image to WhatsApp sticker format (WebP, 512x512, <100KB)."""
    img = img.convert("RGBA")
    img.thumbnail(size, Image.LANCZOS)

    bg = Image.new("RGBA", size, (0, 0, 0, 0))
    offset_x = (size[0] - img.width) // 2
    offset_y = (size[1] - img.height) // 2
    bg.paste(img, (offset_x, offset_y), img)
    img = bg

    for quality in [90, 80, 70, 60, 50, 40, 30]:
        buf = BytesIO()
        img.save(buf, format="WEBP", quality=quality, method=6)
        size_kb = buf.tell() / 1024
        if size_kb <= MAX_FILE_SIZE_KB:
            return buf.getvalue()

    buf = BytesIO()
    img.save(buf, format="WEBP", quality=30, method=6)
    return buf.getvalue()


def create_tray_icon(img):
    """Create a 96x96 tray icon from the first sticker."""
    img = img.convert("RGBA")
    img.thumbnail(TRAY_SIZE, Image.LANCZOS)

    bg = Image.new("RGBA", TRAY_SIZE, (0, 0, 0, 0))
    offset_x = (TRAY_SIZE[0] - img.width) // 2
    offset_y = (TRAY_SIZE[1] - img.height) // 2
    bg.paste(img, (offset_x, offset_y), img)

    buf = BytesIO()
    bg.save(buf, format="PNG")
    return buf.getvalue()


def create_zip_pack(pack):
    """Create a zip file for a single sticker pack."""
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("tray.png", pack["tray"])

        for i, sticker_data in enumerate(pack["stickers"]):
            fname = "sticker_{:03d}.webp".format(i + 1)
            zf.writestr(fname, sticker_data)

        stickers_list = []
        for j in range(len(pack["stickers"])):
            sfile = "sticker_{:03d}.webp".format(j + 1)
            stickers_list.append({"image_file": sfile, "emojis": [""]})

        contents = {
            "android_play_store_link": "",
            "ios_app_store_link": "",
            "sticker_packs": [
                {
                    "identifier": pack["identifier"],
                    "name": pack["name"],
                    "publisher": "Sticker Converter",
                    "tray_image_file": "tray.png",
                    "image_data_version": "1",
                    "avoid_cache": False,
                    "stickers": stickers_list,
                }
            ],
        }
        zf.writestr("contents.json", json.dumps(contents, indent=2))

    return buf.getvalue()


# ─── SESSION STATE INIT ───────────────────────────────────────────────────
if "processed" not in st.session_state:
    st.session_state.processed = False
if "packs" not in st.session_state:
    st.session_state.packs = []


# ─── MAIN UI ──────────────────────────────────────────────────────────────

st.title("WA Sticker Converter")
st.markdown(
    "Convert sticker packs from **stickers.wiki** to WhatsApp-compatible sticker packs."
)

st.divider()

# ─── STEP 1: Input ───
st.markdown("### Step 1 — Input Sticker")

tab_link, tab_upload = st.tabs(
    ["Dari Link stickers.wiki", "Upload Manual (.webp / .png / .jpg)"]
)

urls_from_link = []
uploaded_files = []

with tab_link:
    st.markdown("Tempel link sticker pack dari [stickers.wiki](https://stickers.wiki):")
    sticker_url = st.text_input(
        "URL sticker pack",
        placeholder="https://stickers.wiki/p/sticker-pack-name/",
        label_visibility="collapsed",
    )
    if sticker_url:
        with st.spinner("Mengambil sticker dari halaman..."):
            urls_from_link = extract_sticker_urls_from_page(sticker_url)

        if urls_from_link:
            st.success("Ditemukan **{}** gambar dari halaman.".format(len(urls_from_link)))
            with st.expander("Preview gambar yang ditemukan", expanded=False):
                cols = st.columns(6)
                for i, u in enumerate(urls_from_link[:18]):
                    with cols[i % 6]:
                        try:
                            st.image(u, width=80)
                        except Exception:
                            st.caption("#" + str(i + 1))
                if len(urls_from_link) > 18:
                    st.caption("...dan {} lainnya".format(len(urls_from_link) - 18))
        else:
            st.warning("Tidak ditemukan gambar. Coba gunakan tab Upload Manual.")

with tab_upload:
    uploaded_files = st.file_uploader(
        "Upload file gambar (bisa multi-select)",
        type=["webp", "png", "jpg", "jpeg"],
        accept_multiple_files=True,
    )
    if uploaded_files:
        st.success("**{}** file diupload.".format(len(uploaded_files)))
        cols = st.columns(6)
        for i, f in enumerate(uploaded_files[:18]):
            with cols[i % 6]:
                st.image(f, width=80)

# ─── STEP 2: Config ───
st.divider()
st.markdown("### Step 2 — Konfigurasi Pack")

col_a, col_b = st.columns(2)
with col_a:
    pack_base_name = st.text_input("Nama Sticker Pack", value="My Sticker Pack")
with col_b:
    publisher_name = st.text_input("Publisher", value="Sticker Converter")

# ─── STEP 3: Process ───
st.divider()

total_sources = len(urls_from_link) + len(uploaded_files)

if total_sources == 0:
    st.info("Masukkan link atau upload gambar terlebih dahulu.")
else:
    st.markdown("### Step 3 — Konversi ({} gambar)".format(total_sources))

    estimated_packs = (total_sources + MAX_STICKERS_PER_PACK - 1) // MAX_STICKERS_PER_PACK
    st.caption(
        "Akan dibagi menjadi **{} pack** (maks {} stiker/pack)".format(
            estimated_packs, MAX_STICKERS_PER_PACK
        )
    )

    if st.button("Konversi Sekarang!", use_container_width=True):
        st.session_state.processed = False
        st.session_state.packs = []

        all_images = []

        # Download from URLs
        if urls_from_link:
            dl_headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36"
                )
            }
            progress = st.progress(0, text="Mengunduh gambar dari URL...")
            for i, url in enumerate(urls_from_link):
                img = download_image(url, dl_headers)
                if img:
                    all_images.append(img)
                progress.progress(
                    (i + 1) / len(urls_from_link),
                    text="Mengunduh {}/{}...".format(i + 1, len(urls_from_link)),
                )
            progress.empty()

        # Process uploaded files
        for f in uploaded_files:
            try:
                img = Image.open(f)
                all_images.append(img)
            except Exception:
                st.warning("Gagal membuka file: " + f.name)

        if not all_images:
            st.error("Tidak ada gambar yang berhasil diproses.")
        else:
            # Convert to stickers
            conv_progress = st.progress(0, text="Mengkonversi ke format WhatsApp...")
            sticker_data_list = []
            for i, img in enumerate(all_images):
                sticker_bytes = process_to_sticker(img)
                sticker_data_list.append(sticker_bytes)
                conv_progress.progress(
                    (i + 1) / len(all_images),
                    text="Mengkonversi {}/{}...".format(i + 1, len(all_images)),
                )
            conv_progress.empty()

            # Split into packs of 30
            num_packs = (len(sticker_data_list) + MAX_STICKERS_PER_PACK - 1) // MAX_STICKERS_PER_PACK
            packs = []

            for pack_idx in range(num_packs):
                start = pack_idx * MAX_STICKERS_PER_PACK
                end = min(start + MAX_STICKERS_PER_PACK, len(sticker_data_list))
                pack_stickers = sticker_data_list[start:end]

                if num_packs > 1:
                    pack_name = pack_base_name + " " + str(pack_idx + 1)
                else:
                    pack_name = pack_base_name

                raw_id = re.sub(r'[^a-z0-9_]', '_', pack_name.lower().strip())
                pack_id = raw_id + "_" + str(uuid.uuid4())[:6]

                first_img = all_images[start]
                tray_data = create_tray_icon(first_img)

                packs.append(
                    {
                        "name": pack_name,
                        "identifier": pack_id,
                        "tray": tray_data,
                        "stickers": pack_stickers,
                        "count": len(pack_stickers),
                    }
                )

            st.session_state.packs = packs
            st.session_state.processed = True
            st.rerun()


# ─── STEP 4: Results ───
if st.session_state.processed and st.session_state.packs:
    st.divider()
    st.markdown("### Hasil Konversi")

    packs = st.session_state.packs

    for pack in packs:
        with st.container():
            st.markdown(
                '<div class="pack-card">'
                '<strong style="font-size:1.1rem;">'
                + pack["name"]
                + "</strong>"
                '<span style="color:#7b2ff7; margin-left:8px;">'
                + str(pack["count"])
                + " stiker</span></div>",
                unsafe_allow_html=True,
            )

            col1, col2, col3 = st.columns([1, 2, 1])
            with col2:
                zip_data = create_zip_pack(pack)
                safe_name = pack["name"].replace(" ", "_")
                st.download_button(
                    label="Download " + pack["name"] + ".zip",
                    data=zip_data,
                    file_name=safe_name + ".zip",
                    mime="application/zip",
                    use_container_width=True,
                    key="dl_" + pack["identifier"],
                )

    # Download all combined
    if len(packs) > 1:
        st.markdown("---")
        combined_buf = BytesIO()
        with zipfile.ZipFile(combined_buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for pack in packs:
                folder = pack["name"].replace(" ", "_")
                zf.writestr(folder + "/tray.png", pack["tray"])

                for i, s in enumerate(pack["stickers"]):
                    fname = folder + "/sticker_{:03d}.webp".format(i + 1)
                    zf.writestr(fname, s)

                stickers_list = []
                for j in range(len(pack["stickers"])):
                    sfile = "sticker_{:03d}.webp".format(j + 1)
                    stickers_list.append({"image_file": sfile, "emojis": [""]})

                contents = {
                    "android_play_store_link": "",
                    "ios_app_store_link": "",
                    "sticker_packs": [
                        {
                            "identifier": pack["identifier"],
                            "name": pack["name"],
                            "publisher": publisher_name,
                            "tray_image_file": "tray.png",
                            "image_data_version": "1",
                            "avoid_cache": False,
                            "stickers": stickers_list,
                        }
                    ],
                }
                zf.writestr(folder + "/contents.json", json.dumps(contents, indent=2))

        st.download_button(
            label="Download Semua Pack (ZIP)",
            data=combined_buf.getvalue(),
            file_name=pack_base_name.replace(" ", "_") + "_ALL.zip",
            mime="application/zip",
            use_container_width=True,
            key="dl_all",
        )


# ─── FOOTER ───────────────────────────────────────────────────────────────
st.divider()
st.markdown(
    """
    <div style="text-align:center; color:#555; font-size:0.85rem; padding:1rem 0;">
        <p><strong>Cara import ke WhatsApp:</strong></p>
        <p>1. Download file ZIP lalu extract</p>
        <p>2. Install
           <a href="https://play.google.com/store/apps/details?id=com.marsvard.stickermakerforwhatsapp"
              style="color:#7b2ff7;">Sticker Maker for WhatsApp</a>
        </p>
        <p>3. Buka app, Create New, pilih folder pack</p>
        <p>4. Tap "Add to WhatsApp"</p>
    </div>
    """,
    unsafe_allow_html=True,
)

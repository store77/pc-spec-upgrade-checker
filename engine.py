import os
import re
import json
import time
import hashlib
from datetime import datetime, timezone
from urllib.parse import quote_plus
import requests
from bs4 import BeautifulSoup

# Konfigurasi dari Environment Variables (GitHub Secrets / Vars)
AMAZON_TAG = os.environ.get("AMAZON_TAG", "yourtag-20").strip()
SITE_URL = os.environ.get("SITE_URL", "https://username.github.io/repo-name").rstrip("/")
REPO_NAME = os.environ.get("GITHUB_REPOSITORY", "user/repo")

# Kunci IndexNow deterministik berdasarkan nama repository
INDEXNOW_KEY = hashlib.md5(f"{REPO_NAME}_{AMAZON_TAG}".encode("utf-8")).hexdigest()

DB_PATH = "data/games_db.json"
DOCS_DIR = "docs"
GAMES_DIR = os.path.join(DOCS_DIR, "games")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}


def slugify(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-") or "game"


def load_db() -> dict:
    if os.path.exists(DB_PATH):
        try:
            with open(DB_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_db(db: dict):
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with open(DB_PATH, "w", encoding="utf-8") as f:
        json.dump(db, f, indent=2, ensure_ascii=False)


def fetch_trending_appids() -> list:
    """Mengambil daftar AppID game PC yang sedang tren, rilis baru, dan top sellers di AS."""
    url = "https://store.steampowered.com/api/featuredcategories?cc=us&l=en"
    appids = []
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        categories = ["top_sellers", "new_releases", "coming_soon", "specials"]
        for cat in categories:
            items = data.get(cat, {}).get("items", [])
            for item in items:
                appid = str(item.get("id", ""))
                if appid and appid not in appids:
                    appids.append(appid)
    except Exception as e:
        print(f"[WARN] Gagal mengambil daftar featured Steam: {e}")
    return appids


def parse_hardware_specs(req_html: str) -> dict:
    """Mengekstrak rekomendasi RAM, Storage, GPU, dan CPU dari HTML spesifikasi Steam."""
    soup = BeautifulSoup(req_html, "html.parser")
    text = soup.get_text(" ", strip=True)

    # Deteksi kapasitas RAM (default 16GB jika tidak terdeteksi spesifik)
    ram_match = re.search(r"(\d+)\s*GB\s*RAM", text, re.IGNORECASE)
    ram_gb = int(ram_match.group(1)) if ram_match else 16
    if ram_gb < 16:
        recommended_ram = "16GB DDR4 3200MHz or DDR5 6000MHz Gaming RAM Kit"
        ram_query = "16GB DDR5 6000MHz CL30 desktop gaming RAM kit"
    else:
        recommended_ram = "32GB (2x16GB) DDR5 6000MHz Dual-Channel Gaming RAM"
        ram_query = "32GB 2x16GB DDR5 6000MHz gaming RAM"

    # Deteksi kebutuhan Storage SSD
    storage_match = re.search(r"(\d+)\s*GB\s*available\s*space", text, re.IGNORECASE)
    storage_gb = int(storage_match.group(1)) if storage_match else 60
    if storage_gb >= 80:
        ssd_title = "2TB PCIe Gen4 NVMe M.2 Internal Gaming SSD (Up to 7400 MB/s)"
        ssd_query = "2TB NVMe M.2 PCIe Gen4 internal gaming SSD"
    else:
        ssd_title = "1TB PCIe Gen4 NVMe M.2 Internal Gaming SSD"
        ssd_query = "1TB NVMe M.2 PCIe 4.0 internal SSD"

    # Deteksi GPU
    gpu_text = "NVIDIA GeForce RTX 4060 / AMD Radeon RX 7600 8GB GDDR6"
    gpu_query = "NVIDIA GeForce RTX 4060 8GB graphics card"
    for line in soup.find_all("li"):
        line_str = line.get_text(" ", strip=True)
        if "graphics:" in line_str.lower() or "video card:" in line_str.lower():
            extracted_gpu = re.sub(r"^(Graphics|Video Card):\s*", "", line_str, flags=re.IGNORECASE).strip()
            if len(extracted_gpu) > 5:
                gpu_text = extracted_gpu[:90]
                # Ambil kata kunci GPU utama untuk pencarian Amazon
                gpu_clean = re.sub(r"[^a-zA-Z0-9\s]", " ", extracted_gpu).split()
                gpu_query = " ".join(gpu_clean[:6]) + " graphics card"
            break

    # Deteksi CPU
    cpu_text = "AMD Ryzen 5 7600X / Intel Core i5-13600K"
    cpu_query = "AMD Ryzen 7 7800X3D processor"
    for line in soup.find_all("li"):
        line_str = line.get_text(" ", strip=True)
        if "processor:" in line_str.lower() or "cpu:" in line_str.lower():
            extracted_cpu = re.sub(r"^(Processor|CPU):\s*", "", line_str, flags=re.IGNORECASE).strip()
            if len(extracted_cpu) > 5:
                cpu_text = extracted_cpu[:90]
                cpu_clean = re.sub(r"[^a-zA-Z0-9\s]", " ", extracted_cpu).split()
                cpu_query = " ".join(cpu_clean[:5]) + " processor"
            break

    return {
        "ram_gb": ram_gb,
        "storage_gb": storage_gb,
        "recommended_ram": recommended_ram,
        "ram_query": ram_query,
        "ssd_title": ssd_title,
        "ssd_query": ssd_query,
        "gpu_text": gpu_text,
        "gpu_query": gpu_query,
        "cpu_text": cpu_text,
        "cpu_query": cpu_query,
        "clean_html": str(soup)
    }


def make_amazon_url(search_query: str) -> str:
    encoded = quote_plus(search_query)
    return f"https://www.amazon.com/s?k={encoded}&tag={AMAZON_TAG}"


def fetch_game_details(appid: str) -> dict | None:
    url = f"https://store.steampowered.com/api/appdetails?appids={appid}&cc=us&l=en"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        payload = resp.json().get(str(appid), {})
        if not payload.get("success"):
            return None
        data = payload.get("data", {})
        if data.get("type") != "game":
            return None

        name = data.get("name", "").strip()
        if not name:
            return None

        pc_req = data.get("pc_requirements", {})
        if not isinstance(pc_req, dict):
            return None

        min_html = pc_req.get("minimum", "")
        rec_html = pc_req.get("recommended", min_html)
        if not min_html and not rec_html:
            return None

        combined_req = f"{min_html} {rec_html}"
        specs = parse_hardware_specs(combined_req)

        genres = [g.get("description", "") for g in data.get("genres", []) if g.get("description")]
        release_date = data.get("release_date", {}).get("date", "Available Now")
        header_image = data.get("header_image", "")
        short_desc = BeautifulSoup(data.get("short_description", ""), "html.parser").get_text(" ", strip=True)
        controller_support = data.get("controller_support", "none")

        slug = f"{slugify(name)}-pc-system-requirements-hardware-upgrade"

        return {
            "appid": appid,
            "name": name,
            "slug": slug,
            "header_image": header_image,
            "short_desc": short_desc,
            "genres": genres[:4],
            "release_date": release_date,
            "controller_support": controller_support,
            "min_html": min_html,
            "rec_html": rec_html,
            "specs": specs,
            "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d")
        }
    except Exception as e:
        print(f"[WARN] Gagal memproses AppID {appid}: {e}")
        return None


def build_game_page(game: dict):
    os.makedirs(GAMES_DIR, exist_ok=True)
    specs = game["specs"]
    name = game["name"]
    page_url = f"{SITE_URL}/games/{game['slug']}.html"

    # Buat daftar kartu produk Amazon sesuai spesifikasi game
    products = [
        {
            "category": "Recommended GPU Upgrade",
            "badge": "High FPS 1080p / 1440p",
            "title": specs["gpu_text"],
            "desc": f"Matches or exceeds the official GPU requirement to run {name} smoothly without frame drops or stuttering.",
            "url": make_amazon_url(specs["gpu_query"]),
            "cta": "Check GPU Price on Amazon"
        },
        {
            "category": "Fast NVMe M.2 SSD Storage",
            "badge": f"Requires {specs['storage_gb']} GB Free Space",
            "title": specs["ssd_title"],
            "desc": f"Eliminate texture pop-in and slash loading screen times in {name} with a PCIe 4.0 M.2 SSD.",
            "url": make_amazon_url(specs["ssd_query"]),
            "cta": "Check SSD Deals on Amazon"
        },
        {
            "category": "Low-Latency Memory Upgrade",
            "badge": f"Minimum {specs['ram_gb']} GB RAM",
            "title": specs["recommended_ram"],
            "desc": f"Prevent memory bottlenecks and micro-stutters while running {name} alongside Discord and background apps.",
            "url": make_amazon_url(specs["ram_query"]),
            "cta": "View RAM Kits on Amazon"
        },
        {
            "category": "Compatible CPU Upgrade",
            "badge": "Zero Bottleneck Processor",
            "title": specs["cpu_text"],
            "desc": f"High single-core clock speeds tailored for modern physics and AI calculations in {name}.",
            "url": make_amazon_url(specs["cpu_query"]),
            "cta": "Check CPU Price on Amazon"
        }
    ]

    if game.get("controller_support") in ("full", "partial"):
        products.append({
            "category": "Official Controller Support Detected",
            "badge": "Hall-Effect Zero Stick Drift",
            "title": "Wireless Hall-Effect PC Gaming Controller (2.4GHz Dongle + Bluetooth)",
            "desc": f"{name} features built-in controller support. Upgrade to Hall-Effect magnetic sensors for zero stick drift.",
            "url": make_amazon_url("wireless hall effect PC gaming controller 2.4ghz"),
            "cta": "See Best PC Controllers on Amazon"
        })
    else:
        products.append({
            "category": "Precision Gaming Peripheral",
            "badge": "Ultra-Lightweight Optical Sensor",
            "title": "Wireless 26,000 DPI Lightweight Esports Gaming Mouse",
            "desc": f"Built for low click latency and accurate tracking in {name}.",
            "url": make_amazon_url("wireless lightweight gaming mouse 26000 dpi"),
            "cta": "See Top Gaming Mice on Amazon"
        })

    cards_html = ""
    for prod in products:
        cards_html += f"""
        <div class="card">
            <div class="card-header">
                <span class="category">{prod['category']}</span>
                <span class="badge">{prod['badge']}</span>
            </div>
            <h3>{prod['title']}</h3>
            <p>{prod['desc']}</p>
            <a href="{prod['url']}" target="_blank" rel="nofollow sponsored noopener" class="btn">{prod['cta']} &rarr;</a>
        </div>
        """

    faq_schema = {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {
                "@type": "Question",
                "name": f"How much RAM do I need to run {name} on PC?",
                "acceptedAnswer": {
                    "@type": "Answer",
                    "text": f"{name} requires at least {specs['ram_gb']} GB of system memory. Upgrading to {specs['recommended_ram']} ensures smooth frame pacing."
                }
            },
            {
                "@type": "Question",
                "name": f"How much storage space does {name} take on PC?",
                "acceptedAnswer": {
                    "@type": "Answer",
                    "text": f"{name} requires {specs['storage_gb']} GB of available storage space. Installing the game on an NVMe M.2 SSD is strongly recommended for faster asset streaming."
                }
            },
            {
                "@type": "Question",
                "name": f"What graphics card is recommended for {name}?",
                "acceptedAnswer": {
                    "@type": "Answer",
                    "text": f"The recommended GPU specification for {name} is {specs['gpu_text']} alongside a {specs['cpu_text']} processor."
                }
            }
        ]
    }

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{name} PC System Requirements & Best Hardware Upgrades ({game['updated_at'][:4]})</title>
    <meta name="description" content="Can your PC run {name}? Complete minimum & recommended specs, RAM ({specs['ram_gb']}GB), SSD ({specs['storage_gb']}GB) storage requirements, and compatible GPU upgrade links.">
    <link rel="canonical" href="{page_url}">
    <meta property="og:title" content="{name} PC System Requirements & Hardware Upgrade Guide">
    <meta property="og:description" content="Official PC specs, GPU benchmarks, RAM requirements, and SSD upgrade matches for {name}.">
    <meta property="og:image" content="{game['header_image']}">
    <meta property="og:url" content="{page_url}">
    <meta property="og:type" content="article">
    <script type="application/ld+json">{json.dumps(faq_schema)}</script>
    <style>
        :root {{ --bg: #0f172a; --card: #1e293b; --text: #f8fafc; --muted: #94a3b8; --accent: #f59e0b; --accent-hover: #d97706; --border: #334155; }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: var(--bg); color: var(--text); line-height: 1.6; padding-bottom: 60px; }}
        header {{ background: #020617; border-bottom: 1px solid var(--border); padding: 18px 24px; }}
        .nav-container {{ max-width: 1080px; margin: 0 auto; display: flex; justify-content: space-between; align-items: center; }}
        .brand {{ color: var(--text); text-decoration: none; font-weight: 800; font-size: 1.2rem; letter-spacing: -0.5px; }}
        .brand span {{ color: var(--accent); }}
        .container {{ max-width: 1080px; margin: 32px auto; padding: 0 20px; }}
        .hero {{ display: grid; grid-template-columns: 320px 1fr; gap: 28px; background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 24px; margin-bottom: 32px; }}
        .hero img {{ width: 100%; border-radius: 8px; object-fit: cover; }}
        .hero h1 {{ font-size: 1.85rem; margin-bottom: 12px; line-height: 1.25; }}
        .meta-tags {{ display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 14px; }}
        .tag {{ background: #0f172a; border: 1px solid var(--border); color: var(--muted); font-size: 0.8rem; padding: 4px 10px; border-radius: 6px; }}
        .section-title {{ font-size: 1.45rem; margin: 32px 0 16px; border-left: 4px solid var(--accent); padding-left: 12px; }}
        .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 20px; margin-bottom: 36px; }}
        .card {{ background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 22px; display: flex; flex-direction: column; justify-content: space-between; }}
        .card-header {{ display: flex; justify-content: space-between; align-items: center; gap: 8px; margin-bottom: 12px; flex-wrap: wrap; }}
        .category {{ font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.5px; color: var(--accent); font-weight: 700; }}
        .badge {{ background: #020617; color: #38bdf8; font-size: 0.75rem; padding: 3px 8px; border-radius: 4px; border: 1px solid #0369a1; }}
        .card h3 {{ font-size: 1.1rem; margin-bottom: 10px; color: #ffffff; }}
        .card p {{ color: var(--muted); font-size: 0.92rem; margin-bottom: 18px; }}
        .btn {{ display: inline-block; text-align: center; background: var(--accent); color: #020617; font-weight: 700; text-decoration: none; padding: 12px 16px; border-radius: 8px; transition: background 0.15s ease; }}
        .btn:hover {{ background: var(--accent-hover); }}
        .specs-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 20px; margin-bottom: 36px; }}
        .spec-box {{ background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 22px; }}
        .spec-box h3 {{ margin-bottom: 14px; color: var(--accent); font-size: 1.15rem; }}
        .spec-box ul {{ list-style: none; }}
        .spec-box li {{ margin-bottom: 8px; color: #cbd5e1; font-size: 0.93rem; }}
        .faq-box {{ background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 22px; margin-bottom: 16px; }}
        .faq-box h3 {{ font-size: 1.08rem; margin-bottom: 8px; color: #fff; }}
        .faq-box p {{ color: var(--muted); font-size: 0.94rem; }}
        footer {{ max-width: 1080px; margin: 48px auto 0; padding: 24px 20px 0; border-top: 1px solid var(--border); color: var(--muted); font-size: 0.82rem; text-align: center; }}
        @media (max-width: 720px) {{ .hero {{ grid-template-columns: 1fr; }} }}
    </style>
</head>
<body>
    <header>
        <div class="nav-container">
            <a href="{SITE_URL}/" class="brand">PC<span>SpecMatch</span></a>
            <a href="{SITE_URL}/" style="color: var(--muted); text-decoration: none; font-size: 0.9rem;">&larr; All Game Benchmarks</a>
        </div>
    </header>
    <main class="container">
        <section class="hero">
            <img src="{game['header_image']}" alt="{name} PC System Requirements" loading="eager">
            <div>
                <div class="meta-tags">
                    <span class="tag">Release: {game['release_date']}</span>
                    <span class="tag">RAM Required: {specs['ram_gb']} GB</span>
                    <span class="tag">Storage: {specs['storage_gb']} GB</span>
                    {"".join(f'<span class="tag">{g}</span>' for g in game['genres'])}
                </div>
                <h1>{name} PC System Requirements & Compatible Hardware Upgrades</h1>
                <p style="color: var(--muted); font-size: 0.96rem;">{game['short_desc']}</p>
            </div>
        </section>

        <h2 class="section-title">Direct Compatible Hardware Upgrades for {name}</h2>
        <div class="grid">
            {cards_html}
        </div>

        <h2 class="section-title">Official {name} Minimum & Recommended PC Specs</h2>
        <div class="specs-grid">
            <div class="spec-box">
                <h3>Minimum System Requirements</h3>
                <div>{game['min_html'] or '<p>Standard x64 dual-core CPU, 8GB RAM, DirectX 11 GPU.</p>'}</div>
            </div>
            <div class="spec-box">
                <h3>Recommended System Requirements</h3>
                <div>{game['rec_html'] or game['min_html']}</div>
            </div>
        </div>

        <h2 class="section-title">Frequently Asked Hardware Questions</h2>
        <div class="faq-box">
            <h3>How much RAM do I need to run {name} on PC?</h3>
            <p>{name} requires at least {specs['ram_gb']} GB of RAM. For stutter-free multitasking and stable 1% low framerates, upgrading to a <a href="{make_amazon_url(specs['ram_query'])}" target="_blank" rel="nofollow sponsored noopener" style="color: var(--accent);">{specs['recommended_ram']}</a> is recommended.</p>
        </div>
        <div class="faq-box">
            <h3>Does {name} require an SSD?</h3>
            <p>{name} requires {specs['storage_gb']} GB of free disk space. Installing it on a <a href="{make_amazon_url(specs['ssd_query'])}" target="_blank" rel="nofollow sponsored noopener" style="color: var(--accent);">{specs['ssd_title']}</a> dramatically reduces loading times and prevents asset hitching.</p>
        </div>
        <div class="faq-box">
            <h3>What GPU do I need for 60+ FPS in {name}?</h3>
            <p>The listed graphics card specification is {specs['gpu_text']}. You can compare current pricing and stock for compatible cards <a href="{make_amazon_url(specs['gpu_query'])}" target="_blank" rel="nofollow sponsored noopener" style="color: var(--accent);">directly on Amazon here</a>.</p>
        </div>
    </main>
    <footer>
        <p>As an Amazon Associate, this site earns from qualifying purchases. Game data and system requirements are automatically synchronized with official release specifications. Last verified: {game['updated_at']}.</p>
    </footer>
</body>
</html>"""

    filepath = os.path.join(GAMES_DIR, f"{game['slug']}.html")
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(html)


def build_index_and_seo_files(db: dict):
    os.makedirs(DOCS_DIR, exist_ok=True)
    games_sorted = sorted(db.values(), key=lambda x: x.get("updated_at", ""), reverse=True)

    items_html = ""
    for g in games_sorted:
        specs = g["specs"]
        items_html += f"""
        <article class="game-card">
            <a href="{SITE_URL}/games/{g['slug']}.html" style="text-decoration: none; color: inherit;">
                <img src="{g['header_image']}" alt="{g['name']}" loading="lazy">
                <div class="game-card-body">
                    <h2>{g['name']}</h2>
                    <p class="specs-pill">RAM: {specs['ram_gb']}GB &bull; SSD: {specs['storage_gb']}GB</p>
                    <span class="view-link">View Spec Match & Upgrades &rarr;</span>
                </div>
            </a>
        </article>
        """

    index_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>PCSpecMatch - Automated PC Game System Requirements & Hardware Upgrade Finder</title>
    <meta name="description" content="Check official PC system requirements, RAM, SSD storage sizes, and exact GPU hardware upgrade links for the latest Steam PC games.">
    <link rel="canonical" href="{SITE_URL}/">
    <style>
        :root {{ --bg: #0f172a; --card: #1e293b; --text: #f8fafc; --muted: #94a3b8; --accent: #f59e0b; --border: #334155; }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: var(--bg); color: var(--text); line-height: 1.6; }}
        header {{ background: #020617; border-bottom: 1px solid var(--border); padding: 28px 20px; text-align: center; }}
        header h1 {{ font-size: 2rem; margin-bottom: 8px; }}
        header h1 span {{ color: var(--accent); }}
        header p {{ color: var(--muted); max-width: 680px; margin: 0 auto; }}
        .search-wrap {{ max-width: 560px; margin: 20px auto 0; }}
        #searchInput {{ width: 100%; padding: 12px 16px; border-radius: 8px; border: 1px solid var(--border); background: #0f172a; color: #fff; font-size: 1rem; }}
        .container {{ max-width: 1120px; margin: 36px auto; padding: 0 20px; }}
        .grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 22px; }}
        .game-card {{ background: var(--card); border: 1px solid var(--border); border-radius: 12px; overflow: hidden; transition: transform 0.15s ease; }}
        .game-card:hover {{ transform: translateY(-4px); border-color: var(--accent); }}
        .game-card img {{ width: 100%; height: 150px; object-fit: cover; display: block; }}
        .game-card-body {{ padding: 16px; }}
        .game-card h2 {{ font-size: 1.05rem; margin-bottom: 8px; color: #fff; }}
        .specs-pill {{ font-size: 0.83rem; color: var(--muted); margin-bottom: 12px; }}
        .view-link {{ font-size: 0.88rem; color: var(--accent); font-weight: 700; }}
        footer {{ text-align: center; padding: 40px 20px; color: var(--muted); font-size: 0.82rem; border-top: 1px solid var(--border); margin-top: 60px; }}
    </style>
</head>
<body>
    <header>
        <h1>PC<span>SpecMatch</span> Hardware Database</h1>
        <p>Instant PC System Requirements, RAM/SSD Capacities & Compatible Hardware Upgrade Links for Trending PC Games</p>
        <div class="search-wrap">
            <input type="text" id="searchInput" placeholder="Search game title (e.g. Cyberpunk, Elden Ring, GTA)..." onkeyup="filterGames()">
        </div>
    </header>
    <main class="container">
        <div class="grid" id="gamesGrid">
            {items_html}
        </div>
    </main>
    <footer>
        <p>As an Amazon Associate, this site earns from qualifying purchases. Updated automatically every 24 hours.</p>
    </footer>
    <script>
        function filterGames() {{
            const q = document.getElementById('searchInput').value.toLowerCase();
            const cards = document.querySelectorAll('.game-card');
            cards.forEach(card => {{
                const text = card.innerText.toLowerCase();
                card.style.display = text.includes(q) ? '' : 'none';
            }});
        }}
    </script>
</body>
</html>"""

    with open(os.path.join(DOCS_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(index_html)

    # Buat Sitemap XML
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    urls_xml = f"""  <url>
    <loc>{SITE_URL}/</loc>
    <lastmod>{today}</lastmod>
    <changefreq>daily</changefreq>
    <priority>1.0</priority>
  </url>\n"""

    for g in games_sorted:
        urls_xml += f"""  <url>
    <loc>{SITE_URL}/games/{g['slug']}.html</loc>
    <lastmod>{g.get('updated_at', today)}</lastmod>
    <changefreq>weekly</changefreq>
    <priority>0.8</priority>
  </url>\n"""

    sitemap_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
{urls_xml}</urlset>"""

    with open(os.path.join(DOCS_DIR, "sitemap.xml"), "w", encoding="utf-8") as f:
        f.write(sitemap_content)

    # Buat robots.txt
    robots_content = f"User-agent: *\nAllow: /\nSitemap: {SITE_URL}/sitemap.xml\n"
    with open(os.path.join(DOCS_DIR, "robots.txt"), "w", encoding="utf-8") as f:
        f.write(robots_content)

    # Buat file verifikasi IndexNow
    with open(os.path.join(DOCS_DIR, f"{INDEXNOW_KEY}.txt"), "w", encoding="utf-8") as f:
        f.write(INDEXNOW_KEY)


def ping_indexnow(new_urls: list):
    """Mengirim ping otomatis ke IndexNow API agar mesin pencari langsung mengindeks URL baru."""
    if not new_urls or "username.github.io" in SITE_URL:
        return
    host = SITE_URL.replace("https://", "").replace("http://", "").split("/")[0]
    payload = {
        "host": host,
        "key": INDEXNOW_KEY,
        "keyLocation": f"{SITE_URL}/{INDEXNOW_KEY}.txt",
        "urlList": new_urls
    }
    try:
        r = requests.post(
            "https://api.indexnow.org/indexnow",
            json=payload,
            headers={"Content-Type": "application/json; charset=utf-8"},
            timeout=15
        )
        print(f"[INFO] IndexNow Ping Status: {r.status_code}")
    except Exception as e:
        print(f"[WARN] IndexNow Ping Gagal: {e}")


def main():
    db = load_db()
    appids = fetch_trending_appids()
    print(f"[INFO] Ditemukan {len(appids)} kandidat game dari Steam API.")

    new_urls = []
    processed_count = 0
    # Batasi maksimal 15 game baru per eksekusi harian agar aman dari rate-limit dan bertumbuh alami di mesin pencari
    for appid in appids:
        if appid in db:
            continue
        if processed_count >= 15:
            break

        details = fetch_game_details(appid)
        if details:
            db[appid] = details
            build_game_page(details)
            new_urls.append(f"{SITE_URL}/games/{details['slug']}.html")
            processed_count += 1
            print(f"[OK] Berhasil membuat halaman: {details['name']}")
        time.sleep(1.5)

    # Pastikan semua game yang sudah ada di DB tetap ter-render di docs/games/
    for appid, game_data in db.items():
        build_game_page(game_data)

    build_index_and_seo_files(db)
    save_db(db)

    if new_urls:
        new_urls.append(f"{SITE_URL}/")
        ping_indexnow(new_urls)
    print(f"[DONE] Total halaman game aktif: {len(db)} | Halaman baru hari ini: {processed_count}")


if __name__ == "__main__":
    main()

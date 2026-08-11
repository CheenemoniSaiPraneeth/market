import asyncio
import json
import re
import random
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

#namaste 
INPUT_FILE = "company.json"
OUTPUT_FILE = "companies_suii.json"

HEADLESS = True
TIMEOUT = 60000
MAX_LINKS_PER_SITE = 300
CONCURRENT_WORKERS = 8

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
    "Mozilla/5.0 (X11; Linux x86_64)"
]

# 🔥 ADDED: extra delay helper
async def human_delay(page):
    await page.wait_for_timeout(random.randint(2000, 5000))


# ======================================================
# LOAD
# ======================================================

def load_companies():
    print(f"[LOAD] Reading input file: {INPUT_FILE}")
    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    companies = []
    for company, details in data.items():
        if isinstance(details, dict) and "press_url" in details:
            companies.append({
                "company": company,
                "press_url": details["press_url"]
            })

    print(f"[LOAD] Total companies loaded: {len(companies)}")
    return companies


# ======================================================
# SCORING
# ======================================================

def score_url(url):
    score = 0
    path = urlparse(url).path

    score += path.count("/") * 2
    score += len(path)

    if re.search(r"/20\d{2}/", path):
        score += 25

    if "-" in path:
        score += 10

    return score


# 🔥 ADDED: Improved Cloudflare handling
async def handle_cloudflare(page, company):
    print(f"[CF] Checking Cloudflare | {company}")

    try:
        content = await page.content()

        if any(x in content.lower() for x in [
            "checking your browser",
            "just a moment",
            "cf-browser-verification",
            "challenge-form"
        ]):
            print(f"[CF] DETECTED | {company} -> waiting...")

            for i in range(10):
                await page.wait_for_timeout(3000)

                # 🔥 ADDED: realistic mouse movement
                for _ in range(3):
                    await page.mouse.move(
                        random.randint(100, 800),
                        random.randint(100, 600),
                        steps=random.randint(5, 20)
                    )

                # 🔥 ADDED: smooth scroll
                await page.evaluate("""
                window.scrollBy({
                    top: Math.floor(Math.random() * 500),
                    behavior: 'smooth'
                });
                """)

                content = await page.content()

                if not any(x in content.lower() for x in [
                    "checking your browser",
                    "just a moment",
                    "challenge-form"
                ]):
                    print(f"[CF] PASSED | {company}")
                    return True

            print(f"[CF] FAILED | {company}")
            return False

        return True

    except Exception as e:
        print(f"[CF] ERROR | {company} | {e}")
        return False


def filter_links(base_url, links):
    print(f"[FILTER] Filtering {len(links)} raw links from: {base_url}")
    base_domain = urlparse(base_url).netloc
    clean = {}

    for url, text in links:
        if not url:
            continue

        if url.lower().endswith(
            (".pdf", ".doc", ".docx", ".png", ".jpg", ".zip")
        ):
            continue

        if urlparse(url).netloc != base_domain:
            continue

        clean[url] = {
            "url": url,
            "anchor_text": text,
            "score": score_url(url)
        }

    results = list(clean.values())
    results.sort(key=lambda x: x["score"], reverse=True)

    print(f"[FILTER] Kept {len(results[:MAX_LINKS_PER_SITE])} candidate links for: {base_url}")
    return results[:MAX_LINKS_PER_SITE]


# ======================================================
# PLAYWRIGHT HARVEST
# ======================================================

async def playwright_harvest(context, company, url, wait_mode="domcontentloaded"):

    print(f"[PLAYWRIGHT] START | Company: {company} | URL: {url} | wait_mode: {wait_mode}")
    collected_links = []
    page = await context.new_page()

    # 🔥 ADDED: stealth script
    await page.add_init_script("""
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

    window.chrome = {
        runtime: {}
    };

    Object.defineProperty(navigator, 'plugins', {
        get: () => [1, 2, 3, 4, 5],
    });

    Object.defineProperty(navigator, 'languages', {
        get: () => ['en-US', 'en'],
    });
    """)

    try:
        print(f"[PLAYWRIGHT] GOTO | Company: {company}")
        await page.goto(url, timeout=TIMEOUT, wait_until=wait_mode)

        # existing + your CF
        cf_ok = await handle_cloudflare(page, company)
        if not cf_ok:
            raise Exception("Cloudflare block not bypassed")

        print(f"[PLAYWRIGHT] LOADED | Company: {company}")

        await page.wait_for_timeout(3000)

        # 🔥 ADDED: extra human delay
        await human_delay(page)

        print(f"[PLAYWRIGHT] WAITED 3s | Company: {company}")

        for i in range(4):
            print(f"[PLAYWRIGHT] SCROLL {i+1}/4 | Company: {company}")
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(1200)

        print(f"[PLAYWRIGHT] EXTRACT HTML | Company: {company}")
        html = await page.content()
        soup = BeautifulSoup(html, "lxml")

        for a in soup.find_all("a", href=True):
            full = urljoin(url, a["href"])
            text = a.get_text(strip=True)
            collected_links.append((full, text))

        print(f"[PLAYWRIGHT] RAW LINKS FOUND: {len(collected_links)} | Company: {company}")

        await page.close()
        print(f"[PLAYWRIGHT] PAGE CLOSED | Company: {company}")

        filtered = filter_links(url, collected_links)
        print(f"[PLAYWRIGHT] FINAL LINKS: {len(filtered)} | Company: {company}")

        return filtered

    except Exception as e:
        print(f"[PLAYWRIGHT] ERROR | Company: {company} | URL: {url} | Error: {e}")
        await page.close()
        print(f"[PLAYWRIGHT] PAGE CLOSED AFTER ERROR | Company: {company}")
        raise e


# ======================================================
# STATIC FALLBACK
# ======================================================

def static_harvest(url):
    print(f"[STATIC] START | URL: {url}")
    try:
        r = requests.get(url, headers={"User-Agent": random.choice(USER_AGENTS)}, timeout=20)
        print(f"[STATIC] STATUS: {r.status_code} | URL: {url}")

        soup = BeautifulSoup(r.text, "lxml")

        collected_links = []
        for a in soup.find_all("a", href=True):
            full = urljoin(url, a["href"])
            text = a.get_text(strip=True)
            collected_links.append((full, text))

        print(f"[STATIC] RAW LINKS FOUND: {len(collected_links)} | URL: {url}")
        filtered = filter_links(url, collected_links)
        print(f"[STATIC] FINAL LINKS: {len(filtered)} | URL: {url}")

        return filtered
    except Exception as e:
        print(f"[STATIC] ERROR | URL: {url} | Error: {e}")
        return []


# ======================================================
# MAIN ADAPTIVE PIPELINE
# ======================================================

async def main():

    print("[MAIN] Starting adaptive harvesting pipeline")
    companies = load_companies()

    retry_queue = []
    final_results = []

    semaphore = asyncio.Semaphore(CONCURRENT_WORKERS)

    async with async_playwright() as p:

        print("[MAIN] Launching browser")
        browser = await p.chromium.launch(headless=HEADLESS)

        async def worker(entry):
            async with semaphore:
                print(f"[PHASE 1] START | {entry['company']} | {entry['press_url']}")

                # 🔥 ADDED: stronger context (no removal)
                context = await browser.new_context(
                    user_agent=random.choice(USER_AGENTS),
                    viewport={"width": random.randint(1200, 1600), "height": random.randint(800, 1000)},
                    locale="en-US",
                    timezone_id="Asia/Kolkata",
                    java_script_enabled=True
                )

                try:
                    links = await playwright_harvest(
                        context,
                        entry["company"],
                        entry["press_url"]
                    )

                    await context.close()

                    if links:
                        return {"status": "success", "data": links}
                    else:
                        return {"status": "retry"}

                except:
                    await context.close()
                    return {"status": "retry"}

        tasks = [worker(entry) for entry in companies]
        results = await asyncio.gather(*tasks)

        for entry, result in zip(companies, results):
            if result["status"] == "success":
                final_results.append({
                    "company": entry["company"],
                    "press_url": entry["press_url"],
                    "candidate_links": result["data"]
                })
            else:
                retry_queue.append(entry)

        print("\n[PHASE 2] Retrying...\n")

        for entry in retry_queue:

            context = await browser.new_context(
                user_agent=random.choice(USER_AGENTS),
                viewport={"width": random.randint(1200, 1600), "height": random.randint(800, 1000)},
                locale="en-US",
                timezone_id="Asia/Kolkata"
            )

            try:
                links = await playwright_harvest(context, entry["company"], entry["press_url"])

                await context.close()

                if not links:
                    links = static_harvest(entry["press_url"])

            except:
                await context.close()
                links = static_harvest(entry["press_url"])

            final_results.append({
                "company": entry["company"],
                "press_url": entry["press_url"],
                "candidate_links": links
            })

        await browser.close()

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(final_results, f, indent=2, ensure_ascii=False)

    print("DONE:", OUTPUT_FILE)


if __name__ == "__main__":
    asyncio.run(main())

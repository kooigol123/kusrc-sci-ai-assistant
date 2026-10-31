import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import time

BASE_URL = "https://sci.src.ku.ac.th/"
MAX_PAGES = 60  # crawl สูงสุด 60 หน้า

# หน้าสำคัญที่ต้องมีเสมอ (priority pages)
PRIORITY_PAGES = {
    "home":             "https://sci.src.ku.ac.th/",
    "general_info":     "https://sci.src.ku.ac.th/%e0%b8%82%e0%b9%89%e0%b8%ad%e0%b8%a1%e0%b8%b9%e0%b8%a5%e0%b8%97%e0%b8%b1%e0%b9%88%e0%b8%a7%e0%b9%84%e0%b8%9b%e0%b8%82%e0%b8%ad%e0%b8%87%e0%b8%84%e0%b8%93%e0%b8%b0/",
    "faq":              "https://sci.src.ku.ac.th/qa/",
    "student":          "https://sci.src.ku.ac.th/%e0%b8%99%e0%b8%b4%e0%b8%aa%e0%b8%b4%e0%b8%95/",
    "parent":           "https://sci.src.ku.ac.th/%e0%b8%9c%e0%b8%b9%e0%b9%89%e0%b8%9b%e0%b8%81%e0%b8%84%e0%b8%a3%e0%b8%ad%e0%b8%87-2-2/",
    "alumni":           "https://sci.src.ku.ac.th/%e0%b8%a8%e0%b8%b4%e0%b8%a9%e0%b8%a2%e0%b9%8c%e0%b9%80%e0%b8%81%e0%b9%88%e0%b8%b2/",
    "curriculum":       "https://sci.src.ku.ac.th/en/program/",
    "cs_program":       "https://sci.src.ku.ac.th/program/computer-science/",
    "it_program":       "https://sci.src.ku.ac.th/program/information-technology/",
    "digital_program":  "https://sci.src.ku.ac.th/program/digital-science-and-technology/",
    "env_program":      "https://sci.src.ku.ac.th/program/environmental-science/",
    "special_cs":       "https://sci.src.ku.ac.th/program/special-computer-science/",
    "special_it":       "https://sci.src.ku.ac.th/program/special-information-technology/",
    "special_digital":  "https://sci.src.ku.ac.th/program/special-digital-science-and-technology/",
    "dean":             "https://sci.src.ku.ac.th/%e0%b8%82%e0%b9%89%e0%b8%ad%e0%b8%a1%e0%b8%b9%e0%b8%a5%e0%b8%97%e0%b8%b1%e0%b9%88%e0%b8%a7%e0%b9%84%e0%b8%9b%e0%b8%82%e0%b8%ad%e0%b8%87%e0%b8%84%e0%b8%93%e0%b8%b0/%e0%b8%84%e0%b8%93%e0%b8%9a%e0%b8%94%e0%b8%b5%e0%b9%81%e0%b8%a5%e0%b8%b0%e0%b8%9c%e0%b8%b9%e0%b9%89%e0%b8%9a%e0%b8%a3%e0%b8%b4%e0%b8%ab%e0%b8%b2%e0%b8%a3/",
    "dean_history":     "https://sci.src.ku.ac.th/%e0%b8%82%e0%b9%89%e0%b8%ad%e0%b8%a1%e0%b8%b9%e0%b8%a5%e0%b8%97%e0%b8%b1%e0%b9%88%e0%b8%a7%e0%b9%84%e0%b8%9b%e0%b8%82%e0%b8%ad%e0%b8%87%e0%b8%84%e0%b8%93%e0%b8%b0/%e0%b8%97%e0%b8%b3%e0%b9%80%e0%b8%99%e0%b8%b5%e0%b8%a2%e0%b8%9a%e0%b8%84%e0%b8%93%e0%b8%9a%e0%b8%94%e0%b8%b5/",
    "academic_staff":   "https://sci.src.ku.ac.th/%e0%b8%82%e0%b9%89%e0%b8%ad%e0%b8%a1%e0%b8%b9%e0%b8%a5%e0%b8%97%e0%b8%b1%e0%b9%88%e0%b8%a7%e0%b9%84%e0%b8%9b%e0%b8%82%e0%b8%ad%e0%b8%87%e0%b8%84%e0%b8%93%e0%b8%b0/%e0%b8%9a%e0%b8%b8%e0%b8%84%e0%b8%a5%e0%b8%b2%e0%b8%81%e0%b8%a3%e0%b8%a7%e0%b8%b4%e0%b8%8a%e0%b8%b2%e0%b8%81%e0%b8%b2%e0%b8%a3-2/",
    "support_staff":    "https://sci.src.ku.ac.th/%e0%b8%82%e0%b9%89%e0%b8%ad%e0%b8%a1%e0%b8%b9%e0%b8%a5%e0%b8%97%e0%b8%b1%e0%b9%88%e0%b8%a7%e0%b9%84%e0%b8%9b%e0%b8%82%e0%b8%ad%e0%b8%87%e0%b8%84%e0%b8%93%e0%b8%b0/%e0%b8%9a%e0%b8%b8%e0%b8%84%e0%b8%a5%e0%b8%b2%e0%b8%81%e0%b8%a3%e0%b8%aa%e0%b8%99%e0%b8%b1%e0%b8%9a%e0%b8%aa%e0%b8%99%e0%b8%b8%e0%b8%99/",
    "cs_staff":         "https://sci.src.ku.ac.th/department/computer-science/",
    "it_staff":         "https://sci.src.ku.ac.th/department/digital-science-and-technology/",
    "env_staff":        "https://sci.src.ku.ac.th/department/natural-product-science-technology/",
    "env_master":       "https://sci.src.ku.ac.th/department/natural-product-science-technology-master/",
    "chem_staff":       "https://sci.src.ku.ac.th/department/applied-chemical-science-and-technology/",
    "math_staff":       "https://sci.src.ku.ac.th/department/data-analytics-and-actuarial-science/",
    "physics_staff":    "https://sci.src.ku.ac.th/department/physics/",
    "sports_staff":     "https://sci.src.ku.ac.th/department/sports-science/",
    "award":            "https://sci.src.ku.ac.th/%e0%b8%82%e0%b9%89%e0%b8%ad%e0%b8%a1%e0%b8%b9%e0%b8%a5%e0%b8%97%e0%b8%b1%e0%b9%88%e0%b8%a7%e0%b9%84%e0%b8%9b%e0%b8%82%e0%b8%ad%e0%b8%87%e0%b8%84%e0%b8%93%e0%b8%b0/award/",
    "km":               "https://sci.src.ku.ac.th/knowledgemanagement/",
    "research":         "https://sci.src.ku.ac.th/%e0%b8%a7%e0%b8%b4%e0%b8%88%e0%b8%b1%e0%b8%a2/",
    "news":             "https://sci.src.ku.ac.th/category/news/",
    "admission":        "https://admissions.src.ku.ac.th/",
    "regis":            "https://regis.src.ku.ac.th/",
    "service":          "https://sci.src.ku.ac.th/%e0%b8%9a%e0%b8%a3%e0%b8%b4%e0%b8%81%e0%b8%b2%e0%b8%a3%e0%b8%81%e0%b8%b2%e0%b8%a3%e0%b8%a8%e0%b8%b6%e0%b8%81%e0%b8%a9%e0%b8%b2/",
    "coop":             "https://sci.src.ku.ac.th/%e0%b8%aa%e0%b8%ab%e0%b8%81%e0%b8%b4%e0%b8%88/",
    "scholarship":      "https://sci.src.ku.ac.th/%e0%b8%97%e0%b8%b8%e0%b8%99%e0%b8%81%e0%b8%b2%e0%b8%a3%e0%b8%a8%e0%b8%b6%e0%b8%81%e0%b8%a9%e0%b8%b2/",
    "tuition_fees":     "https://sci.src.ku.ac.th/en/tuition-fees/",
}

# หมวดหมู่ URL patterns
SKIP_PATTERNS = [
    "wp-admin", "wp-login", "wp-json", "xmlrpc",
    "feed", "comment", "attachment", "?replytocom",
    "wp-content/uploads", "sdc_download",
    ".pdf", ".jpg", ".jpeg", ".png", ".gif", ".zip", ".rar",
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    "mailto:", "tel:", "javascript:",
    "facebook.com", "youtube.com", "twitter.com", "instagram.com",
    "page/", "?page_id", "#",
]

def should_skip(url):
    return any(p in url.lower() for p in SKIP_PATTERNS)

def is_same_domain(url):
    parsed = urlparse(url)
    return parsed.scheme in ("http", "https") and parsed.netloc in ("sci.src.ku.ac.th", "admissions.src.ku.ac.th")

def scrape_page(url):
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        # เก็บรายการลิงก์สำคัญในหน้าเว็บและ footer ก่อนตัด tag
        page_resources = []
        seen_res = set()
        for a in soup.find_all("a", href=True):
            label = a.get_text(" ", strip=True)
            full_url = urljoin(url, a["href"]).split("#")[0].strip()
            if label and len(label) > 2 and full_url.startswith(("http://", "https://")):
                if any(d in full_url for d in ("ku.ac.th", "ku.th")) and full_url not in seen_res:
                    seen_res.add(full_url)
                    page_resources.append(f"- {label}: {full_url}")

        for tag in soup(["script", "style", "noscript", "header", "footer", "nav", "aside"]):
            tag.extract()

        main = (
            soup.find("main") or
            soup.find("article") or
            soup.find("div", class_=lambda c: c and any(x in str(c) for x in ["content", "entry", "post-body"])) or
            soup.body
        )

        if not main:
            return "", []

        text = main.get_text(separator="\n", strip=True)
        lines = [l.strip() for l in text.splitlines() if l.strip() and len(l.strip()) > 3]
        text = "\n".join(lines)
        if page_resources:
            text += "\n\nลิงก์ที่เกี่ยวข้องในหน้านี้:\n" + "\n".join(page_resources[:40])

        # เก็บ links ทั้งหมดในหน้านี้
        links = []
        for a in soup.find_all("a", href=True):
            href = urljoin(url, a["href"])
            if is_same_domain(href) and not should_skip(href):
                links.append(href.split("#")[0].rstrip("/") + "/")

        return text, list(set(links))

    except Exception as e:
        print(f"  Error: {e}")
        return "", []


def get_kusrc_data():
    documents = []
    visited = set()
    to_visit = []

    # เริ่มจาก priority pages ก่อนเสมอ
    print(f"[Scraper] Starting with {len(PRIORITY_PAGES)} priority pages...")
    for category, url in PRIORITY_PAGES.items():
        norm = url.rstrip("/") + "/"
        if norm not in visited:
            to_visit.append((norm, category))
            visited.add(norm)

    scraped_count = 0

    while to_visit and scraped_count < MAX_PAGES:
        url, category = to_visit.pop(0)
        print(f"[{scraped_count+1}/{MAX_PAGES}] Scraping [{category}]: {url[:70]}")

        text, found_links = scrape_page(url)

        if text and len(text) > 100:
            documents.append({
                "content":  text[:10000],
                "category": category,
                "source":   url
            })
            print(f"  OK ({len(text)} chars, found {len(found_links)} links)")
            scraped_count += 1

            # เพิ่ม links ที่ค้นพบเข้า queue (ถ้ายังไม่เคย visit)
            for link in found_links:
                if link not in visited and scraped_count + len(to_visit) < MAX_PAGES:
                    visited.add(link)
                    to_visit.append((link, "auto"))
        else:
            print(f"  SKIP (empty or too short)")

        time.sleep(0.8)

    # ── ดึงปฏิทินการศึกษาเพิ่มเติม ──
    print("\n[Scraper] Scraping academic calendar...")
    calendar_docs = scrape_calendar()
    documents.extend(calendar_docs)
    print(f"[Scraper] Calendar: {len(calendar_docs)} tabs scraped")

    print(f"\n[Scraper] Total: {len(documents)} pages scraped")
    return documents


def scrape_calendar():
    """
    ดึงปฏิทินการศึกษาจาก regis.src.ku.ac.th
    รองรับทั้ง 3 แท็บ: ภาคปกติ (Thai), บัณฑิตศึกษา (Graduate), นานาชาติ (International)
    """
    CALENDAR_URL = "https://regis.src.ku.ac.th/res/calender.php"
    TAB_LABELS = {
        "home":    "ภาคปกติ (Thai Program)",
        "profile": "บัณฑิตศึกษา (Graduate)",
        "inter":   "นานาชาติ (International Program)",
    }

    results = []
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        response = requests.get(CALENDAR_URL, headers=headers, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        for tab_id, label in TAB_LABELS.items():
            tab_div = soup.find("div", id=tab_id)
            if not tab_div:
                continue

            rows = tab_div.find_all("tr")
            lines = []
            for row in rows:
                cells = [td.get_text(separator=" ", strip=True) for td in row.find_all(["td", "th"])]
                cells = [c for c in cells if c]
                if cells:
                    lines.append(" | ".join(cells))

            if not lines:
                continue

            content = f"ปฏิทินการศึกษา {label}\nที่มา: {CALENDAR_URL}\n\n"
            content += "\n".join(lines)

            results.append({
                "content":  content[:10000],
                "category": "ปฏิทินการศึกษา",
                "source":   f"{CALENDAR_URL}#{tab_id}",
            })
            print(f"  [Calendar] {label}: {len(lines)} rows")

    except Exception as e:
        print(f"  [Calendar] Error: {e}")

    return results


if __name__ == "__main__":
    data = get_kusrc_data()
    print(f"Done: {len(data)} documents")
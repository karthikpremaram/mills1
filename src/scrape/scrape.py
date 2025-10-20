"""scrape the website content using playwright and craw4ai"""

import re
import os
import re
import math 
from src.jobs.redis_state import set_task_state
from pathlib import Path
from markdownify import markdownify  # cSpell:disable-line
from crawl4ai import AsyncWebCrawler
from playwright.async_api import async_playwright
from langchain_community.document_transformers import Html2TextTransformer
from langchain_core.documents import Document # type: ignore
from src.scrape.llm import refine_with_llm



def clean_text_for_prompt(content):
    html2text = Html2TextTransformer(ignore_links=False)
    doc = Document(page_content=content)
    clean_doc = html2text.transform_documents([doc])[0]
    cleaned_text = clean_doc.page_content.strip()

    return cleaned_text


def clean_text_for_kb(text: str) -> str:
    """Clean crawled markdown text into plain readable text."""

    # 1. Remove image markdown like ![](url)
    text = re.sub(r"!\[.*?\]\(.*?\)", "", text)
    # 2. Replace markdown links [text](url) → keep text only
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    # 3. Remove bare URLs (http/https/www)
    text = re.sub(r"http[s]?://\S+|www\.\S+", "", text)
    # 4. Remove dangling empty () or []
    text = re.sub(r"\(\s*\)|\[\s*\]", "", text)
    # 5. Remove lines with only special chars (*, #, spaces)
    text = re.sub(r"^[\s*#]+$", "", text, flags=re.MULTILINE)
    text = re.sub(r"^!\[\].*\n", "", text)
    # 6. Collapse repeated sections (optional deduplication)
    lines = text.splitlines()
    seen = set()
    deduped = []
    for line in lines:
        line_stripped = line.strip()
        if (line_stripped and line_stripped not in seen) or len(line_stripped) < 2:
            deduped.append(line)
            seen.add(line_stripped)
    text = "\n".join(deduped)
    # 7. Normalize whitespace
    text = re.sub(r"\n\s*\n+", "\n\n", text)  # collapse multiple blank lines
    text = re.sub(r" {2,}", " ", text)  # collapse multiple spaces
    return text.strip()




def get_filename(url: str, output_dir: str) -> str:
    os.makedirs(output_dir, exist_ok=True)
    paths = os.listdir(output_dir)
    if len(paths) == 0:
        return os.path.join(output_dir, "0.md")
    else:
        try:
            path = max([int(x.replace(".md", "")) for x in paths if x.endswith(".md")])
            return os.path.join(output_dir, f"{path + 1}.md")
        except Exception:
            # fallback if no numeric filenames exist
            return os.path.join(output_dir, "100.md")

        

# def get_filename(url: str, output_dir: str) -> str:
#     """
#     Generate the next markdown filename in numeric sequence (e.g., 0.md, 1.md, 2.md).

#     Args:
#         url (str): (Unused) URL input, kept for compatibility.
#         output_dir (str): Path to the output directory.

#     Returns:
#         str: Full path to the next markdown file.
#     """
#     output_path = Path(output_dir)
#     output_path.mkdir(parents=True, exist_ok=True)

#     # Collect all numeric parts from markdown filenames
#     md_files = [f for f in output_path.iterdir() if f.is_file() and f.suffix == ".md"]
#     numbers = [
#         int(match.group(1))
#         for f in md_files
#         if (match := re.search(r"(\d+)", f.stem))
#     ]

#     # Compute the next available number safely
#     next_num = (max(numbers) + 1) if numbers else 0
#     next_file = output_path / f"{next_num}.md"

#     return str(next_file)



def save_file(md, url, output_dir="./markdown_content"):
    """save the markdown file"""
    file_name = get_filename(url, output_dir)
    with open(file_name, "w", encoding="utf-8") as f:
        f.write(md)


def remove_header_footer(html_text: str) -> str:
    """remove headers from the file"""
    return re.sub(r"<(header|footer)[\s\S]*?</\1>", "", html_text, flags=re.I)



async def crawl(cur_url, purpose):
    """Fetch markdown content for a single URL using crawl4ai."""
    try:
        print(f"--> Crawl: Extracting content from {cur_url}")
        excluded_tags = []
        if purpose != "prompt":
            excluded_tags = ["header", "footer"]
        async with AsyncWebCrawler() as crawler:
            result = await crawler.arun(cur_url, excluded_tags=excluded_tags)
            if not result or not result.markdown:
                raise ValueError("No markdown found")
            cleaned = ""
            if purpose == "prompt":
                print("-->cleaning for prompt")
                cleaned = clean_text_for_prompt(result.html)
            else:
                print("--> cleaning and refining for kb")
                cleaned = clean_text_for_kb(result.markdown)
                cleaned = refine_with_llm(cleaned)

            print(f"--> {len(cleaned)} chars extracted")
            return cleaned, result.html
    except Exception as e:
        raise RuntimeError(f"Crawl failed for {cur_url}: {e}") from e



async def playwright(cur_url, purpose):
    """Fetch markdown content for a single URL using playwright."""
    try:
        print(f"-->Playwright: Extracting content from {cur_url}")
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(cur_url, timeout=30000)
            html = await page.content()
            cleaned = ""
            if purpose == "prompt":
                print("--> cleaning for prompt")
                cleaned = clean_text_for_prompt(html)
            else:
                print("--> cleaning and refining for kb")
                cleaned = markdownify(html)
                cleaned = clean_text_for_kb(cleaned)
                cleaned = refine_with_llm(cleaned)
            print(f"--> {len(cleaned)} chars extracted")
            await browser.close()
            return cleaned, html
    except Exception as e:
        raise e


async def scrape(cur_url, purpose):
    md, html = "", ""
    try:
        print(f"-->Trying crawl4ai for {cur_url}")
        md, html = await crawl(cur_url, purpose)
    except Exception as e:
        print(f"-->crawl4ai failed for {cur_url}: {e}")
        try:
            print(f"-->Trying playwright for {cur_url}")
            md, html = await playwright(cur_url, purpose)

        except Exception as e2:
            print(f"-->playwright failed for {cur_url}: {e2}")
            return md, html

    return md, html





async def scrape_urls(
    urls,
    redis=None,
    task_id: str = None,
    step_name: str = None,
    step_weight: int = None,
    purpose="kb",
    output_dir: str = "./markdown_content"
):
    """
    Scrape one or multiple URLs and optionally update progress in Redis.
    Each URL contributes proportionally to the step's weight if provided.
    """
    scraped_content = ""

    if not isinstance(urls, list):
        urls = [urls]

    no_of_links = len(urls)
    per_link_percent = (step_weight / no_of_links) if (step_weight and no_of_links) else 0
    current_percent = 0.0

    for i, url in enumerate(urls, start=1):
        print("--" * 20)
        if no_of_links > 1:
            print(f"Scraping {i}/{no_of_links}: {url}")

        try:
            cleaned_text, _ = await scrape(url, purpose)
            scraped_content += cleaned_text or ""
            save_file(cleaned_text, url, output_dir)

            # ------------------ update progress if redis/task_id provided ------------------
            if redis and task_id and step_name and step_weight:
                current_percent += per_link_percent
                step_progress = min(math.ceil(current_percent), step_weight)
                await set_task_state(redis, task_id, {
                    "current_step": step_name,
                    "percent": step_progress,
                    "details": f"Scraped {i}/{no_of_links} URLs"
                })

        except Exception as e:
            print(f"Error scraping {url}: {e}")
            if redis and task_id:
                await set_task_state(redis, task_id, {
                    "state": "FAILED",
                    "error_message": f"Error scraping {url}: {e}"
                })
            raise

    return scraped_content
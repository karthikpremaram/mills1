"""scrape the website content using playwright and craw4ai"""

import os
import re
import math
from crawl4ai import AsyncWebCrawler
from playwright.async_api import async_playwright
from langchain_community.document_transformers import Html2TextTransformer
from langchain_core.documents import Document  # type: ignore
from markdownify import markdownify  # cSpell:disable-line

from src.scrape.llm import refine_with_llm
from src.logger.logger import logger
from src.jobs.redis_state import set_task_state


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
    """name the file"""
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
        logger.info("Crawl: Extracting content from %s", cur_url)
        excluded_tags = []
        if purpose != "prompt":
            excluded_tags = ["header", "footer"]
        async with AsyncWebCrawler() as crawler:
            result = await crawler.arun(cur_url, excluded_tags=excluded_tags)
            if not result or not result.markdown:
                raise ValueError("No markdown found")
            cleaned = ""
            if purpose == "prompt":
                logger.debug("Processing content for prompt")
                cleaned = clean_text_for_prompt(result.html)
            else:
                logger.debug("Processing content for knowledge base")
                cleaned = clean_text_for_kb(result.markdown)
                cleaned = refine_with_llm(cleaned)

            logger.info("Extracted %d characters from %s", len(cleaned), cur_url)
            return cleaned, result.html
    except Exception as e:
        logger.error("Crawl failed for %s: %s", cur_url, str(e))
        raise RuntimeError(f"Crawl failed for {cur_url}: {e}") from e


async def playwright(cur_url, purpose):
    """Fetch markdown content for a single URL using playwright."""
    try:
        logger.info("Playwright: Extracting content from %s", cur_url)
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(cur_url, timeout=30000)
            html = await page.content()
            cleaned = ""
            if purpose == "prompt":
                logger.debug("Processing content for prompt")
                cleaned = clean_text_for_prompt(html)
            else:
                logger.debug("Processing content for knowledge base")
                cleaned = markdownify(html)
                cleaned = clean_text_for_kb(cleaned)
                cleaned = refine_with_llm(cleaned)
            logger.info("Extracted %d characters from %s", len(cleaned), cur_url)
            await browser.close()
            return cleaned, html
    except Exception as e:
        logger.error("Playwright failed for %s: %s", cur_url, str(e))
        raise e


async def scrape(cur_url, purpose):
    """Main scraping function that tries crawl4ai first, then falls back to playwright."""
    md, html = "", ""
    try:
        logger.info("Attempting to scrape %s with crawl4ai", cur_url)
        md, html = await crawl(cur_url, purpose)
    except Exception as e:
        logger.warning("crawl4ai failed for %s: %s", cur_url, str(e)) 
        try:
            logger.info("Falling back to playwright for %s", cur_url)
            md, html = await playwright(cur_url, purpose)
        except Exception as e2:
            logger.error("All scraping methods failed for %s: %s", cur_url, str(e2))
            return md, html

    return md, html


async def scrape_urls(
    urls,
    redis=None,
    task_id: str = None,
    step_name: str = None,
    step_weight: int = None,
    purpose="kb",
    output_dir: str = "./markdown_content",
    base_percent: float = 0.0,  # ✅ newly added: percent already achieved before this step
    total_progress: float = 100.0,  # ✅ overall cap (default 100)
):
    """
    Scrape one or multiple URLs and optionally update progress in Redis.
    Each URL contributes proportionally to the step's weight if provided.

    Now includes global progress logic:
    - base_percent: previous total progress (e.g., 20 after step 1)
    - step_weight: portion assigned to this step (e.g., 20)
    - updates Redis percent atomically as each URL finishes
    """

    scraped_content = ""

    if not isinstance(urls, list):
        urls = [urls]

    no_of_links = len(urls)
    if no_of_links == 0:
        logger.warning("No URLs provided for scraping.")
        return scraped_content

    per_link_increment = step_weight / no_of_links if step_weight else 0

    logger.info("Starting batch scrape of %d URLs", no_of_links)
    logger.debug(
        "Base percent: %.2f, Step weight: %d, Increment per link: %.2f",
        base_percent,
        step_weight or 0,
        per_link_increment,
    )

    for i, url in enumerate(urls, start=1):
        logger.info("Processing URL %d/%d: %s", i, no_of_links, url)

        try:
            cleaned_text, _ = await scrape(url, purpose)
            scraped_content += cleaned_text or ""
            save_file(cleaned_text, url, output_dir)

            # ------------------ update progress if redis/task_id provided ------------------
            if redis and task_id and step_name and step_weight:
                global_percent = base_percent + (i * per_link_increment)

                # Ensure we don’t exceed base + step_weight or total_progress
                global_percent = min(
                    global_percent, base_percent + step_weight, total_progress
                )

                progress_details = f"Scraped {i}/{no_of_links} URLs"
                logger.debug(
                    "Updating global progress: %.2f%% (%s)",
                    global_percent,
                    progress_details,
                )

                await set_task_state(
                    redis,
                    task_id,
                    {
                        "current_step": step_name,
                        "percent": math.ceil(global_percent),
                        "details": progress_details,
                    },
                )

        except Exception as e:
            logger.error("Failed to scrape %s: %s", url, str(e))
            if redis and task_id:
                error_msg = f"Error scraping {url}: {e}"
                logger.error("Setting task failure state: %s", error_msg)
                await set_task_state(
                    redis, task_id, {"state": "FAILED", "error_message": error_msg}
                )
            raise

    logger.info("Completed batch scrape of %d URLs", no_of_links)
    return scraped_content

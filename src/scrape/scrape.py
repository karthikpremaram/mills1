"""scrape the website content using playwright and craw4ai"""

import re
import os
from markdownify import markdownify  # cSpell:disable-line
from crawl4ai import AsyncWebCrawler
from playwright.async_api import async_playwright, Error
from langchain_community.document_transformers import Html2TextTransformer
from langchain.schema import Document


def clean_text_for_prompt(content):
    """remove the html tags from the content"""
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


def get_filename(url, output_dir):  # pylint: disable=unused-argument
    """get the markdown file name"""
    paths = os.listdir(output_dir)
    if len(paths) == 0:
        return output_dir + "/0.md"
    else:
        path = max(list(map(lambda x: int(re.findall(r"\d+", x)[0])), paths))
        try:
            return output_dir + "/" + str(int(path) + 1) + ".md"
        except ValueError:
            return output_dir + "/" + str(101) + ".md"


def save_file(md, url, output_dir="./markdown_content"):
    """save the markdown file"""
    file_name = get_filename(url, output_dir)
    with open(file_name, "w", encoding="utf-8") as f:
        f.write(md)


def remove_header_footer(html_text: str) -> str:
    """remove headers from the file"""
    return re.sub(r"<(header|footer)[\s\S]*?</\1>", "", html_text, flags=re.I)


async def crawl(cur_url, refine_with_llm: bool):
    """Fetch markdown content for a single URL using crawl4ai."""
    try:
        print(f"--> Crawl: Extracting content from {cur_url}")
        excluded_tags = []
        if not refine_with_llm:
            excluded_tags = ["header", "footer"]
        async with AsyncWebCrawler() as crawler:
            result = await crawler.arun(
                cur_url, excluded_tags=excluded_tags
            )  # cSpell:disable-line
            if not result or not result.markdown:
                raise ValueError("No markdown found")
            cleaned = ""
            if refine_with_llm:
                print("--> cleaning and refining for kb")
                cleaned = clean_text_for_kb(result.markdown)
                cleaned = refine_with_llm(cleaned)
            else:
                print("-->cleaning for prompt")
                cleaned = clean_text_for_prompt(result.html)

            print(f"--> {len(cleaned)} chars extracted")
            return cleaned, result.html
    except Error as e:
        print("error", e)
        raise RuntimeError(
            f"failed to extract the content from {cur_url}", "error :", e
        ) from e


async def playwright(cur_url, refine_with_llm: bool):
    """Fetch markdown content for a single URL using playwright."""
    try:
        print(f"-->Playwright: Extracting content from {cur_url}")
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(cur_url, timeout=30000)
            html = await page.content()
            cleaned = ""
            if refine_with_llm:
                print("--> cleaning and refining for kb")
                cleaned = markdownify(html)  # cSpell:disable-line
                cleaned = clean_text_for_kb(cleaned)
                cleaned = refine_with_llm(cleaned)
            else:
                print("--> cleaning for prompt")
                cleaned = clean_text_for_prompt(html)

            print(f"--> {len(cleaned)} chars extracted")
            await browser.close()
            return cleaned, html
    except Error as e:
        raise RuntimeError(
            f"Failed to extract content from {cur_url}", "error :", e
        ) from e


async def scrape(cur_url: str, refine_with_llm: bool):
    """Scrape the content using crawl4ai or fallback to playwright."""
    md, html = "", ""

    try:
        print(f"-->Trying crawl4ai for {cur_url}")
        md, html = await crawl(cur_url, refine_with_llm)

    except Exception as e:  # pylint: disable=broad-exception-caught
        print(f"-->crawl4ai failed for {cur_url}: {e},")

        try:
            print(f"-->Trying playwright for {cur_url}")
            md, html = await playwright(cur_url, refine_with_llm)

        except Exception as e2:  # pylint: disable=broad-exception-caught
            print(f"-->playwright failed for {cur_url}: {e2}")
            return md, html

    return md


async def scrape_urls(
    urls, refine_with_llm: bool = True, output_dir: str = "./markdown_content"
):
    """Scrape one or multiple URLs using crawl4ai/playwright and save the output."""

    scraped_content = ""

    # Handle both single URL and list of URLs
    if not isinstance(urls, list):
        urls = [urls]

    no_of_links = len(urls)

    for i, url in enumerate(urls, start=1):
        print("--" * 20)
        if no_of_links > 1:
            print(f"Scraping {i}/{no_of_links}: {url}")

        cleaned_text = await scrape(url, refine_with_llm)
        scraped_content += cleaned_text or ""

        try:
            save_file(cleaned_text, url, output_dir)
        except Exception as e:  # pylint: disable=broad-exception-caught
            print(f"Exception while saving scraped content from {url}: {e}")

    return scraped_content

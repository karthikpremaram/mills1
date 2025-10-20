"""Agent tools for scraping and directory management."""

import asyncio
import json
import os
import shutil
from typing import List, Dict

from langchain.tools import tool
from pydantic import BaseModel
from src.scrape.scrape import scrape_urls


# -------------------------------
# Data Models
# -------------------------------
class LinksInput(BaseModel):
    values: List[str]


# -------------------------------
# Global state
# -------------------------------
COMPANY_NAMES: List[str] = []
IMPORTANT_LINKS: Dict[str, LinksInput] = {}
_LINKS_FILE_COUNTER = 0


# -------------------------------
# Directory functions
# -------------------------------
def create_directory_structure(path: str, override: bool = False) -> None:
    """Create a directory structure. Clears it if `clear_folder` is True."""
    try:
        if override:
            if os.path.exists(path) and os.path.isdir(path):
                shutil.rmtree(path)

        os.makedirs(path, exist_ok=False)
        print(f"Directory structure '{path}' created successfully.")
    except OSError as e:
        print(f"{path} already exists or error creating directory: {e}")


@tool
def create_directory(company_name: str) -> str:
    """Create directories to store scraped and generated data."""
    global COMPANY_NAMES

    COMPANY_NAMES.clear()
    COMPANY_NAMES.append(company_name)
    print(f"Current company names: {COMPANY_NAMES}")

    # Create directory for markdown files from scraping
    create_directory_structure(f"markdown_content/{company_name}/")

    # Create directory for agent-related content (kb, important links, etc)
    create_directory_structure(f"agent_content/{company_name}/", override=True)

    return f"Created directories for {company_name}"


# -------------------------------
# Scraping functions
# -------------------------------
@tool
def scrape_and_clean(url: str) -> str:
    """Scrape and extract clean text content from a single webpage URL."""
    global _LINKS_FILE_COUNTER

    if not COMPANY_NAMES:
        return "Error: No company selected. Please create directories first."

    company = COMPANY_NAMES[0]
    links_file = f"agent_content/{company}/links_opened.txt"

    # Write the URL to links file
    mode = "w" if _LINKS_FILE_COUNTER == 0 else "a"
    try:
        with open(links_file, mode, encoding="utf-8") as f:
            f.write(f"{url},\n")
        _LINKS_FILE_COUNTER += 1
    except OSError as err:
        return f"Error writing URL to file: {err}"

    # Scrape the URL asynchronously
    try:
        # Store markdown files in markdown_content directory
        scraped_content = asyncio.run(
            scrape_urls(url, purpose="prompt", output_dir=f"markdown_content/{company}")
        )
        return scraped_content
    except Exception as e:  # pylint: disable=broad-exception-caught
        print(e)
        return f"Error processing {url}: {e}"


# -------------------------------
# Important links functions
# -------------------------------
@tool("save_links", args_schema=LinksInput)
def save_links(values: LinksInput) -> str:
    """Save important links about the company (about, services, contact, etc.)."""
    if not COMPANY_NAMES:
        return "Error: No company selected. Please create directories first."
    global IMPORTANT_LINKS
    company = COMPANY_NAMES[0]
    IMPORTANT_LINKS["links"] = values

    path = f"agent_content/{company}/important_links.json"
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"links": values}, f, indent=4)
        print(f"Important links saved: {path}")
        return "Saved successfully"
    except OSError as err:
        print(f"Error saving important links: {err}")
        return f"Error saving links: {err}"

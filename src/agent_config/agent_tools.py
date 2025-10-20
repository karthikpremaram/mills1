"""Agent tools for scraping and directory management."""

import asyncio
import json
import os
import shutil
from typing import List, Dict

from langchain.tools import tool
from pydantic import BaseModel
from src.scrape.scrape import scrape_urls
from src.logger.logger import logger


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
                logger.info("Removing existing directory: %s", path)
                shutil.rmtree(path)

        os.makedirs(path, exist_ok=False)
        logger.info("Directory structure created successfully: %s", path)
    except OSError as e:
        logger.error("Error creating directory %s: %s", path, str(e))
        raise


@tool
def create_directory(company_name: str) -> str:
    """Create directories to store scraped and generated data."""
    global COMPANY_NAMES
    logger.info("Creating directories for company: %s", company_name)

    COMPANY_NAMES.clear()
    COMPANY_NAMES.append(company_name)
    logger.debug("Updated company names list: %s", COMPANY_NAMES)

    # Create directory for markdown files from scraping
    markdown_dir = f"markdown_content/{company_name}/"
    logger.info("Creating markdown content directory: %s", markdown_dir)
    create_directory_structure(markdown_dir)

    # Create directory for agent-related content (kb, important links, etc)
    agent_dir = f"agent_content/{company_name}/"
    logger.info("Creating agent content directory: %s", agent_dir)
    create_directory_structure(agent_dir, override=True)

    logger.info("Successfully created all directories for %s", company_name)
    return f"Created directories for {company_name}"


# -------------------------------
# Scraping functions
# -------------------------------
@tool
def scrape_and_clean(url: str) -> str:
    """Scrape and extract clean text content from a single webpage URL."""
    global _LINKS_FILE_COUNTER

    if not COMPANY_NAMES:
        logger.error("No company selected - directories must be created first")
        return "Error: No company selected. Please create directories first."

    company = COMPANY_NAMES[0]
    logger.info("Starting scrape for company %s at URL: %s", company, url)

    links_file = f"agent_content/{company}/links_opened.txt"
    logger.debug("Links file location: %s", links_file)

    # Write the URL to links file
    mode = "w" if _LINKS_FILE_COUNTER == 0 else "a"
    try:
        with open(links_file, mode, encoding="utf-8") as f:
            f.write(f"{url},\n")
        _LINKS_FILE_COUNTER += 1
        logger.debug(
            "Successfully wrote URL to links file (counter: %d)", _LINKS_FILE_COUNTER
        )
    except OSError as err:
        logger.error("Failed to write URL to links file: %s", str(err))
        return f"Error writing URL to file: {err}"

    # Scrape the URL asynchronously
    try:
        output_dir = f"markdown_content/{company}"
        logger.info("Starting URL scrape with output dir: %s", output_dir)

        scraped_content = asyncio.run(
            scrape_urls(url, purpose="prompt", output_dir=output_dir)
        )
        logger.info(
            "Successfully scraped URL, content length: %d chars", len(scraped_content)
        )
        return scraped_content

    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Error processing URL %s: %s", url, str(e), exc_info=True)
        return f"Error processing {url}: {e}"


# -------------------------------
# Important links functions
# -------------------------------
@tool("save_links", args_schema=LinksInput)
def save_links(values: LinksInput) -> str:
    """Save important links about the company (about, services, contact, etc.)."""
    if not COMPANY_NAMES:
        logger.error("No company selected - directories must be created first")
        return "Error: No company selected. Please create directories first."

    global IMPORTANT_LINKS
    company = COMPANY_NAMES[0]
    logger.info("Saving important links for company: %s", company)

    IMPORTANT_LINKS["links"] = values
    logger.debug("Updated IMPORTANT_LINKS with %d links", len(values.values))

    path = f"agent_content/{company}/important_links.json"
    try:
        logger.debug("Writing links to: %s", path)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"links": values}, f, indent=4)
        logger.info("Successfully saved important links to %s", path)
        return "Saved successfully"
    except OSError as err:
        logger.error(
            "Error saving important links to %s: %s", path, str(err), exc_info=True
        )
        return f"Error saving links: {err}"

"""agent functionality"""

import asyncio
from langchain.chat_models import init_chat_model
from src.agent_config.agent_graph import AgentGraph
from src.agent_config.agent_tools import (
    COMPANY_NAMES,
    IMPORTANT_LINKS,
    create_directory,
    save_links,
    scrape_and_clean,
)
from src.scrape.scrape import scrape_urls
from src.core.config import Config
from src.core.prompts import FIXED_PROMPT, SYSTEM_PROMPT
from src.track_cost.cost_tracking_llm import CostTrackingLLM


# ✅ Initialize LLM
llm = init_chat_model(
    "google_genai:gemini-2.5-flash",
    model_kwargs={
        "api_key": Config.GEMINI_API_KEY,
        "streaming": True,
    },
)


cost_tracking_llm = CostTrackingLLM(llm)
tools = [scrape_and_clean, save_links, create_directory]


async def agent_action(url: str):
    """Agent to create system prompt and provide URLs for knowledge base."""

    try:
        agent_graph = AgentGraph(cost_tracking_llm, tools)
        agent = await agent_graph.create_agent()
        print("✅ Agent created successfully")
        print("*" * 40)

        # Construct dynamic user prompt
        prompt = f"""
        Go through the URL and give a prompt like the reference below.
        Main URL: {url}
        If you don't get info in the main URL, use links from the scraped content by observing endpoints.
        Save important links using the provided tool before giving the final output.
        """

        # System & user messages
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt.strip()},
        ]

        # Agent configuration
        config = {"configurable": {"thread_id": "1"}}

        result = await agent.ainvoke({"messages": messages}, config=config)

        assistant_prompt = result.get("messages", [])[-1].content if isinstance(result, dict) else str(result)
        if not assistant_prompt:
            raise ValueError("Agent did not return a valid assistant prompt")


        lines = assistant_prompt.splitlines()
        assistant_prompt = "\n".join([lines[0], FIXED_PROMPT, *lines[1:]])


        company = COMPANY_NAMES[0] if COMPANY_NAMES else "unknown_company"
        path = f"agent_content/{company}/prompt.txt"
        with open(path, "w", encoding="utf-8") as f:
            f.write(assistant_prompt)

        print(f"System prompt saved: {path}")
        return assistant_prompt, IMPORTANT_LINKS

    except Exception as e:
        print(f"Error in agent_action: {e}")
        raise

async def get_knowledge_base(company_name: str, important_links: dict):
    """Scrape and clean links for knowledge base."""
    try:
        # Await the async scraper function
        kb = await scrape_urls(
            important_links.get("links", []),
            refine_with_llm=True,
            output_dir=f"agent_content/{company_name}",
        )

        print(f"Knowledge base length: {len(kb)} characters")

        path = f"agent_content/{company_name}/kb.txt"
        with open(path, "w", encoding="utf-8") as f:
            f.write(kb)

        print(f"Knowledge base stored at: {path}")
        return kb

    except Exception as e:
        print(f"Error in get_knowledge_base: {e}")
        raise

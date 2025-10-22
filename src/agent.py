"""agent functionality"""

import os
from langchain.chat_models import init_chat_model
from src.agent_config.agent_graph import AgentGraph
from src.agent_config.agent_tools import (
    COMPANY_NAMES,
    IMPORTANT_LINKS,
    create_directory,
    save_links,
    scrape_and_clean,
)
from src.jobs.redis_state import set_task_state
from src.logger.logger import logger
from src.scrape.scrape import scrape_urls
from src.core.config import Config
from src.core.prompts import FIXED_PROMPT, SYSTEM_PROMPT
from src.track_cost.cost_tracking_llm import CostTrackingLLM
import pickle


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


async def create_system_prompt_important_links(url: str):
    """
    Agent to create system prompt and provide URLs for knowledge base.

    Returns:
        assistant_prompt (str): Final system prompt
        IMPORTANT_LINKS (list): List of important links extracted by the agent
    """
    try:
        # Create agent
        logger.info("Creating agent for URL: %s", url)
        agent_graph = AgentGraph(cost_tracking_llm, tools)
        agent = await agent_graph.create_agent()
        logger.info("✅ Agent created successfully")

        # Construct dynamic user prompt
        logger.debug("Constructing user prompt for URL: %s", url)
        prompt = f"""
        Go through the URL and give a prompt like the reference below.
        Main URL: {url}
        If you don't get info in the main URL, use links from the scraped content by observing endpoints.
        Save important links using the provided tool before giving the final output.
        """

        # System & user messages
        config = {"configurable": {"thread_id": "1"}}
        logger.debug("Invoking agent with system and user messages")
        result = agent.invoke(
            {
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ]
            },
            config,
            stream_mode="values",
        )
        print("agent completed")
        with open("pickle.pkl", "wb") as f:
            pickle.dump(result, f)
        # get assistant content safely (could be str or list or other)
        assistant_content = result["messages"][-1].content

        # normalize to a single string
        if isinstance(assistant_content, list):
            # join with newlines (preserves structure)
            assistant_prompt = "\n".join(str(x) for x in assistant_content)
        else:
            assistant_prompt = str(assistant_content) if assistant_content is not None else ""

        if not assistant_prompt.strip():
            logger.error("Agent returned empty prompt for URL: %s", url)
            raise ValueError("Agent did not return a valid assistant prompt")

        # safely split into lines and insert FIXED_PROMPT after first line
        lines = assistant_prompt.splitlines()
        first = lines[0] if lines else ""
        rest = "\n".join(lines[1:]) if len(lines) > 1 else ""
        assistant_prompt = first + "\n" + FIXED_PROMPT + ("\n" + rest if rest else "")

        # # Ensure assistant_prompt is a string before splitlines
        # lst = assistant_prompt.split("\n")
        # assistant_prompt = lst[0] + "\n" + FIXED_PROMPT + "\n".join(lst[1:])
        
        logger.debug(
            "Generated assistant prompt length: %d chars", len(assistant_prompt)
        )

        # Save prompt to file
        company = COMPANY_NAMES[0] if COMPANY_NAMES else "unknown_company"
        path = os.path.join("agent_content", company)
        os.makedirs(path, exist_ok=True)
        file_path = os.path.join(path, "prompt.txt")

        logger.debug("Saving system prompt to: %s", file_path)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(assistant_prompt)

        logger.info("System prompt successfully generated and saved")
        return assistant_prompt, IMPORTANT_LINKS

    except Exception as e:
        logger.error("Error in agent_action for %s: %s", url, str(e), exc_info=True)
        raise


async def get_knowledge_base(
    company_name: str, IMPORTANT_LINKS: dict, redis, task_id: str
):
    """Scrape and clean links for knowledge base with progress tracking."""
    try:
        urls = IMPORTANT_LINKS.get("links", [])
        logger.info(
            "Starting knowledge base creation for %s with %d URLs",
            company_name,
            len(urls),
        )

        kb = await scrape_urls(
            urls,
            redis=redis,
            task_id=task_id,
            step_name="create_knowledge_base",
            step_weight=20,  # same as STEPS[1]
            purpose="kb",
            output_dir=f"markdown_content/{company_name}",
        )

        logger.info("Generated knowledge base of %d characters", len(kb))

        path = f"agent_content/{company_name}/kb.txt"
        logger.debug("Saving knowledge base to: %s", path)
        with open(path, "w", encoding="utf-8") as f:
            f.write(kb)

        logger.info("Knowledge base successfully stored at: %s", path)
        return kb

    except Exception as e:
        logger.error(
            "Error in get_knowledge_base for %s: %s",
            company_name,
            str(e),
            exc_info=True,
        )
        await set_task_state(
            redis, task_id, {"state": "FAILED", "error_message": str(e)}
        )
        raise

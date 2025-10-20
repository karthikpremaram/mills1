"""create markdown and knowledge base description using llm"""

import os
from langchain.chat_models import init_chat_model
from langchain_core.prompts import PromptTemplate

from src.track_cost.cost_tracking_llm import CostTrackingLLM
from src.core.config import Config
from src.core.prompts import KNOWLEDGE_BASE_DESCRIPTION_PROMPT, MARKDOWN_PROMPT
from src.logger.logger import logger


# llm = init_chat_model(
#     "openai:gpt-4",
#     api_key=Config.OPENAI_API_KEY,
# )


llm = init_chat_model(
    "google_genai:gemini-2.5-flash",
    model_kwargs={
        "api_key": Config.GEMINI_API_KEY,
        "streaming": True,
    },
)

cost_tracking_llm = CostTrackingLLM(llm, Config.OPENAI_MODEL_NAME)

markdown_prompt_template = PromptTemplate.from_template(MARKDOWN_PROMPT)
kb_description_prompt_template = PromptTemplate.from_template(
    KNOWLEDGE_BASE_DESCRIPTION_PROMPT
)

markdown_chain = markdown_prompt_template | cost_tracking_llm
kd_description_chain = kb_description_prompt_template | cost_tracking_llm


def refine_with_llm(markdown):
    """refine the scraped content using llm"""
    logger.info(
        "Starting markdown refinement using LLM, input length: %d chars", len(markdown)
    )
    refined_markdown = markdown_chain.invoke(markdown)
    logger.info(
        "Successfully refined markdown, output length: %d chars",
        len(refined_markdown.content),
    )
    return refined_markdown.content


def get_kb_description(links, output_dir):
    """create knowledge base description based on the important URLS"""
    logger.info("Generating knowledge base description for %d links", len(links))
    kb_description = kd_description_chain.invoke(links)

    path = os.path.join(output_dir, "kb_description.txt")
    logger.debug("Writing knowledge base description to: %s", path)

    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(kb_description.content)
        logger.info("Successfully saved knowledge base description to %s", path)
    except IOError as e:
        logger.error(
            "Failed to save knowledge base description to %s: %s",
            path,
            str(e),
            exc_info=True,
        )
        raise

    return kb_description.content

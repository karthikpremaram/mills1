"""create markdown and knowledge base description using llm"""

import os
from langchain.chat_models import init_chat_model
from langchain_core.prompts import PromptTemplate

from src.track_cost.cost_tracking_llm import CostTrackingLLM
from src.core.config import Config
from src.core.prompts import KNOWLEDGE_BASE_DESCRIPTION_PROMPT, MARKDOWN_PROMPT


llm = init_chat_model(
    "openai:gpt-4",
    api_key=Config.OPENAI_API_KEY,
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
    refined_markdown = markdown_chain.invoke(markdown)
    return refined_markdown.content

def get_kb_description(links, output_dir):
    """create knowledge base description based on the important URLS"""
    kb_description =  kd_description_chain.invoke(links)
    path = os.path.join(output_dir, "kb_description.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(kb_description.content)
    print(f"kb description saved {path}")

    return kb_description.content

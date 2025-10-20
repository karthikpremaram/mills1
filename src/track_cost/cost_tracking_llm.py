"""calculate the cost per token"""

from langchain_core.runnables import Runnable
from src.logger.logger import logger


def calc_cost(input_tokens, output_tokens, model_name):
    """Calculate the cost per input and output token based on the model."""

    if model_name in ("models/gpt-4.1-mini", "gpt-4.1-mini"):
        rate_in = 0.40 / 1_000_000  # $0.40 per 1M input tokens
        rate_out = 1.60 / 1_000_000  # $1.60 per 1M output tokens

    elif model_name in ("models/gpt-4o-mini", "gpt-4o-mini"):
        rate_in = 0.15 / 1_000_000
        rate_out = 0.60 / 1_000_000

    elif model_name in ("models/gemini-2.5-flash", "gemini-2.5-flash"):
        rate_in = 0.30 / 1_000_000
        rate_out = 2.50 / 1_000_000

    else:
        logger.warning("Unknown model %s - cannot calculate token costs", model_name)
        return None, None

    return round(input_tokens * rate_in, 6), round(output_tokens * rate_out, 6)


class CostTrackingLLM(Runnable):
    """calculate the cost per token"""

    def __init__(self, llm, model_name=None):
        self.llm = llm
        self.model_name = model_name
        self.final_cost = 0

    def invoke(self, messages, tags=None, **kwargs):  # pylint: disable=unused-argument  disable=arguments-renamed
        """function called when the llm is invoked"""
        response = self.llm.invoke(messages, **kwargs)

        if self.model_name is None:
            if hasattr(self.llm, "model"):
                self.model_name = self.llm.model
            elif hasattr(self.llm, "model_name"):
                self.model_name = self.llm.model_name

        input_tokens = response.usage_metadata["input_tokens"]
        output_tokens = response.usage_metadata["output_tokens"]
        input_cost, output_cost = calc_cost(
            input_tokens, output_tokens, model_name=self.model_name
        )

        logger.info("Model %s usage:", self.model_name)
        logger.info("Tokens - Input: %d, Output: %d", input_tokens, output_tokens)
        logger.debug("Costs - Input: $%.6f, Output: $%.6f", input_cost, output_cost)

        combined_cost = input_cost + output_cost
        self.final_cost += combined_cost
        logger.info(
            "Request cost: $%.6f, Total cost: $%.6f", combined_cost, self.final_cost
        )

        return response

    def bind_tools(self, tools):
        """Ensure cost tracking persists after binding tools"""
        bound_llm = self.llm.bind_tools(tools)
        return CostTrackingLLM(bound_llm, self.model_name)

from langchain_core.runnables import Runnable
from src.logger.logger import logger


def calc_cost(input_tokens, output_tokens, model_name):
    if model_name in ["models/gpt-4.1-mini", "gpt-4.1-mini"]:
        rate_in = 0.40 / 1_000_000
        rate_out = 1.60 / 1_000_000
    elif model_name in ["models/gpt-4o-mini", "gpt-4o-mini"]:
        rate_in = 0.15 / 1_000_000
        rate_out = 0.60 / 1_000_000
    elif model_name in ["models/gemini-2.5-flash", "gemini-2.5-flash"]:
        rate_in = 0.30 / 1_000_000
        rate_out = 2.50 / 1_000_000
    else:
        logger.warning("Assign cost for input and output tokens for model: %s", model_name)
        return 0, 0

    input_cost = round(input_tokens * rate_in, 6)
    output_cost = round(output_tokens * rate_out, 6)
    return input_cost, output_cost


class CostTrackingLLM(Runnable):
    def __init__(self, llm, model_name=None):
        self.llm = llm
        self.model_name = model_name
        self.final_cost = 0

    def invoke(self, messages, tags=None, **kwargs):
        response = self.llm.invoke(messages, **kwargs)

        # Determine model name dynamically if not provided
        if self.model_name is None:
            if hasattr(self.llm, "model"):
                self.model_name = self.llm.model
            elif hasattr(self.llm, "model_name"):
                self.model_name = self.llm.model_name
            else:
                self.model_name = "unknown"

        # ---- SAFE TOKEN HANDLING ----
        usage = getattr(response, "usage_metadata", None)
        if usage and isinstance(usage, dict):
            input_tokens = usage.get("input_tokens", 0)
            output_tokens = usage.get("output_tokens", 0)
        else:
            input_tokens, output_tokens = 0, 0

        input_cost, output_cost = calc_cost(
            input_tokens, output_tokens, model_name=self.model_name
        )

        logger.info("---- Cost Tracking ----")
        logger.info("Model name        : %s", self.model_name)
        logger.info("Input tokens      : %s", input_tokens)
        logger.info("Output tokens     : %s", output_tokens)
        logger.info("Input cost ($)    : %s", input_cost)
        logger.info("Output cost ($)   : %s", output_cost)
        logger.info("Total cost ($)    : %s", input_cost + output_cost)
        logger.info(
            "Accumulated total : %s", self.final_cost + input_cost + output_cost
        )
        logger.info("-----------------------")

        self.final_cost += input_cost + output_cost
        return response

    def bind_tools(self, tools):
        """Ensure cost tracking persists after binding tools"""
        bound_llm = self.llm.bind_tools(tools)
        return CostTrackingLLM(bound_llm, self.model_name)

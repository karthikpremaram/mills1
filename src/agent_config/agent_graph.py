from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from typing_extensions import TypedDict
from typing import Annotated

class State(TypedDict):
    messages: Annotated[list, add_messages]

class AgentGraph:
    def __init__(self, llm, tools):
        self.llm = llm
        self.tools = tools

    async def create_agent(self):
        graph_builder = StateGraph(State)
        llm_with_tools = self.llm.bind_tools(self.tools)
        
        def chatbot(state: State):
            return {"messages": [llm_with_tools.invoke(state["messages"])]}
        graph_builder.add_node("chatbot", chatbot)
        # Add tool node
        tool_node = ToolNode(tools=self.tools)
        graph_builder.add_node("tools", tool_node)
        # Conditional edges using END
        graph_builder.add_conditional_edges(
            "chatbot",
            tools_condition,
        )

        # Connect tools back to chatbot
        graph_builder.add_edge("tools", "chatbot")
        graph_builder.add_edge(START, "chatbot")
        graph = graph_builder.compile()

        return graph

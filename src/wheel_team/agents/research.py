"""Input middleware shared by the two public research roles."""

from strands import Agent
from strands.middleware import InvokeModelContext, InvokeModelStage
from strands.types.tools import ToolChoiceToolDict

from wheel_team.option_response_view import option_response_messages
from wheel_team.types import RoleContext


def install_research_middleware(agent: Agent, context: RoleContext) -> None:
    def option_views(model_context: InvokeModelContext) -> InvokeModelContext:
        return model_context.replace(messages=option_response_messages(model_context.messages))

    def terminal_tools(model_context: InvokeModelContext) -> InvokeModelContext:
        if context.terminal_only:
            choice: ToolChoiceToolDict = {"tool": {"name": "ResearchResult"}}
            return model_context.replace(
                tool_specs=[
                    spec for spec in model_context.tool_specs if spec["name"] == "ResearchResult"
                ],
                tool_choice=choice,
            )
        return model_context

    agent.add_middleware(InvokeModelStage.Input, option_views)
    agent.add_middleware(InvokeModelStage.Input, terminal_tools)

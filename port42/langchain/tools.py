from __future__ import annotations

try:
    from langchain_core.tools import BaseTool
except ImportError:
    raise ImportError("langchain-core is required: pip install port42[langchain]")

from pydantic import Field


class Port42Tool(BaseTool):
    agent: object = Field(exclude=True)


class RestCallTool(Port42Tool):
    name: str = "rest_call"
    description: str = "Make an HTTP request using Port42's secret store"

    def _run(self, url: str, method: str = "GET", secret: str | None = None, **kwargs):
        return self.agent.rest_call(url, method=method, secret=secret, **kwargs)


class ScreenCaptureTool(Port42Tool):
    name: str = "screen_capture"
    description: str = "Take a screenshot of the user's screen"

    def _run(self, scale: float = 0.5):
        return self.agent.screen_capture(scale=scale)


class ClipboardTool(Port42Tool):
    name: str = "clipboard"
    description: str = "Read or write the user's clipboard"

    def _run(self, action: str = "read", data: str | None = None):
        if action == "write" and data:
            return self.agent.clipboard_write(data)
        return self.agent.clipboard_read()


class TerminalTool(Port42Tool):
    name: str = "terminal_exec"
    description: str = "Run a shell command on the user's machine"

    def _run(self, command: str):
        return self.agent.terminal_exec(command)


def port42_tools(agent) -> list[BaseTool]:
    return [
        RestCallTool(agent=agent),
        ScreenCaptureTool(agent=agent),
        ClipboardTool(agent=agent),
        TerminalTool(agent=agent),
    ]

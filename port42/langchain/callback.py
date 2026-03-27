from __future__ import annotations

try:
    from langchain_core.callbacks import BaseCallbackHandler
except ImportError:
    raise ImportError("langchain-core is required: pip install port42[langchain]")


class Port42CallbackHandler(BaseCallbackHandler):
    """Streams LangChain execution progress into Port42 channels."""

    def __init__(self, agent, channel_id: str | None = None):
        self.agent = agent
        self.channel_id = channel_id

    def on_chain_start(self, serialized, inputs, **kwargs):
        self.agent.typing(self.channel_id)

    def on_chain_end(self, outputs, **kwargs):
        if hasattr(outputs, "model_dump"):
            # Pydantic model — render as port
            self._render_as_port(outputs)
        elif isinstance(outputs, str):
            self.agent.send(outputs, channel_id=self.channel_id)

    def on_chain_error(self, error, **kwargs):
        self.agent.send(f"Chain error: {error}", channel_id=self.channel_id)

    def on_tool_start(self, serialized, input_str, **kwargs):
        self.agent.typing(self.channel_id)

    def on_tool_end(self, output, **kwargs):
        pass

    def _render_as_port(self, model):
        fields = model.model_dump()
        rows = "".join(
            f"<tr><td>{k}</td><td>{v}</td></tr>"
            for k, v in fields.items()
        )
        html = f"""<!DOCTYPE html>
<html>
<head><title>{type(model).__name__}</title><meta name="version" content="1"></head>
<body>
<table style="width:100%;border-collapse:collapse;font-family:monospace;font-size:12px">
{rows}
</table>
</body>
</html>"""
        self.agent.port_create(html=html, title=type(model).__name__)

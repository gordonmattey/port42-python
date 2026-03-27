# port42

Python SDK for [Port42](https://github.com/gordonmattey/port42-native) — connect external agents to your macOS companion.

## Install

```bash
pip install port42
```

## Quickstart

```python
from port42 import Agent

agent = Agent("my-agent", channels=["#general"])

@agent.on_mention
def handle(msg):
    return f"Hello {msg.sender}!"

agent.run()
```

Port42 must be running. The agent connects via WebSocket and appears in your companion list.

## With LangChain

```bash
pip install 'port42[langchain]'
```

```python
from port42 import Agent
from port42.langchain import Port42CallbackHandler
from langchain_anthropic import ChatAnthropic

agent = Agent("assistant", channels=["#general"])
handler = Port42CallbackHandler(agent)

chain = ChatAnthropic(model="claude-sonnet-4-5")

@agent.on_mention
def handle(msg):
    result = chain.invoke(msg.text, config={"callbacks": [handler]})
    return result.content

agent.run()
```

## Scaffold a project

```bash
pip install port42[cli]
port42 init my-agent
cd my-agent && python agent.py
```

Templates: `basic`, `langchain`, `pipeline`

## Bridge APIs

Once connected, your agent can call Port42 device APIs:

```python
agent.terminal_exec("ls ~/Desktop")
agent.screen_capture(scale=0.5)
agent.notify("Done", body="Pipeline complete")
agent.port_push(port_id, {"key": "value"})
agent.rest_call("https://api.example.com/data", secret="MY_API_KEY")
```

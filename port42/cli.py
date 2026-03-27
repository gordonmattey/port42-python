try:
    import click
except ImportError:
    raise ImportError("click is required: pip install port42[cli]")

import os


BASIC_TEMPLATE = '''from port42 import Agent

agent = Agent("{name}", channels=["#general"])

@agent.on_mention
def handle(msg):
    return f"Hello {{msg.sender}}! You said: {{msg.text}}"

agent.run()
'''

LANGCHAIN_TEMPLATE = '''from port42 import Agent
from port42.langchain import Port42CallbackHandler
from langchain_anthropic import ChatAnthropic
from langchain_core.prompts import ChatPromptTemplate

agent = Agent("{name}", channels=["#general"])
handler = Port42CallbackHandler(agent)

prompt = ChatPromptTemplate.from_messages([
    ("system", "You are a helpful assistant."),
    ("human", "{{input}}"),
])
chain = prompt | ChatAnthropic(model="claude-sonnet-4-5")

@agent.on_mention
def handle(msg):
    result = chain.invoke(
        {{"input": msg.text}},
        config={{"callbacks": [handler]}}
    )
    return result.content

agent.run()
'''

PIPELINE_TEMPLATE = '''from port42 import Agent

agent = Agent("{name}", channels=["#ops"])

@agent.on_mention
def handle(msg):
    agent.typing()

    # TODO: run your pipeline here
    result = f"processed: {{msg.text}}"

    # Optionally create a port to display results
    # agent.port_create(html="<html>...</html>", title="Result")

    return result

agent.run()
'''


@click.group()
def main():
    pass


@main.command()
@click.argument("name")
@click.option("--template", default="basic", help="Template: basic, langchain, pipeline")
def init(name: str, template: str):
    """Create a new Port42 agent project."""
    os.makedirs(name, exist_ok=True)

    templates = {
        "basic": BASIC_TEMPLATE,
        "langchain": LANGCHAIN_TEMPLATE,
        "pipeline": PIPELINE_TEMPLATE,
    }
    if template not in templates:
        raise click.BadParameter(f"Unknown template '{template}'. Choose: basic, langchain, pipeline")

    agent_py = os.path.join(name, "agent.py")
    with open(agent_py, "w") as f:
        f.write(templates[template].format(name=name))

    click.echo(f"Created {name}/agent.py")
    click.echo(f"Run with: cd {name} && python agent.py")

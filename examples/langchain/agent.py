from port42 import Agent
from port42.langchain import Port42CallbackHandler
from langchain_anthropic import ChatAnthropic
from langchain_core.prompts import ChatPromptTemplate

agent = Agent("assistant", channels=["#general"])
handler = Port42CallbackHandler(agent)

prompt = ChatPromptTemplate.from_messages([
    ("system", "You are a helpful assistant."),
    ("human", "{input}"),
])
chain = prompt | ChatAnthropic(model="claude-sonnet-4-5")

@agent.on_mention
def handle(msg):
    result = chain.invoke(
        {"input": msg.text},
        config={"callbacks": [handler]}
    )
    return result.content

agent.run()

from port42 import Agent

agent = Agent("echo", channels=["#general"])

@agent.on_mention
def handle(msg):
    return f"Hello {msg.sender}! You said: {msg.text}"

agent.run()

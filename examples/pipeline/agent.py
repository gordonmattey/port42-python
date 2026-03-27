from port42 import Agent

agent = Agent("pipeline", channels=["#ops"])

@agent.on_mention
def handle(msg):
    agent.typing()

    # Run your pipeline
    result = f"Processed: {msg.text}"

    # Push a port with results
    html = f"""<!DOCTYPE html>
<html>
<head><title>Pipeline Result</title><meta name="version" content="1"></head>
<body style="padding:16px;font-family:monospace">
  <p style="color:#00d4aa">Result</p>
  <pre>{result}</pre>
</body>
</html>"""
    agent.port_create(html=html, title="Pipeline Result")

    return result

agent.run()

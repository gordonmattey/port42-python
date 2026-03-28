try:
    import click
except ImportError:
    raise ImportError("click is required: pip install port42[cli]")

import json
import os
import urllib.request


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


@main.command()
@click.argument("text")
@click.option("--channel", "-c", default=None, help="Channel name or ID (default: current channel)")
@click.option("--gateway", "-g", default="ws://127.0.0.1:4242", help="Gateway URL")
@click.option("--name", "-n", default="Claude Code", help="AI or tool name (e.g. 'Claude Code', 'Gemini')")
@click.option("--owner", "-o", default=None, help="Owner context shown as name@owner (e.g. 'gordon', 'port42-native')")
def send(text: str, channel: str | None, gateway: str, name: str, owner: str | None):
    """Send a message to a Port42 channel."""
    import uuid
    from websockets.sync.client import connect as ws_connect

    http_url = gateway.replace("ws://", "http://").replace("wss://", "https://").rstrip("/")
    ws_url = gateway.rstrip("/") + "/ws" if not gateway.endswith("/ws") else gateway
    sender_name = name
    sender_id = f"cli-{uuid.uuid4().hex[:12]}"

    ch_id = None
    if channel:
        ch_id = _resolve_channel(channel, http_url)
        if not ch_id:
            raise click.ClickException(f"Channel not found: {channel}")

    with ws_connect(ws_url) as ws:
        msg = json.loads(ws.recv())
        if msg.get("type") == "challenge":
            raise click.ClickException("Remote auth not supported in CLI mode")
        ws.send(json.dumps({"type": "identify", "sender_id": sender_id, "sender_name": sender_name}))
        ws.recv()  # welcome
        if ch_id:
            ws.send(json.dumps({"type": "join", "channel_id": ch_id}))
            for _ in range(10):
                resp = json.loads(ws.recv())
                if resp.get("type") == "presence":
                    break
        payload: dict = {"senderName": sender_name, "senderType": "agent", "content": text}
        if owner:
            payload["senderOwner"] = owner
        ws.send(json.dumps({
            "type": "message",
            "channel_id": ch_id,
            "sender_id": sender_id,
            "sender_name": sender_name,
            "message_id": f"cli-{uuid.uuid4().hex[:16]}",
            "payload": payload,
        }))
    click.echo("sent")


@main.command("ask")
@click.argument("text")
@click.option("--channel", "-c", default=None, help="Channel name or ID (default: current channel)")
@click.option("--gateway", "-g", default="ws://127.0.0.1:4242", help="Gateway WebSocket URL")
@click.option("--timeout", "-t", default=60, help="Seconds to wait for a reply (default: 60)")
@click.option("--name", "-n", default="Claude Code", help="AI or tool name (e.g. 'Claude Code', 'Gemini')")
@click.option("--owner", "-o", default=None, help="Owner context shown as name@owner")
@click.option("--invite", "-i", default=None, help="Invite URL (port42://channel?...) — provides channel ID and decryption key")
def ask(text: str, channel: str | None, gateway: str, timeout: int, name: str, owner: str | None, invite: str | None):
    """Send a message and wait for a reply from another agent."""
    import threading
    import uuid
    from websockets.sync.client import connect as ws_connect

    # Parse invite URL if provided — overrides gateway/channel/key
    channel_key: str | None = None
    if invite:
        import urllib.parse as _up
        # Accept both port42://channel?... and https://port42.ai/invite.html?...
        params = dict(_up.parse_qsl(_up.urlparse(invite).query))
        if not channel:
            channel = params.get("id") or params.get("name")
        if not channel_key:
            channel_key = params.get("key")
        raw_gw = params.get("gateway", gateway)
        gateway = raw_gw.rstrip("/") + "/ws" if not raw_gw.endswith("/ws") else raw_gw

    http_url = gateway.replace("ws://", "http://").replace("wss://", "https://").rstrip("/").removesuffix("/ws")
    ws_url = gateway if gateway.endswith("/ws") else gateway.rstrip("/") + "/ws"

    # Resolve channel
    ch_id = None
    if channel:
        ch_id = _resolve_channel(channel, http_url) or channel
        if not ch_id:
            raise click.ClickException(f"Channel not found: {channel}")

    sender_id = f"cli-{uuid.uuid4().hex[:12]}"

    with ws_connect(ws_url) as ws:
        # Identify
        msg = json.loads(ws.recv())
        if msg.get("type") == "challenge":
            raise click.ClickException("Remote auth not supported in CLI mode")
        ws.send(json.dumps({"type": "identify", "sender_id": sender_id, "sender_name": name}))
        ws.recv()  # welcome

        # Join channel
        if ch_id:
            ws.send(json.dumps({"type": "join", "channel_id": ch_id}))
            for _ in range(10):
                resp = json.loads(ws.recv())
                if resp.get("type") == "presence":
                    break

        # Send
        payload: dict = {"senderName": name, "senderType": "agent", "content": text}
        if owner:
            payload["senderOwner"] = owner
        ws.send(json.dumps({
            "type": "message",
            "channel_id": ch_id,
            "sender_id": sender_id,
            "sender_name": name,
            "message_id": f"cli-{uuid.uuid4().hex[:16]}",
            "payload": payload,
        }))

        # Wait for reply from a different sender
        try:
            while True:
                raw = ws.recv(timeout=timeout)
                env = json.loads(raw)
                if env.get("type") == "message" and env.get("sender_id") != sender_id:
                    payload = env.get("payload", {})
                    if isinstance(payload, str):
                        payload = json.loads(payload)
                    # Decrypt if needed
                    if payload.get("encrypted") and channel_key:
                        from .crypto import decrypt
                        decrypted = decrypt(payload.get("content", ""), channel_key)
                        if decrypted:
                            payload = decrypted
                    content = payload.get("content") or payload.get("text", "")
                    if content:
                        click.echo(content)
                        break
        except Exception:
            raise click.ClickException(f"No reply within {timeout}s")


@main.command()
@click.option("--channel", "-c", default=None, help="Channel name or ID")
@click.option("--gateway", "-g", default="ws://127.0.0.1:4242", help="Gateway WebSocket URL")
@click.option("--name", "-n", default="Claude Code", help="Sender name shown in channel")
@click.option("--owner", "-o", default=None, help="Owner context shown as name@owner")
@click.option("--live", is_flag=True, default=False, help="Live mode: stream stdin line-by-line (vs pipe: send all at once)")
@click.option("--batch-ms", default=200, help="Live mode: ms to wait before flushing buffered lines (default: 200)")
def bridge(channel: str | None, gateway: str, name: str, owner: str | None, live: bool, batch_ms: int):
    """Bridge terminal output to a Port42 channel.

    Pipe mode (default): reads all stdin and sends as one message.
      example: my-script | port42 bridge -c "#ops"

    Live mode (--live): streams stdin line-by-line, batching rapid output.
      example: port42 bridge -c "#ops" --live  (then type or pipe to it)
    """
    import sys
    import threading
    import time
    import uuid
    from websockets.sync.client import connect as ws_connect

    http_url = gateway.replace("ws://", "http://").replace("wss://", "https://").rstrip("/")
    ws_url = gateway.rstrip("/") + "/ws" if not gateway.endswith("/ws") else gateway
    sender_id = f"cli-{uuid.uuid4().hex[:12]}"

    ch_id = None
    if channel:
        ch_id = _resolve_channel(channel, http_url)
        if not ch_id:
            raise click.ClickException(f"Channel not found: {channel}")

    def make_payload(text: str) -> dict:
        p: dict = {"senderName": name, "senderType": "agent", "content": text}
        if owner:
            p["senderOwner"] = owner
        return p

    def send_msg(ws, text: str):
        if not text.strip():
            return
        ws.send(json.dumps({
            "type": "message",
            "channel_id": ch_id,
            "sender_id": sender_id,
            "sender_name": name,
            "message_id": f"cli-{uuid.uuid4().hex[:16]}",
            "payload": make_payload(text),
        }))

    with ws_connect(ws_url) as ws:
        msg = json.loads(ws.recv())
        if msg.get("type") == "challenge":
            raise click.ClickException("Remote auth not supported in CLI mode")
        ws.send(json.dumps({"type": "identify", "sender_id": sender_id, "sender_name": name}))
        ws.recv()  # welcome
        if ch_id:
            ws.send(json.dumps({"type": "join", "channel_id": ch_id}))
            for _ in range(10):
                resp = json.loads(ws.recv())
                if resp.get("type") == "presence":
                    break

        if not live:
            # Pipe mode: read all stdin, send as one message
            content = sys.stdin.read()
            send_msg(ws, content)
            click.echo("sent", err=True)
        else:
            # Live mode: stream stdin line-by-line with batching
            click.echo(f"bridging stdin → #{channel or 'current'} (ctrl+c to stop)", err=True)
            buf: list[str] = []
            lock = threading.Lock()

            def flush():
                with lock:
                    if buf:
                        send_msg(ws, "\n".join(buf))
                        buf.clear()

            def flush_loop():
                while True:
                    time.sleep(batch_ms / 1000)
                    flush()

            threading.Thread(target=flush_loop, daemon=True).start()

            try:
                for line in sys.stdin:
                    with lock:
                        buf.append(line.rstrip())
            except KeyboardInterrupt:
                pass
            finally:
                flush()
            click.echo("bridge closed", err=True)


def _call(gateway: str, method: str, args: dict) -> dict:
    http = gateway.replace("ws://", "http://").replace("wss://", "https://").rstrip("/")
    data = json.dumps({"method": method, "args": args}).encode()
    req = urllib.request.Request(
        http + "/call", data=data, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req) as resp:
        result = json.loads(resp.read())
    content = result.get("content", result)
    if isinstance(content, str):
        try:
            return json.loads(content)
        except Exception:
            return {"ok": True}
    return content if isinstance(content, dict) else {"ok": True}


def _resolve_channel(name: str, gateway: str) -> str | None:
    channels = _call(gateway, "channel.list", {})
    if isinstance(channels, list):
        clean = name.lstrip("#").lower()
        for ch in channels:
            if ch.get("name", "").lower() == clean or ch.get("id") == name:
                return ch["id"]
    return None

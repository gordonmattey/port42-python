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
    """Port42 CLI — connect agents and scripts to your Port42 channels.

    \b
    Quick start:
      port42 init my-agent            # scaffold a new agent project
      port42 send "hello"             # send to current channel (localhost)
      port42 send "hello" --invite <url>  # send to a remote channel via invite URL
      port42 ask "@agent question" --invite <url>  # send and wait for reply

    \b
    Get an invite URL from Port42: right-click a channel → "Create Invitation Link".
    """
    pass


@main.command()
@click.argument("name")
@click.option("--template", default="basic", type=click.Choice(["basic", "langchain", "pipeline"]), help="Scaffold template (default: basic)")
def init(name: str, template: str):
    """Scaffold a new Port42 agent project.

    \b
    Templates:
      basic      — simple on_mention handler
      langchain  — LangChain chain with Port42CallbackHandler (typing indicator)
      pipeline   — pipeline skeleton with port creation example

    \b
    Example:
      port42 init my-agent
      port42 init my-agent --template langchain
    """
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
@click.option("--invite", "-i", default=None, help="Invite URL (port42:// or https://port42.ai/invite.html?...) — sets gateway, channel, and E2E encryption key")
def send(text: str, channel: str | None, gateway: str, name: str, owner: str | None, invite: str | None):
    """Send a message to a Port42 channel.

    \b
    With --invite, the URL provides the gateway, channel ID, and encryption key —
    no separate configuration needed. Messages are E2E encrypted automatically.

    \b
    Examples:
      port42 send "hello"                              # localhost, current channel
      port42 send "hello" -c "#ops"                   # localhost, named channel
      port42 send "hello" --invite "<url>"            # remote channel via invite URL
      port42 send "hello" --invite "<url>" --owner gordon  # shown as "Claude Code@gordon"
    """
    import uuid
    import urllib.parse as _up
    from websockets.sync.client import connect as ws_connect

    channel_key: str | None = None
    join_token: str | None = None
    if invite:
        params = dict(_up.parse_qsl(_up.urlparse(invite).query))
        if not channel:
            channel = params.get("id") or params.get("name")
        gw = params.get("gateway", "").rstrip("/")
        if gw:
            gateway = gw
        channel_key = params.get("key")
        join_token = params.get("token")

    http_url = gateway.replace("ws://", "http://").replace("wss://", "https://").rstrip("/")
    ws_url = gateway.rstrip("/") + "/ws" if not gateway.endswith("/ws") else gateway
    sender_name = name
    sender_id = f"cli-{uuid.uuid4().hex[:12]}"

    ch_id = None
    if channel:
        ch_id = _resolve_channel(channel, http_url) or channel

    with ws_connect(ws_url) as ws:
        msg = json.loads(ws.recv())
        if msg.get("type") == "challenge":
            raise click.ClickException("Remote auth not supported in CLI mode")
        identify: dict = {"type": "identify", "sender_id": sender_id, "sender_name": sender_name}
        if join_token:
            identify["token"] = join_token
        ws.send(json.dumps(identify))
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
        if channel_key:
            from port42.crypto import encrypt
            encrypted_blob = encrypt(payload, channel_key)
            payload = {"senderName": "", "senderType": "agent", "encrypted": True, "content": encrypted_blob}
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
@click.option("--invite", "-i", default=None, help="Invite URL (port42:// or https://port42.ai/invite.html?...) — sets gateway, channel, and E2E decryption key")
def ask(text: str, channel: str | None, gateway: str, timeout: int, name: str, owner: str | None, invite: str | None):
    """Send a message and wait for a reply from an agent.

    \b
    Sends TEXT to the channel, then blocks until another sender replies (or timeout).
    The reply is printed to stdout — useful for scripting and LLM tool use.

    \b
    With --invite, replies are decrypted automatically using the key in the URL.

    \b
    Examples:
      port42 ask "@synth summarise today"
      port42 ask "@agent what is the build status?" --timeout 120
      port42 ask "@agent run report" --invite "<url>" --owner gordon
    """
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
        if channel_key:
            from port42.crypto import encrypt
            payload = {"senderName": "", "senderType": "agent", "encrypted": True, "content": encrypt(payload, channel_key)}
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
@click.option("--name", "-n", default="Claude Code", help="Your agent name in the channel")
@click.option("--invite", "-i", default=None, help="Invite URL — provides gateway, channel ID, and encryption key")
@click.option("--mentions-only", is_flag=True, default=False, help="Only print messages that @mention your name")
def listen(channel: str | None, gateway: str, name: str, invite: str | None, mentions_only: bool):
    """Listen for messages in a channel and print them as NDJSON.

    \b
    Connects via WebSocket, joins the channel, and streams incoming messages to
    stdout as newline-delimited JSON. Each line is one message. Use this as the
    input to a resident agent loop.

    \b
    Examples:
      port42 listen --invite "<url>" --name "scout"
      port42 listen --invite "<url>" --name "scout" --mentions-only
      port42 listen --invite "<url>" --name "scout" | python my_agent.py
    """
    import sys
    import uuid
    import urllib.parse as _up
    from websockets.sync.client import connect as ws_connect

    channel_key: str | None = None
    join_token: str | None = None
    if invite:
        params = dict(_up.parse_qsl(_up.urlparse(invite).query))
        if not channel:
            channel = params.get("id") or params.get("name")
        gw = params.get("gateway", "").rstrip("/")
        if gw:
            gateway = gw
        channel_key = params.get("key")
        join_token = params.get("token")

    http_url = gateway.replace("ws://", "http://").replace("wss://", "https://").rstrip("/")
    ws_url = gateway.rstrip("/") + "/ws" if not gateway.endswith("/ws") else gateway
    sender_id = f"cli-{uuid.uuid4().hex[:12]}"

    ch_id = None
    if channel:
        ch_id = _resolve_channel(channel, http_url) or channel

    click.echo(f"[port42] listening as {name} in {channel or 'current channel'} (ctrl+c to stop)", err=True)

    with ws_connect(ws_url) as ws:
        msg = json.loads(ws.recv())
        if msg.get("type") == "challenge":
            raise click.ClickException("Remote auth not supported in CLI mode")
        identify: dict = {"type": "identify", "sender_id": sender_id, "sender_name": name}
        if join_token:
            identify["token"] = join_token
        ws.send(json.dumps(identify))
        ws.recv()  # welcome

        if ch_id:
            ws.send(json.dumps({"type": "join", "channel_id": ch_id}))
            for _ in range(10):
                resp = json.loads(ws.recv())
                if resp.get("type") == "presence":
                    click.echo(f"[port42] joined, {len(resp.get('online_ids', []))} members online", err=True)
                    break

        try:
            while True:
                raw = ws.recv()
                env = json.loads(raw)
                if env.get("type") != "message":
                    continue
                if env.get("sender_id") == sender_id:
                    continue

                raw_payload = env.get("payload", {})
                if isinstance(raw_payload, str):
                    try:
                        raw_payload = json.loads(raw_payload)
                    except Exception:
                        continue

                if raw_payload.get("encrypted") and channel_key:
                    from port42.crypto import decrypt
                    decrypted = decrypt(raw_payload.get("content", ""), channel_key)
                    if decrypted:
                        raw_payload = decrypted

                text = raw_payload.get("content") or raw_payload.get("text", "")
                sender_name = raw_payload.get("senderName") or env.get("sender_name", "")

                if not text:
                    continue

                is_mention = f"@{name.lower()}" in text.lower()
                if mentions_only and not is_mention:
                    continue

                out = {
                    "sender": sender_name,
                    "text": text,
                    "channel_id": env.get("channel_id", ch_id),
                    "message_id": env.get("message_id", ""),
                    "mention": is_mention,
                }
                sys.stdout.write(json.dumps(out) + "\n")
                sys.stdout.flush()
        except KeyboardInterrupt:
            click.echo("[port42] disconnected", err=True)


@main.command()
@click.option("--channel", "-c", default=None, help="Channel name or ID")
@click.option("--gateway", "-g", default="ws://127.0.0.1:4242", help="Gateway WebSocket URL")
@click.option("--name", "-n", default="Claude Code", help="Sender name shown in channel")
@click.option("--owner", "-o", default=None, help="Owner context shown as name@owner")
@click.option("--invite", "-i", default=None, help="Invite URL — provides gateway, channel ID, and encryption key")
@click.option("--live", is_flag=True, default=False, help="Stream stdin line-by-line as it arrives (default: wait for EOF and send as one message)")
@click.option("--batch-ms", default=200, help="Live mode: flush interval in ms for batching rapid lines (default: 200)")
def bridge(channel: str | None, gateway: str, name: str, owner: str | None, invite: str | None, live: bool, batch_ms: int):
    """Bridge terminal output to a Port42 channel.

    \b
    Pipe mode (default): reads all stdin to EOF and sends as a single message.
      make build 2>&1 | port42 bridge -c "#ops"
      cat report.txt  | port42 bridge -c "#ops" --owner gordon

    \b
    Live mode (--live): streams stdin line-by-line, flushing every --batch-ms.
    Useful for long-running processes where you want updates as they arrive.
      tail -f server.log | port42 bridge -c "#ops" --live
      pytest -v         | port42 bridge -c "#ops" --live --batch-ms 500
    """
    import sys
    import threading
    import time
    import uuid
    import urllib.parse as _up
    from websockets.sync.client import connect as ws_connect

    channel_key: str | None = None
    join_token: str | None = None
    if invite:
        params = dict(_up.parse_qsl(_up.urlparse(invite).query))
        if not channel:
            channel = params.get("id") or params.get("name")
        gw = params.get("gateway", "").rstrip("/")
        if gw:
            gateway = gw
        channel_key = params.get("key")
        join_token = params.get("token")

    http_url = gateway.replace("ws://", "http://").replace("wss://", "https://").rstrip("/")
    ws_url = gateway.rstrip("/") + "/ws" if not gateway.endswith("/ws") else gateway
    sender_id = f"cli-{uuid.uuid4().hex[:12]}"

    ch_id = None
    if channel:
        ch_id = _resolve_channel(channel, http_url) or channel

    def make_payload(text: str) -> dict:
        p: dict = {"senderName": name, "senderType": "agent", "content": text}
        if owner:
            p["senderOwner"] = owner
        if channel_key:
            from port42.crypto import encrypt
            return {"senderName": "", "senderType": "agent", "encrypted": True, "content": encrypt(p, channel_key)}
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
        identify: dict = {"type": "identify", "sender_id": sender_id, "sender_name": name}
        if join_token:
            identify["token"] = join_token
        ws.send(json.dumps(identify))
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


@main.command()
@click.option("--channel", "-c", default=None, help="Channel name or ID")
@click.option("--gateway", "-g", default="ws://127.0.0.1:4242", help="Gateway URL")
@click.option("--invite", "-i", default=None, help="Invite URL — provides gateway, channel ID, and encryption key")
@click.option("--count", "-n", default=20, help="Number of messages to fetch (default: 20)")
@click.option("--since", default=None, help="Only show messages after this ISO timestamp or message ID")
@click.option("--json", "as_json", is_flag=True, default=False, help="Output as NDJSON (one message per line)")
def recent(channel: str | None, gateway: str, invite: str | None, count: int, since: str | None, as_json: bool):
    """Read recent messages from a channel.

    \b
    Prints messages without sending anything — use this to check for replies.

    \b
    Examples:
      port42 recent --invite "<url>"
      port42 recent --invite "<url>" --since "2026-03-29T12:00:00Z"
      port42 recent --invite "<url>" --json
    """
    import urllib.parse as _up

    channel_key: str | None = None
    if invite:
        params = dict(_up.parse_qsl(_up.urlparse(invite).query))
        if not channel:
            channel = params.get("id") or params.get("name")
        gw = params.get("gateway", "").rstrip("/")
        if gw:
            gateway = gw
        channel_key = params.get("key")

    http_url = gateway.replace("ws://", "http://").replace("wss://", "https://").rstrip("/")

    ch_id = None
    if channel:
        ch_id = _resolve_channel(channel, http_url) or channel

    if not ch_id:
        raise click.ClickException("Could not resolve channel — provide --channel or --invite")

    result = _call(http_url, "messages.recent", {"count": count, "channel_id": ch_id})
    messages = result if isinstance(result, list) else []

    # Decrypt if needed
    if channel_key:
        from port42.crypto import decrypt
        decrypted = []
        for m in messages:
            content = m.get("content", "")
            if isinstance(content, str) and content.startswith("{"):
                try:
                    import json as _json
                    blob = _json.loads(content)
                    if blob.get("encrypted"):
                        d = decrypt(blob.get("content", ""), channel_key)
                        if d:
                            m = {**m, "content": d.get("content", ""), "sender": d.get("senderName", m.get("sender", ""))}
                except Exception:
                    pass
            decrypted.append(m)
        messages = decrypted

    # Filter by --since
    if since:
        messages = [m for m in messages if str(m.get("timestamp", "")) > since or str(m.get("message_id", "")) > since]

    if as_json:
        for m in messages:
            click.echo(json.dumps(m))
    else:
        if not messages:
            click.echo("(no messages)")
        for m in messages:
            sender = m.get("sender", "?")
            content = m.get("content", "")
            click.echo(f"{sender}: {content}")


def _resolve_channel(name: str, gateway: str) -> str | None:
    channels = _call(gateway, "channel.list", {})
    if isinstance(channels, list):
        clean = name.lstrip("#").lower()
        for ch in channels:
            if ch.get("name", "").lower() == clean or ch.get("id") == name:
                return ch["id"]
    return None

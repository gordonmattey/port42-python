import json
import os
import threading
import time
import uuid

from .types import Message, Feedback


class Agent:
    def __init__(
        self,
        name: str,
        channels: list[str] | None = None,
        trigger: str = "mention",
        gateway: str = "ws://127.0.0.1:4242",
        tokens: list[str] | None = None,
    ):
        self.name = name
        self.channels = channels or []
        self.trigger = trigger
        self.gateway_url = gateway.rstrip("/") + "/ws"
        self.http_url = gateway.rstrip("/").replace("ws://", "http://").replace("wss://", "https://")
        self._channel_tokens: dict[str, str] = dict(zip(channels or [], tokens or []))
        self._channel_ids: list[str] = []
        self._ws = None
        self._handlers: dict[str, list] = {
            "mention": [],
            "message": [],
            "feedback": [],
        }
        self._pending_calls: dict[str, threading.Event] = {}
        self._call_results: dict[str, dict] = {}
        self._recv_thread: threading.Thread | None = None
        self._state_file = f".port42/{name}.json"
        self.sender_id: str | None = None
        self._load_state()
        if not self.sender_id:
            self.sender_id = f"agent-{name}-{uuid.uuid4().hex[:12]}"

    # --- State persistence ---

    def _load_state(self):
        try:
            with open(self._state_file) as f:
                state = json.load(f)
                self.sender_id = state.get("sender_id")
        except FileNotFoundError:
            pass

    def _save_state(self):
        os.makedirs(os.path.dirname(self._state_file), exist_ok=True)
        with open(self._state_file, "w") as f:
            json.dump({"sender_id": self.sender_id}, f)

    # --- Decorators ---

    def on_mention(self, fn):
        self._handlers["mention"].append(fn)
        return fn

    def on_message(self, fn):
        self._handlers["message"].append(fn)
        return fn

    def on_feedback(self, fn):
        self._handlers["feedback"].append(fn)
        return fn

    # --- Run ---

    def run(self):
        """Block and listen for messages."""
        self._connect()
        self._recv_thread = threading.Thread(target=self._recv_loop, daemon=True)
        self._recv_thread.start()
        try:
            self._recv_thread.join()
        except KeyboardInterrupt:
            self._disconnect()

    # --- Connection ---

    def _connect(self):
        from websockets.sync.client import connect as ws_connect

        print(f"[port42] connecting to {self.gateway_url} as {self.name} ({self.sender_id})")
        self._ws = ws_connect(self.gateway_url)

        # Auth handshake
        auth_msg = json.loads(self._ws.recv())
        if auth_msg["type"] == "challenge":
            raise ConnectionError("Remote auth not yet supported — use a local gateway or provide a token")

        # Identify
        self._ws.send(json.dumps({
            "type": "identify",
            "sender_id": self.sender_id,
            "sender_name": self.name,
        }))
        welcome = json.loads(self._ws.recv())
        if welcome.get("type") != "welcome":
            raise ConnectionError(f"Expected welcome, got: {welcome}")
        self._save_state()
        print(f"[port42] connected")

        # Resolve channel names → IDs
        if self.channels and not self._channel_ids:
            self._channel_ids = self._resolve_channels(self.channels)

        # Join channels
        for ch_id in self._channel_ids:
            join = {"type": "join", "channel_id": ch_id}
            token = self._channel_tokens.get(ch_id)
            if token:
                join["token"] = token
            self._ws.send(json.dumps(join))
            # Drain until presence (joined) or error
            for _ in range(10):
                resp = json.loads(self._ws.recv())
                if resp["type"] in ("presence", "error"):
                    if resp["type"] == "error":
                        print(f"[port42] failed to join {ch_id}: {resp.get('error')}")
                    break

    def _resolve_channels(self, names: list[str]) -> list[str]:
        import urllib.request
        url = self.http_url + "/call"
        data = json.dumps({"method": "channel.list"}).encode()
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as resp:
            result = json.loads(resp.read())
        # result is {"content": "[{...}]"} or direct list
        content = result.get("content", result)
        if isinstance(content, str):
            channels = json.loads(content)
        else:
            channels = content
        name_set = {n.lstrip("#").lower() for n in names}
        ids = [ch["id"] for ch in channels if ch.get("name", "").lower() in name_set]
        if not ids:
            print(f"[port42] warning: no channels matched {names}")
        return ids

    def _disconnect(self):
        if self._ws:
            self._ws.close()

    # --- Recv loop ---

    def _recv_loop(self):
        while True:
            try:
                raw = self._ws.recv()
            except Exception as e:
                print(f"[port42] disconnected: {e}")
                break
            env = json.loads(raw)
            call_id = env.get("call_id")
            if call_id and call_id in self._pending_calls:
                self._call_results[call_id] = env
                self._pending_calls[call_id].set()
            else:
                threading.Thread(target=self._dispatch, args=(env,), daemon=True).start()

    def _dispatch(self, env: dict):
        msg_type = env.get("type")

        if msg_type == "message":
            # Skip own messages
            if env.get("sender_id") == self.sender_id:
                return
            msg = Message.from_envelope(env)
            is_mention = f"@{self.name.lower()}" in (msg.text or "").lower()
            if is_mention:
                for handler in self._handlers["mention"]:
                    try:
                        result = handler(msg)
                        if result is not None:
                            self.send(str(result), channel_id=msg.channel_id)
                    except Exception as e:
                        print(f"[port42] handler error: {e}")
            else:
                for handler in self._handlers["message"]:
                    try:
                        result = handler(msg)
                        if result is not None:
                            self.send(str(result), channel_id=msg.channel_id)
                    except Exception as e:
                        print(f"[port42] handler error: {e}")

        elif msg_type == "feedback":
            fb = Feedback.from_envelope(env)
            for handler in self._handlers["feedback"]:
                try:
                    handler(fb)
                except Exception as e:
                    print(f"[port42] feedback handler error: {e}")

    # --- Send ---

    def send(self, text: str, channel_id: str | None = None):
        ch = channel_id or (self._channel_ids[0] if self._channel_ids else None)
        if not ch:
            raise RuntimeError("No channel to send to — specify channel_id or join a channel first")
        env = {
            "type": "message",
            "channel_id": ch,
            "sender_id": self.sender_id,
            "sender_name": self.name,
            "message_id": f"agent-{uuid.uuid4().hex[:16]}",
            "payload": json.dumps({"text": text}),
        }
        self._ws.send(json.dumps(env))

    def typing(self, channel_id: str | None = None):
        ch = channel_id or (self._channel_ids[0] if self._channel_ids else None)
        if not ch:
            return
        self._ws.send(json.dumps({
            "type": "typing",
            "channel_id": ch,
            "sender_id": self.sender_id,
        }))

    # --- Bridge API ---

    def _call(self, method: str, args: dict | None = None) -> dict:
        call_id = f"sdk-{uuid.uuid4().hex[:12]}"
        event = threading.Event()
        self._pending_calls[call_id] = event
        self._ws.send(json.dumps({
            "type": "call",
            "method": method,
            "args": args or {},
            "call_id": call_id,
            "sender_id": self.sender_id,
        }))
        if not event.wait(timeout=30):
            del self._pending_calls[call_id]
            raise TimeoutError(f"{method} timed out")
        result = self._call_results.pop(call_id)
        del self._pending_calls[call_id]
        if result.get("error"):
            raise RuntimeError(result["error"])
        from .types import _parse_payload
        payload = result.get("payload", {})
        if isinstance(payload, dict):
            return payload
        return _parse_payload(payload)

    def port_push(self, port_id: str, data: dict):
        return self._call("port_push", {"id": port_id, "data": data})

    def port_exec(self, port_id: str, js: str):
        return self._call("port_exec", {"id": port_id, "js": js})

    def port_patch(self, port_id: str, search: str, replace: str):
        return self._call("port_patch", {"id": port_id, "search": search, "replace": replace})

    def port_create(self, html: str, title: str | None = None):
        return self._call("messages.send", {"text": f"```port\n{html}\n```"})

    def ports_list(self):
        return self._call("ports_list")

    def rest_call(self, url: str, method: str = "GET", secret: str | None = None, **kwargs):
        args: dict = {"url": url, "method": method}
        if secret:
            args["secret"] = secret
        args.update(kwargs)
        return self._call("rest.call", args)

    def clipboard_read(self):
        return self._call("clipboard.read")

    def clipboard_write(self, data: str):
        return self._call("clipboard.write", {"data": data})

    def notify(self, title: str, body: str | None = None):
        args: dict = {"title": title}
        if body:
            args["body"] = body
        return self._call("notify.send", args)

    def screen_capture(self, scale: float = 0.5):
        return self._call("screen_capture", {"scale": scale})

    def terminal_exec(self, command: str):
        return self._call("terminal.exec", {"command": command})

    def files_read(self, path: str):
        return self._call("files.read", {"path": path})

    def files_write(self, path: str, data: str):
        return self._call("files.write", {"path": path, "data": data})

    def storage_get(self, key: str, scope: str = "global"):
        return self._call("storage.get", {"key": key, "scope": scope})

    def storage_set(self, key: str, value, scope: str = "global"):
        return self._call("storage.set", {"key": key, "value": value, "scope": scope})

import json
import os
import threading
import time
import uuid
import urllib.parse

from .types import Message, Feedback


class Agent:
    def __init__(
        self,
        name: str,
        invite: str | None = None,
        channels: list[str] | None = None,
        trigger: str = "mention",
        gateway: str = "ws://127.0.0.1:4242",
        tokens: list[str] | None = None,
    ):
        self.name = name
        self.trigger = trigger
        self._channel_key: str | None = None
        self._invite_channel_id: str | None = None
        self._invite_token: str | None = None

        if invite:
            # Parse port42://channel?gateway=...&id=...&name=...&key=...&token=...
            parsed = urllib.parse.urlparse(invite)
            params = dict(urllib.parse.parse_qsl(parsed.query))
            raw_gw = params.get("gateway", gateway)
            self.gateway_url = raw_gw.rstrip("/") + "/ws"
            self.http_url = raw_gw.rstrip("/").replace("ws://", "http://").replace("wss://", "https://")
            self._invite_channel_id = params.get("id")
            channel_name = params.get("name", "")
            self.channels = [channel_name] if channel_name else []
            self._channel_key = params.get("key")
            self._invite_token = params.get("token")
            self._channel_tokens: dict[str, str] = {}
            if self._invite_channel_id and self._invite_token:
                self._channel_tokens[self._invite_channel_id] = self._invite_token
        else:
            self.channels = channels or []
            self.gateway_url = gateway.rstrip("/") + "/ws"
            self.http_url = gateway.rstrip("/").replace("ws://", "http://").replace("wss://", "https://")
            self._channel_tokens = dict(zip(channels or [], tokens or []))

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

        # Resolve channel IDs
        if self._invite_channel_id:
            # Invite URL gives us the channel ID directly
            self._channel_ids = [self._invite_channel_id]
        elif self.channels and not self._channel_ids:
            self._channel_ids = self._resolve_channels(self.channels)

        if not self._channel_ids:
            print(f"[port42] warning: no channels joined — could not resolve {self.channels}")

        # Join channels
        for ch_id in self._channel_ids:
            join = {"type": "join", "channel_id": ch_id}
            token = self._channel_tokens.get(ch_id)
            if token:
                join["token"] = token
            self._ws.send(json.dumps(join))
            for _ in range(10):
                resp = json.loads(self._ws.recv())
                if resp["type"] in ("presence", "error"):
                    if resp["type"] == "error":
                        print(f"[port42] failed to join {ch_id}: {resp.get('error')}")
                    else:
                        print(f"[port42] joined channel {ch_id}")
                    break

    def _resolve_channels(self, names: list[str]) -> list[str]:
        import urllib.request
        url = self.http_url + "/call"
        data = json.dumps({"method": "channel.list"}).encode()
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as resp:
            result = json.loads(resp.read())
        content = result.get("content", result)
        if isinstance(content, str):
            channels = json.loads(content)
        elif isinstance(content, list):
            channels = content
        else:
            channels = []
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
                def _safe_dispatch(e):
                    try:
                        self._dispatch(e)
                    except Exception as ex:
                        print(f"[port42] dispatch error: {ex!r}")
                threading.Thread(target=_safe_dispatch, args=(env,), daemon=True).start()

    def _dispatch(self, env: dict):
        msg_type = env.get("type")

        if msg_type == "message":
            if env.get("sender_id") == self.sender_id:
                return

            # Resolve payload: decrypt if needed
            raw_payload = env.get("payload", {})
            if isinstance(raw_payload, str):
                try:
                    raw_payload = json.loads(raw_payload)
                except Exception:
                    raw_payload = {}

            if raw_payload.get("encrypted") and self._channel_key:
                from .crypto import decrypt
                decrypted = decrypt(raw_payload.get("content", ""), self._channel_key)
                if decrypted:
                    raw_payload = decrypted

            # Support Port42 SyncPayload format (content/senderName) and legacy SDK format (text)
            text = raw_payload.get("content") or raw_payload.get("text", "")
            sender_name = raw_payload.get("senderName") or env.get("sender_name", "")
            sender_id = env.get("sender_id", "")

            if not text or not sender_id:
                return

            msg = Message(
                text=text,
                sender=sender_name,
                sender_id=sender_id,
                channel_id=env.get("channel_id", ""),
                message_id=env.get("message_id", ""),
                timestamp=env.get("timestamp", 0),
                history=raw_payload.get("history", []),
            )

            is_mention = f"@{self.name.lower()}" in msg.text.lower()
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

        # Build SyncPayload-compatible payload
        clear_payload = {
            "senderName": self.name,
            "senderType": "agent",
            "content": text,
        }

        if self._channel_key:
            from .crypto import encrypt
            blob = encrypt(clear_payload, self._channel_key)
            wire_payload = {
                "senderName": "",
                "senderType": "agent",
                "content": blob,
                "encrypted": True,
            }
        else:
            wire_payload = clear_payload

        env = {
            "type": "message",
            "channel_id": ch,
            "sender_id": self.sender_id,
            "sender_name": self.name,
            "message_id": f"agent-{uuid.uuid4().hex[:16]}",
            "payload": wire_payload,
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

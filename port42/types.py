from dataclasses import dataclass, field
import json


def _parse_payload(raw) -> dict:
    """Parse a payload that may be a JSON string, bytes, or already a dict."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, (str, bytes, bytearray)):
        try:
            return json.loads(raw)
        except Exception:
            return {}
    return {}


@dataclass
class Message:
    text: str
    sender: str
    sender_id: str
    channel_id: str
    message_id: str
    timestamp: int
    history: list = field(default_factory=list)

    @classmethod
    def from_envelope(cls, env: dict) -> "Message":
        payload = _parse_payload(env.get("payload", "{}"))
        return cls(
            text=payload.get("text", ""),
            sender=env.get("sender_name", ""),
            sender_id=env.get("sender_id", ""),
            channel_id=env.get("channel_id", ""),
            message_id=env.get("message_id", ""),
            timestamp=env.get("timestamp", 0),
            history=payload.get("history", []),
        )


@dataclass
class Feedback:
    message_id: str
    type: str
    content: str
    sender: str
    sender_id: str

    @classmethod
    def from_envelope(cls, env: dict) -> "Feedback":
        payload = _parse_payload(env.get("payload", "{}"))
        return cls(
            message_id=env.get("message_id", ""),
            type=payload.get("feedback_type", ""),
            content=payload.get("content", ""),
            sender=env.get("sender_name", ""),
            sender_id=env.get("sender_id", ""),
        )

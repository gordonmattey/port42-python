from dataclasses import dataclass, field
import json


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
        payload = json.loads(env.get("payload", "{}") or "{}")
        if isinstance(payload, bytes):
            payload = json.loads(payload)
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
        payload = json.loads(env.get("payload", "{}") or "{}")
        if isinstance(payload, bytes):
            payload = json.loads(payload)
        return cls(
            message_id=env.get("message_id", ""),
            type=payload.get("feedback_type", ""),
            content=payload.get("content", ""),
            sender=env.get("sender_name", ""),
            sender_id=env.get("sender_id", ""),
        )

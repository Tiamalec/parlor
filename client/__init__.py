"""Desktop client for the course chatroom project."""

from .network import MAX_FRAME_SIZE, NetworkClient, ProtocolError, encode_frame

__all__ = ["MAX_FRAME_SIZE", "NetworkClient", "ProtocolError", "encode_frame"]
__version__ = "1.0.0"

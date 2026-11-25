"""Reusable widgets for the V2 interface."""

from .surface import configure_scroll_surface, configure_surface
from .avatar import Avatar, UserAvatar, avatar_image, avatar_pixmap, initials
from .rendering import markdown_to_safe_html, mentions_to_safe_html
from .text_edit import MentionTextEdit
from .conversation import BrandLockup, ConversationCard, EmptyState
from .message import MediaLabel, MessageBubble, message_card_kind
from .toggle import ToggleSwitch

__all__ = ['configure_surface', 'configure_scroll_surface', 'initials', 'avatar_image', 'avatar_pixmap', 'mentions_to_safe_html', 'markdown_to_safe_html', 'message_card_kind', 'Avatar', 'UserAvatar', 'ToggleSwitch', 'MentionTextEdit', 'ConversationCard', 'MediaLabel', 'MessageBubble', 'BrandLockup', 'EmptyState']

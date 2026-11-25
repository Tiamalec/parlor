"""SQL schema and migration scripts for the chatroom database."""

from __future__ import annotations


SCHEMA_SQL = """CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    display_name TEXT,
    password_hash BLOB NOT NULL,
    salt BLOB NOT NULL,
    role TEXT NOT NULL DEFAULT 'user' CHECK(role IN ('user','admin')),
    avatar_path TEXT,
    avatar_mime TEXT,
    avatar_version INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    owner_id INTEGER REFERENCES users(id),
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS group_members (
    group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role TEXT NOT NULL DEFAULT 'member' CHECK(role IN ('member','owner')),
    nickname TEXT,
    joined_at REAL NOT NULL,
    PRIMARY KEY(group_id,user_id)
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_type TEXT NOT NULL CHECK(conversation_type IN ('group','private')),
    conversation_id TEXT NOT NULL,
    sender_id INTEGER REFERENCES users(id),
    sender_agent_id TEXT REFERENCES agents(id) ON DELETE SET NULL,
    sender_name TEXT NOT NULL,
    sender_kind TEXT NOT NULL DEFAULT 'user' CHECK(sender_kind IN ('user','agent')),
    message_type TEXT NOT NULL CHECK(message_type IN ('text','image','agent','sticker','system')),
    content_format TEXT NOT NULL DEFAULT 'plain_text' CHECK(content_format IN ('plain_text','markdown')),
    card_payload TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL DEFAULT '',
    mentions TEXT NOT NULL DEFAULT '[]',
    image_path TEXT,
    image_name TEXT,
    image_mime TEXT,
    sticker_id TEXT,
    sticker_name TEXT,
    sticker_category TEXT,
    sticker_tags TEXT NOT NULL DEFAULT '[]',
    created_at REAL NOT NULL,
    recalled_at REAL,
    recalled_by INTEGER REFERENCES users(id),
    reply_to_id INTEGER REFERENCES messages(id),
    reply_preview TEXT,
    edited_at REAL,
    client_request_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_messages_scope ON messages(conversation_type,conversation_id,id);
CREATE TABLE IF NOT EXISTS message_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER NOT NULL REFERENCES messages(id),
    event_type TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_message_events_message ON message_events(message_id);
CREATE TABLE IF NOT EXISTS personal_todos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    normalized_title TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    due_at REAL,
    priority TEXT NOT NULL DEFAULT 'normal' CHECK(priority IN ('low','normal','high')),
    completed_at REAL,
    source_type TEXT NOT NULL DEFAULT 'manual' CHECK(source_type IN ('manual','agent_extracted','chat_message')),
    source_conversation_type TEXT,
    source_conversation_id TEXT,
    source_message_id INTEGER REFERENCES messages(id) ON DELETE SET NULL,
    idempotency_key TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    manual_revision INTEGER NOT NULL DEFAULT 0,
    last_modified_by TEXT NOT NULL DEFAULT 'manual',
    created_by_agent_id TEXT REFERENCES agents(id) ON DELETE SET NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_personal_todos_owner ON personal_todos(owner_id,completed_at,due_at);
CREATE TABLE IF NOT EXISTS todo_revisions (
    owner_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    revision INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS read_cursors (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    conversation_type TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    last_message_id INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(user_id,conversation_type,conversation_id)
);
CREATE TABLE IF NOT EXISTS private_conversations (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    peer_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    opened_at REAL NOT NULL,
    PRIMARY KEY(user_id,peer_id),
    CHECK(user_id != peer_id)
);
CREATE TABLE IF NOT EXISTS private_blocks (
    blocker_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    blocked_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at REAL NOT NULL,
    PRIMARY KEY(blocker_id,blocked_id),
    CHECK(blocker_id != blocked_id)
);
CREATE TABLE IF NOT EXISTS mute_rules (
    group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    muted_by INTEGER NOT NULL REFERENCES users(id),
    created_at REAL NOT NULL,
    expires_at REAL,
    PRIMARY KEY(group_id,user_id)
);
CREATE TABLE IF NOT EXISTS agent_schedules (
    group_id INTEGER PRIMARY KEY REFERENCES groups(id) ON DELETE CASCADE,
    interval_minutes INTEGER NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    last_message_id INTEGER NOT NULL DEFAULT 0,
    next_run_at REAL NOT NULL,
    updated_by INTEGER NOT NULL REFERENCES users(id),
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id TEXT REFERENCES agents(id) ON DELETE SET NULL,
    task_type TEXT NOT NULL,
    conversation_type TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    model_profile_id TEXT,
    requested_by INTEGER REFERENCES users(id),
    status TEXT NOT NULL,
    started_at REAL NOT NULL,
    finished_at REAL,
    result_message_id INTEGER,
    error TEXT
);
CREATE TABLE IF NOT EXISTS tool_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
    agent_id TEXT,
    tool_name TEXT NOT NULL,
    conversation_type TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    input_summary TEXT NOT NULL,
    tool_mode TEXT NOT NULL DEFAULT 'auto',
    result_count INTEGER,
    status TEXT NOT NULL,
    started_at REAL NOT NULL,
    finished_at REAL,
    error TEXT
);
CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY,
    owner_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    description TEXT NOT NULL DEFAULT '',
    avatar_path TEXT,
    avatar_mime TEXT,
    avatar_version INTEGER NOT NULL DEFAULT 0,
    card TEXT NOT NULL DEFAULT '{}',
    model_profile_id TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    memory_enabled INTEGER NOT NULL DEFAULT 1,
    auto_sticker INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS group_agents (
    group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    enabled INTEGER NOT NULL DEFAULT 1,
    auto_sticker INTEGER NOT NULL DEFAULT 1,
    added_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY(group_id,agent_id)
);
CREATE TABLE IF NOT EXISTS stickers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    category TEXT NOT NULL DEFAULT '通用',
    tags TEXT NOT NULL DEFAULT '[]',
    path TEXT NOT NULL,
    mime TEXT NOT NULL,
    animated INTEGER NOT NULL DEFAULT 0,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_stickers_category ON stickers(category,enabled);
CREATE TABLE IF NOT EXISTS sticker_agents (
    sticker_id TEXT NOT NULL REFERENCES stickers(id) ON DELETE CASCADE,
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    created_at REAL NOT NULL,
    PRIMARY KEY(sticker_id,agent_id)
);
CREATE INDEX IF NOT EXISTS idx_sticker_agents_agent ON sticker_agents(agent_id,sticker_id);
CREATE TABLE IF NOT EXISTS agent_sticker_state (
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    scope_type TEXT NOT NULL CHECK(scope_type IN ('private','group')),
    scope_id TEXT NOT NULL,
    last_sent_at REAL NOT NULL,
    last_sticker_id TEXT,
    PRIMARY KEY(agent_id,scope_type,scope_id)
);
CREATE TABLE IF NOT EXISTS agent_tools (
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    tool_name TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    updated_at REAL NOT NULL,
    PRIMARY KEY(agent_id,tool_name)
);
CREATE TABLE IF NOT EXISTS group_agent_tools (
    group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    tool_name TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY(group_id,agent_id,tool_name)
);
CREATE TABLE IF NOT EXISTS agent_memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    scope_type TEXT NOT NULL CHECK(scope_type IN ('private','group')),
    scope_id TEXT NOT NULL,
    owner_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    group_id INTEGER REFERENCES groups(id) ON DELETE CASCADE,
    kind TEXT NOT NULL DEFAULT 'note',
    content TEXT NOT NULL,
    source_message_id INTEGER REFERENCES messages(id) ON DELETE SET NULL,
    created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    last_accessed_at REAL,
    enabled INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_agent_memories_scope
    ON agent_memories(agent_id,scope_type,scope_id,enabled);
CREATE TABLE IF NOT EXISTS agent_memory_settings (
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL DEFAULT 0,
    scope_type TEXT NOT NULL CHECK(scope_type IN ('private','group')),
    scope_id TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 0,
    updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY(agent_id,user_id,scope_type,scope_id)
);
CREATE TABLE IF NOT EXISTS agent_context_summaries (
    agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    scope_type TEXT NOT NULL CHECK(scope_type IN ('private','group')),
    scope_id TEXT NOT NULL,
    summary TEXT NOT NULL,
    last_message_id INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL,
    PRIMARY KEY(agent_id,scope_type,scope_id)
);
CREATE TABLE IF NOT EXISTS admin_audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_id INTEGER REFERENCES users(id),
    action TEXT NOT NULL,
    resource TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    source_ip TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_admin_audit_created ON admin_audit_log(created_at DESC);"""


MESSAGES_REBUILD_SQL = """BEGIN IMMEDIATE;
DROP TABLE IF EXISTS messages_rebuild;
CREATE TABLE messages_rebuild (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_type TEXT NOT NULL CHECK(conversation_type IN ('group','private')),
    conversation_id TEXT NOT NULL,
    sender_id INTEGER REFERENCES users(id),
    sender_agent_id TEXT REFERENCES agents(id) ON DELETE SET NULL,
    sender_name TEXT NOT NULL,
    sender_kind TEXT NOT NULL DEFAULT 'user' CHECK(sender_kind IN ('user','agent')),
    message_type TEXT NOT NULL CHECK(message_type IN ('text','image','agent','sticker','system')),
    content_format TEXT NOT NULL DEFAULT 'plain_text' CHECK(content_format IN ('plain_text','markdown')),
    card_payload TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL DEFAULT '',
    mentions TEXT NOT NULL DEFAULT '[]',
    image_path TEXT,
    image_name TEXT,
    image_mime TEXT,
    sticker_id TEXT,
    sticker_name TEXT,
    sticker_category TEXT,
    sticker_tags TEXT NOT NULL DEFAULT '[]',
    created_at REAL NOT NULL,
    recalled_at REAL,
    recalled_by INTEGER REFERENCES users(id),
    reply_to_id INTEGER REFERENCES messages(id),
    reply_preview TEXT,
    edited_at REAL,
    client_request_id TEXT
);
INSERT INTO messages_rebuild (
    id,conversation_type,conversation_id,sender_id,sender_agent_id,sender_name,
    sender_kind,message_type,content_format,content,mentions,image_path,image_name,
    image_mime,sticker_id,sticker_name,sticker_category,sticker_tags,created_at,
    recalled_at,recalled_by,reply_to_id,reply_preview,edited_at,client_request_id
)
SELECT
    id,conversation_type,conversation_id,sender_id,sender_agent_id,sender_name,
    sender_kind,message_type,content_format,content,mentions,image_path,image_name,
    image_mime,sticker_id,sticker_name,sticker_category,sticker_tags,created_at,
    recalled_at,recalled_by,reply_to_id,reply_preview,edited_at,client_request_id
FROM messages;
DROP TABLE messages;
ALTER TABLE messages_rebuild RENAME TO messages;
CREATE INDEX IF NOT EXISTS idx_messages_scope ON messages(conversation_type,conversation_id,id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_request ON messages(sender_id,client_request_id) WHERE client_request_id IS NOT NULL;
COMMIT;"""

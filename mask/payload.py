"""The shape of a model request (Anthropic Messages, as DeepSeek Harness sends it): which strings the
model reads as text, and where the agent states its working directory.

The key tables are ported from magpie's internal/redact/json.go (MIT, see docs/第三方组件.md).
"""
from __future__ import annotations

import json
from pathlib import Path
import re

# Keys the vendor matches or decodes as written: ids, names, kinds, and what is sealed or encoded.
KEPT = {'signature', 'encrypted_content', 'data', 'thoughtSignature', 'thought_signature', 'model', 'id', 'tool_use_id',
        'call_id', 'item_id', 'type', 'role', 'name', 'namespace', 'server_label', 'server_name', 'previous_response_id',
        'prompt_cache_key', 'media_type', 'mime_type', 'mimeType', 'url', 'image_url', 'file_id', 'reasoning_effort',
        'effort', 'stop_reason', 'finish_reason', 'status', 'object', 'event'}
# Request keys the vendor reads as structure; only a description in them is text.
DEFINITIONS = {'tools', 'tool_choice', 'output_config', 'output_format'}

# dsh states it as a JSON string literal inside its runtime-context text.
WORKDIR = re.compile(r'Current working directory: ("(?:[^"\\]|\\.)*")')


def _id(key):
    return key in ('id', 'ids') or key.endswith(('_id', '_ids', 'Id', 'Ids'))


def _sealed(block):
    # The vendor checks a signed thinking block against its signature; the model wrote it and never saw an original.
    return block.get('type') == 'thinking' and bool(block.get('signature'))


def rewrite(body: dict, text):
    """A copy of the request with text(s) in place of every string the model reads."""
    def walk(value, key, mode):
        if isinstance(value, str):
            if value.startswith('data:'):
                return value
            if mode == 'arguments':
                # The keys are the tool's own: a "name" or "url" there is what the model wrote.
                return text(value)
            if mode == 'definition':
                return text(value) if key == 'description' else value
            return value if key in KEPT or _id(key) else text(value)
        if isinstance(value, list):
            return [walk(item, key, mode) for item in value]
        if isinstance(value, dict):
            if mode == 'text' and _sealed(value):
                return value
            tool_use = mode == 'text' and str(value.get('type')).endswith('tool_use')
            return {k: walk(v, k, 'arguments' if tool_use and k == 'input' else mode) for k, v in value.items()}
        return value
    return {k: walk(v, k, 'definition' if k in DEFINITIONS else 'text') for k, v in body.items()}


def working_directory(body: dict):
    """The folder the agent says it works in, or None. Only its own layer counts: the system prompt and the text
    blocks of messages, not tool results, which hold file contents. The last statement wins."""
    def texts(content):
        if isinstance(content, str):
            yield content
        elif isinstance(content, list):
            yield from (b['text'] for b in content if isinstance(b, dict) and b.get('type') == 'text' and isinstance(b.get('text'), str))
    parts = [body.get('system'), *(m.get('content') for m in body.get('messages') or [] if isinstance(m, dict))]
    stated = [found for part in parts for t in texts(part) for found in WORKDIR.findall(t)]
    return Path(json.loads(stated[-1])) if stated else None

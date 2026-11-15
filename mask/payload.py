"""The shape of a model request (Anthropic Messages as DeepSeek Harness sends it, OpenAI Responses as Codex
does): which strings the model reads as text, and where the agent states its working directory.

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
DEFINITIONS = {'tools', 'tool_choice', 'output_config', 'output_format', 'text', 'client_metadata'}

# How each agent states its folder, and how to read what the pattern caught. dsh writes a JSON string literal
# inside its runtime-context text; Codex writes the path as it is, first in its <environment_context>.
WORKDIR = {'dsh': (re.compile(r'Current working directory: ("(?:[^"\\]|\\.)*")'), json.loads),
           'codex': (re.compile(r'<environment_context>\s*<cwd>(.*?)</cwd>'), str)}


def _id(key):
    return key in ('id', 'ids') or key.endswith(('_id', '_ids', 'Id', 'Ids'))


def _sealed(block):
    # The vendor checks a signed thinking block against its signature and decrypts a reasoning item itself;
    # the model wrote both and never saw an original.
    return block.get('type') in ('thinking', 'reasoning') and bool(block.get('signature') or block.get('encrypted_content'))


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
            # Codex lists its tools in an input item instead of the request's `tools`.
            inner = {} if mode != 'text' else {'input': 'arguments'} if str(value.get('type')).endswith('tool_use') \
                else {'tools': 'definition'} if value.get('type') == 'additional_tools' else {}
            return {k: walk(v, k, inner.get(k, mode)) for k, v in value.items()}
        return value
    return {k: walk(v, k, 'definition' if k in DEFINITIONS else 'text') for k, v in body.items()}


def working_directory(body: dict, agent: str):
    """The folder the agent says it works in, or None. Only its own layer counts: the system prompt and the text
    blocks of messages, not tool results, which hold file contents. The last statement wins."""
    pattern, read = WORKDIR[agent]
    def texts(content):
        if isinstance(content, str):
            yield content
        elif isinstance(content, list):
            yield from (b['text'] for b in content if isinstance(b, dict) and b.get('type') in ('text', 'input_text') and isinstance(b.get('text'), str))
    # Anthropic Messages keeps the conversation in `messages`, OpenAI Responses in `input`.
    turns = [*(body.get('messages') or []), *(body['input'] if isinstance(body.get('input'), list) else [])]
    parts = [body.get('system'), *(m.get('content') for m in turns if isinstance(m, dict))]
    stated = [found for part in parts for t in texts(part) for found in pattern.findall(t)]
    return Path(read(stated[-1])) if stated else None

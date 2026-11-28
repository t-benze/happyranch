"""Portable names and runtime/executor environment collision policy."""
from __future__ import annotations

import re

MAX_NAME = 128
MAX_VALUE_BYTES = 65536
MAX_TOTAL_BYTES = 131072
MAX_SECRETS = 128
# Case-folded, including the runtime's lower-spelled npm_config_cache.
RESERVED_NAMES = frozenset({
    'PATH', 'HOME', 'SHELL', 'USER', 'LOGNAME', 'PWD', 'OLDPWD', 'SHLVL', '_',
    'TMPDIR', 'TMP', 'TEMP', 'PYTHONDONTWRITEBYTECODE', 'PYTHONPATH',
    'PYTHONHOME', 'PYTHONSTARTUP', 'PYTHONINSPECT', 'VIRTUAL_ENV',
    'XDG_CACHE_HOME', 'XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_RUNTIME_DIR',
    'NPM_CONFIG_CACHE', 'NODE_COMPILE_CACHE', 'NODE_OPTIONS', 'GOCACHE',
    'DBUS_SESSION_BUS_ADDRESS', 'LD_PRELOAD', 'LD_LIBRARY_PATH',
    'GOOGLE_API_KEY', 'GEMINI_API_KEY', 'GROQ_API_KEY', 'XAI_API_KEY',
    'MISTRAL_API_KEY', 'DEEPSEEK_API_KEY', 'AZURE_API_KEY',
    'AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_SESSION_TOKEN',
    'AWS_PROFILE', 'AWS_REGION', 'AWS_DEFAULT_REGION',
    'LANG', 'LANGUAGE', 'TZ', 'ENV', 'SHELLOPTS', 'PS4', 'IFS', 'CDPATH',
    'GLOBIGNORE', 'PROMPT_COMMAND', 'ZDOTDIR', 'FPATH',
    'SSL_CERT_FILE', 'SSL_CERT_DIR', 'REQUESTS_CA_BUNDLE', 'CURL_CA_BUNDLE',
})
RESERVED_PREFIXES = (
    'HAPPYRANCH_', 'ANTHROPIC_', 'OPENAI_', 'CLAUDE_', 'CODEX_', 'OPENCODE_',
    'PI_', 'UV_', 'PIP_', 'DYLD_', 'BEDROCK_', 'VERTEX_', 'GOOGLE_CLOUD_',
    'AZURE_OPENAI_',
    # Interpreters/loaders consume these before application code and may echo
    # invalid values in automatic startup diagnostics. Reserve whole families.
    'LC_', 'PYTHON', 'NODE_', 'LD_', 'BASH', 'ZSH',
)



class SecretError(RuntimeError):
    """Closed diagnostics only. Never include input or dependency exceptions."""

    def __init__(self, code: str, status: int = 422) -> None:
        self.code, self.status = code, status
        super().__init__(code)


def reserved(name: str) -> bool:
    folded = name.upper()
    return folded in RESERVED_NAMES or folded.startswith(RESERVED_PREFIXES)


def validate_name(name: object) -> str:
    if not isinstance(name, str) or not 1 <= len(name) <= MAX_NAME or re.fullmatch(r'[A-Z_][A-Z0-9_]*', name, re.ASCII) is None:
        raise SecretError('invalid_name')
    if reserved(name):
        raise SecretError('reserved_name')
    return name


def validate_value(value: object) -> str:
    if not isinstance(value, str):
        raise SecretError('invalid_value')
    if not value:
        raise SecretError('empty_value')
    try:
        size = len(value.encode('utf-8'))
    except UnicodeError:
        raise SecretError('invalid_value') from None
    if size > MAX_VALUE_BYTES:
        raise SecretError('value_too_large', 413)
    if '\0' in value:
        raise SecretError('invalid_value')
    return value

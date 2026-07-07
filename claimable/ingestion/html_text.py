"""Shared HTML-fragment → readable-text conversion for ingestion adapters.

One implementation on purpose: the compiler's citation gate (verify_quotes)
requires each source_quote to reappear verbatim in the assembled source text,
so every adapter that feeds policy_text MUST normalize HTML identically — a
fix applied to one private copy but not another would silently break citation
verification for a whole source class.

Changing this function changes the text the drift monitor hashes: expect
every policy_text-shaped rulebook to need a recompile-to-baseline after any
edit here. Don't tweak casually.
"""

from __future__ import annotations

import html
import re


def html_to_text(fragment: str) -> str:
    """HTML fragment → readable text, keeping list items as '- ' bullets."""
    fragment = re.sub(r"(?s)<li[^>]*>", "\n- ", fragment)
    fragment = re.sub(r"(?s)</(p|ul|ol|li)>|<br\s*/?>", "\n", fragment)
    fragment = re.sub(r"(?s)<[^>]+>", " ", fragment)
    fragment = html.unescape(fragment)
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in fragment.splitlines()]
    return "\n".join(line for line in lines if line)

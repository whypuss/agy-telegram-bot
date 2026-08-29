"""
Markdown Formatting & Processing Engine for Telegram Bot.
Ported and refined from Hermes Agent platform formatting architecture.

Features:
- Converts GFM Pipe Tables to Telegram-friendly bullet groups
- Converts standard Markdown to Telegram MarkdownV2 format
- Preserves code blocks (fenced and inline) without corrupting syntax
- Handles headers, bold, italics, strikethrough, spoiler, blockquotes, links
- Code-block aware chunking that splits messages under 4096 UTF-16 units
- Automatic fallback plain-text cleaner when Markdown parsing fails
"""

import re
from typing import List, Optional

# Telegram limit is 4096 UTF-16 code units
MAX_TG_LENGTH = 4096

# Matches every character that MarkdownV2 requires to be backslash-escaped
# when it appears outside a code span or fenced code block.
_MDV2_ESCAPE_RE = re.compile(r'([_*\[\]()~`>#\+\-=|{}.!\\])')

# Matches a GFM table delimiter row: optional outer pipes, cells containing
# only dashes (with optional leading/trailing colons for alignment) separated by '|'.
_TABLE_SEPARATOR_RE = re.compile(
    r'^\s*\|?\s*:?-+:?\s*(?:\|\s*:?-+:?\s*){1,}\|?\s*$'
)


def utf16_len(s: str) -> int:
    """
    Count UTF-16 code units in string.
    Telegram message length limit (4096) is measured in UTF-16 code units.
    """
    return len(s.encode("utf-16-le")) // 2


def _prefix_within_utf16_limit(s: str, limit: int) -> str:
    """Return the longest prefix of s whose UTF-16 length <= limit."""
    if utf16_len(s) <= limit:
        return s
    lo, hi = 0, len(s)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if utf16_len(s[:mid]) <= limit:
            lo = mid
        else:
            hi = mid - 1
    return s[:lo]


def _custom_unit_to_cp(s: str, budget: int) -> int:
    """Return the largest codepoint offset n such that utf16_len(s[:n]) <= budget."""
    if utf16_len(s) <= budget:
        return len(s)
    lo, hi = 0, len(s)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if utf16_len(s[:mid]) <= budget:
            lo = mid
        else:
            hi = mid - 1
    return lo


def escape_mdv2(text: str) -> str:
    """Escape Telegram MarkdownV2 special characters with a preceding backslash."""
    return _MDV2_ESCAPE_RE.sub(r'\\\1', text)


def strip_markdown_v2(text: str) -> str:
    """
    Strip MarkdownV2 escape backslashes and markers to produce clean plain text.
    Used for safe fallback when Telegram rejects MarkdownV2 syntax.
    """
    # Remove escape backslashes before special characters
    cleaned = re.sub(r'\\([_*\[\]()~`>#\+\-=|{}.!\\])', r'\1', text)
    # Remove Markdown bold markers (*text* or **text**)
    cleaned = re.sub(r'\*+([^*]+)\*+', r'\1', cleaned)
    # Remove Markdown italic markers (_text_)
    cleaned = re.sub(r'(?<!\w)_+([^_]+)_+(?!\w)', r'\1', cleaned)
    # Remove Markdown strikethrough markers (~text~)
    cleaned = re.sub(r'~+([^~]+)~+', r'\1', cleaned)
    # Remove Markdown spoiler markers (||text||)
    cleaned = re.sub(r'\|\|([^|]+)\|\|', r'\1', cleaned)
    return cleaned


# ---------------------------------------------------------------------------
# Markdown Table Converter
# ---------------------------------------------------------------------------

def _is_table_row(line: str) -> bool:
    """Return True if line could plausibly be a table data row."""
    stripped = line.strip()
    return bool(stripped) and '|' in stripped


def _split_markdown_table_row(line: str) -> list[str]:
    """Split a GFM table row into stripped cell values."""
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in stripped.split("|")]


def _render_table_block_for_telegram(table_block: list[str]) -> str:
    """Render a detected GFM table as Telegram-friendly bullet groups."""
    if len(table_block) < 3:
        return "\n".join(table_block)

    headers = _split_markdown_table_row(table_block[0])
    if len(headers) < 2:
        return "\n".join(table_block)

    rendered_rows: list[str] = []
    for index, row in enumerate(table_block[2:], start=1):
        cells = _split_markdown_table_row(row)
        if len(cells) < len(headers):
            cells.extend([""] * (len(headers) - len(cells)))
        elif len(cells) > len(headers):
            cells = cells[: len(headers)]

        heading = next((cell for cell in cells if cell), f"Row {index}")
        rendered_rows.append(f"**{heading}**")
        rendered_rows.extend(
            f"• {header}: {value}" for header, value in zip(headers, cells) if header
        )

    return "\n\n".join(rendered_rows)


def _wrap_markdown_tables(text: str) -> str:
    """Rewrite GFM-style pipe tables into Telegram-friendly bullet groups."""
    if '|' not in text or '-' not in text:
        return text

    lines = text.split('\n')
    out: list[str] = []
    in_fence = False
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.lstrip()

        # Track existing fenced code blocks — never touch content inside.
        if stripped.startswith('```'):
            in_fence = not in_fence
            out.append(line)
            i += 1
            continue
        if in_fence:
            out.append(line)
            i += 1
            continue

        # Look for a header row (contains '|') immediately followed by a delimiter row.
        if (
            '|' in line
            and i + 1 < len(lines)
            and _TABLE_SEPARATOR_RE.match(lines[i + 1])
        ):
            table_block = [line, lines[i + 1]]
            j = i + 2
            while j < len(lines) and _is_table_row(lines[j]):
                table_block.append(lines[j])
                j += 1
            out.append(_render_table_block_for_telegram(table_block))
            i = j
            continue

        out.append(line)
        i += 1

    return '\n'.join(out)


# ---------------------------------------------------------------------------
# Format Markdown to Telegram MarkdownV2
# ---------------------------------------------------------------------------

def format_markdown_v2(content: str) -> str:
    """
    Convert standard markdown to Telegram MarkdownV2 format.
    Protected regions (code blocks, inline code) are extracted first so
    their contents are preserved.
    """
    if not content:
        return content

    placeholders: dict = {}
    counter = [0]

    def _ph(value: str) -> str:
        """Stash value behind a placeholder token that survives escaping."""
        key = f"\x00PH{counter[0]}\x00"
        counter[0] += 1
        placeholders[key] = value
        return key

    text = content

    # 0) Convert GFM tables to mobile-friendly rows
    text = _wrap_markdown_tables(text)

    # 1) Protect fenced code blocks (``` ... ```)
    def _protect_fenced(m):
        raw = m.group(0)
        open_end = raw.index('\n') + 1 if '\n' in raw[3:] else 3
        opening = raw[:open_end]
        body_and_close = raw[open_end:]
        body = body_and_close[:-3]
        # In MarkdownV2 code blocks, backslash and backtick must be escaped
        body = body.replace('\\', '\\\\').replace('`', '\\`')
        return _ph(opening + body + '```')

    text = re.sub(
        r'(```(?:[^\n]*\n)?[\s\S]*?```)',
        _protect_fenced,
        text,
    )

    # 2) Protect inline code (`...`)
    text = re.sub(
        r'(`[^`]+`)',
        lambda m: _ph(m.group(0).replace('\\', '\\\\')),
        text,
    )

    # 3) Convert markdown links [display](url)
    def _convert_link(m):
        display = escape_mdv2(m.group(1))
        url = m.group(2).replace('\\', '\\\\').replace(')', '\\)')
        return _ph(f'[{display}]({url})')

    text = re.sub(r'\[([^\]]+)\]\(([^()]*(?:\([^()]*\)[^()]*)*)\)', _convert_link, text)

    # 4) Convert markdown headers (# Header) -> bold *Header*
    def _convert_header(m):
        inner = m.group(1).strip()
        inner = re.sub(r'\*\*(.+?)\*\*', r'\1', inner)
        return _ph(f'*{escape_mdv2(inner)}*')

    text = re.sub(r'^#{1,6}\s+(.+)$', _convert_header, text, flags=re.MULTILINE)

    # 5) Convert bold: **text** -> *text* (MarkdownV2 bold)
    text = re.sub(
        r'\*\*(.+?)\*\*',
        lambda m: _ph(f'*{escape_mdv2(m.group(1))}*'),
        text,
    )

    # 6) Convert italic: *text* -> _text_ (MarkdownV2 italic)
    text = re.sub(
        r'(?<!\*)\*([^*\n]+)\*(?!\*)',
        lambda m: _ph(f'_{escape_mdv2(m.group(1))}_'),
        text,
    )

    # 7) Convert strikethrough: ~~text~~ -> ~text~
    text = re.sub(
        r'~~(.+?)~~',
        lambda m: _ph(f'~{escape_mdv2(m.group(1))}~'),
        text,
    )

    # 8) Convert spoiler: ||text|| -> ||text||
    text = re.sub(
        r'\|\|(.+?)\|\|',
        lambda m: _ph(f'||{escape_mdv2(m.group(1))}||'),
        text,
    )

    # 9) Convert blockquotes (> quote or **> expandable quote)
    def _convert_blockquote(m):
        prefix = m.group(1)
        content_str = m.group(2)
        if prefix.startswith('**') and content_str.endswith('||'):
            return _ph(f'{prefix} {escape_mdv2(content_str[:-2])}||')
        return _ph(f'{prefix} {escape_mdv2(content_str)}')

    text = re.sub(
        r'^((?:\*\*)?>{1,3}) (.+)$',
        _convert_blockquote,
        text,
        flags=re.MULTILINE,
    )

    # 10) Escape remaining special characters
    text = escape_mdv2(text)

    # 11) Restore placeholders in reverse order
    for key in reversed(list(placeholders.keys())):
        text = text.replace(key, placeholders[key])

    # 12) Safety net: escape unescaped ( ) { } outside code blocks
    code_split = re.split(r'(```[\s\S]*?```|`[^`]+`)', text)
    safe_parts = []
    for idx, seg in enumerate(code_split):
        if idx % 2 == 1:
            # Code span or block — leave untouched
            safe_parts.append(seg)
        else:
            # Outside code — ensure parentheses and brackets don't break parse
            def _esc_bare(m, _seg=seg):
                s = m.start()
                ch = m.group(0)
                if s > 0 and _seg[s - 1] == '\\':
                    return ch
                if ch == '(' and s > 0 and _seg[s - 1] == ']':
                    return ch
                if ch == ')':
                    before = _seg[:s]
                    if '](http' in before or '](' in before:
                        depth = 0
                        for j in range(s - 1, max(s - 2000, -1), -1):
                            if _seg[j] == '(':
                                depth -= 1
                                if depth < 0:
                                    if j > 0 and _seg[j - 1] == ']':
                                        return ch
                                    break
                            elif _seg[j] == ')':
                                depth += 1
                return '\\' + ch
            safe_parts.append(re.sub(r'[(){}]', _esc_bare, seg))
    text = ''.join(safe_parts)

    return text


# ---------------------------------------------------------------------------
# Code-Block Aware Message Chunking
# ---------------------------------------------------------------------------

def split_markdown_chunks(
    content: str,
    max_length: int = MAX_TG_LENGTH,
) -> List[str]:
    """
    Split a long message into chunks while preserving code block boundaries.
    When a split falls inside a triple-backtick code block, the fence is closed
    at the end of the chunk and reopened in the next chunk with the same language tag.
    Multi-chunk responses receive escaped indicators like (1/3).
    """
    if utf16_len(content) <= max_length:
        return [content]

    INDICATOR_RESERVE = 12  # room for " \(XX/XX\)"
    FENCE_CLOSE = "\n```"

    chunks: List[str] = []
    remaining = content
    carry_lang: Optional[str] = None

    while remaining:
        prefix = f"```{carry_lang}\n" if carry_lang is not None else ""
        headroom = max_length - INDICATOR_RESERVE - utf16_len(prefix) - utf16_len(FENCE_CLOSE)
        if headroom < 1:
            headroom = max_length // 2

        if utf16_len(prefix) + utf16_len(remaining) <= max_length - INDICATOR_RESERVE:
            chunks.append(prefix + remaining)
            break

        cp_limit = _custom_unit_to_cp(remaining, headroom)
        region = remaining[:cp_limit]

        split_at = region.rfind("\n")
        if split_at < cp_limit // 2:
            split_at = region.rfind(" ")
        if split_at < 1:
            split_at = cp_limit

        # Avoid splitting inside inline code `...`
        candidate = remaining[:split_at]
        backtick_count = candidate.count("`") - candidate.count("\\`")
        if backtick_count % 2 == 1:
            last_bt = candidate.rfind("`")
            while last_bt > 0 and candidate[last_bt - 1] == "\\":
                last_bt = candidate.rfind("`", 0, last_bt)
            if last_bt > 0:
                safe_split = max(candidate.rfind(" ", 0, last_bt), candidate.rfind("\n", 0, last_bt))
                if safe_split > cp_limit // 4:
                    split_at = safe_split

        chunk_body = remaining[:split_at]
        remaining = remaining[split_at:].lstrip()

        full_chunk = prefix + chunk_body

        in_code = carry_lang is not None
        lang = carry_lang or ""
        for line in chunk_body.split("\n"):
            stripped = line.strip()
            if stripped.startswith("```"):
                if in_code:
                    in_code = False
                    lang = ""
                else:
                    in_code = True
                    tag = stripped[3:].strip()
                    lang = tag.split()[0] if tag else ""

        if in_code:
            full_chunk += FENCE_CLOSE
            carry_lang = lang
        else:
            carry_lang = None

        chunks.append(full_chunk)

    if len(chunks) > 1:
        total = len(chunks)
        chunks = [
            f"{chunk} \\({i + 1}/{total}\\)" for i, chunk in enumerate(chunks)
        ]

    return chunks

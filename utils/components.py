from __future__ import annotations

from collections.abc import Iterable
from typing import Optional

import discord


def _chunk_text(text: str, limit: int = 3900) -> list[str]:
    text = str(text)
    if not text:
        return []
    return [text[i : i + limit] for i in range(0, len(text), limit)]


def _fit_text_parts(parts: list[str], limit: int = 3900) -> list[str]:
    """Fit all TextDisplay content under the Components V2 4000-char limit."""
    output: list[str] = []
    remaining = limit
    for part in parts:
        if remaining <= 0:
            break
        part = str(part)
        if not part:
            continue
        take = min(len(part), remaining)
        chunk = part[:take]
        if take < len(part) and take >= 1:
            chunk = chunk[:-1] + "…" if take >= 1 else chunk
        output.append(chunk)
        remaining -= len(chunk)
    return output or ["\u200b"]


def embed_to_markdown(embed: discord.Embed) -> list[str]:
    parts: list[str] = []

    if getattr(embed, "author", None) and embed.author.name:
        author = embed.author.name
        if embed.author.url:
            author = f"[{author}]({embed.author.url})"
        parts.append(f"**{author}**")

    if embed.title:
        title = embed.title
        if embed.url:
            title = f"[{title}]({embed.url})"
        parts.append(f"# {title}")

    if embed.description:
        parts.append(embed.description)

    for field in embed.fields:
        name = field.name or "\u200b"
        value = field.value or "\u200b"
        parts.append(f"**{name}**\n{value}")

    if embed.footer.text:
        footer = embed.footer.text
        timestamp = embed.timestamp
        if timestamp:
            footer = f"{footer}\n{discord.utils.format_dt(timestamp, style='R')}"
        parts.append(f"*{footer}*")
    elif embed.timestamp:
        parts.append(f"*{discord.utils.format_dt(embed.timestamp, style='R')}*")

    if not parts:
        parts.append("\u200b")

    chunks: list[str] = []
    for part in parts:
        chunks.extend(_chunk_text(part))
    return chunks


class ComponentsV2View(discord.ui.LayoutView):
    """Reusable Components V2 layout that replaces legacy embed+View messages."""

    def __init__(
        self,
        *,
        timeout: Optional[float] = 180.0,
        embed: Optional[discord.Embed] = None,
    ) -> None:
        super().__init__(timeout=timeout)
        accent = embed.color if embed and embed.color else None
        self.content_container = discord.ui.Container(accent_color=accent)
        self.add_item(self.content_container)
        if embed is not None:
            self.set_embed(embed)

    def set_embed(self, embed: discord.Embed) -> "ComponentsV2View":
        self.content_container.clear_items()
        text = _fit_text_parts(embed_to_markdown(embed))

        if embed.thumbnail.url:
            # Use a section so the thumbnail remains visible in Components V2.
            section = discord.ui.Section(
                discord.ui.TextDisplay(text[0]),
                accessory=discord.ui.Thumbnail(embed.thumbnail.url),
            )
            self.content_container.add_item(section)
            for chunk in text[1:]:
                self.content_container.add_item(discord.ui.TextDisplay(chunk))
        else:
            for chunk in text:
                self.content_container.add_item(discord.ui.TextDisplay(chunk))

        if embed.image.url:
            gallery = discord.ui.MediaGallery()
            gallery.add_item(media=embed.image.url)
            self.content_container.add_item(gallery)

        return self

    def clear_content(self) -> None:
        self.content_container.clear_items()

    def add_text(self, content: str) -> discord.ui.TextDisplay:
        item = discord.ui.TextDisplay(content)
        self.content_container.add_item(item)
        return item


def prepare_v2_view(
    view: ComponentsV2View,
    embed: Optional[discord.Embed] = None,
) -> ComponentsV2View:
    if embed is not None:
        view.set_embed(embed)
    return view


def disable_all_components(view: discord.ui.LayoutView) -> None:
    for item in view.walk_children():
        if isinstance(item, (discord.ui.Button, discord.ui.Select)):
            item.disabled = True

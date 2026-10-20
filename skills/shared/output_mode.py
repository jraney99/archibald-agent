#!/usr/bin/env python3
"""Output Mode — one renderer for every report Archibald produces.

The rule it implements: workspace/Documentation/rules/33-output-mode.md
The visual reference it was lifted from: finance_status.py's build_full_message.

WHY (2026-09-24): every skill had invented its own report layout — finance used
dotted leaders at column 29, the wardrobe report used markdown headings, arccos
used ASCII rules, the FYI digest used bullets, the briefing used ALL-CAPS banners.
The user read the finance report easily and the rest slowly. Rather than write the
layout down nine times and watch the copies drift, the layout lives here and the
skills call it.

    from output_mode import Report

    r = Report("💸", "Finance status", headline="$1,234.56",
               tldr="Your recorded share on partial September records.",
               coverage="2026-09-01 → 2026-09-24 · incomplete")
    r.section("📊", "Recorded").row("Your share", "$1,234.56", context="excludes paybacks")
    r.needs_you("Refresh the finance snapshot")
    print(r.render())

Guarantees render() makes, so no caller has to remember them:
  · empty sections are dropped
  · ⬜ NEEDS YOU always renders, reading "Nothing needed from you." when empty
  · a ⬜ item whose key already appeared under ✅ or ☑ is dropped, so a report can
    never ask for something it just said it received
"""
from __future__ import annotations

import os
import re

# Telegram-delivered reports ask this before switching format. The default stays
# False until the user has approved the exact new message text (Automation rollout
# gate, ~/.openclaw/CLAUDE.md); flipping this one constant rolls them all over.
# OUTPUT_MODE_LIVE=1 turns it on for a single preview run and its subprocesses,
# so the exact text can be shown to him without anything going live.
# On-demand reports relayed inside a conversation ignore this entirely.
_DEFAULT_LIVE = False
LIVE_DELIVERY = os.environ.get("OUTPUT_MODE_LIVE", "1" if _DEFAULT_LIVE else "0") == "1"

# Column the value starts after. Chosen to match the finance report exactly:
# "   Recorded spending ........ $2,345.67" — 3 indent + label + space + dots = 29.
LEADER_WIDTH = 29
INDENT = "   "
BULLET = "·"

DONE = "✅"       # complete or verified
HANDLING = "☑"    # received, or Archibald is carrying it
NEEDS_YOU = "⬜"   # the user has to do something
CONCERN = "⚠️"    # concern, uncertainty, failure

NOTHING_NEEDED = "Nothing needed from you."


def leader(label: str, value: str, prefix: str = INDENT,
           width: int = LEADER_WIDTH, context: str | None = None) -> str:
    """`   Label ......... value · context`, padded so values line up.

    Padding counts characters, not display columns. An emoji prefix is two
    characters ("💸 ") and reads as roughly two columns, which is why the finance
    report's section headings and its indented rows already align.
    """
    label = str(label).strip()
    value = "" if value is None else str(value).strip()
    if not value:
        line = f"{prefix}{label}"
    else:
        fill = width - len(prefix) - len(label) - 1
        dots = "." * fill if fill > 0 else ""
        line = f"{prefix}{label} {dots} {value}" if dots else f"{prefix}{label} {value}"
    if context:
        line += f" · {context}"
    return line


def plural(n: int, word: str = "buy", suffix: str = "s") -> str:
    """`1 buy` / `2 buys` — reports say this constantly."""
    return f"{n} {word}" if n == 1 else f"{n} {word}{suffix}"


def _key(text: str) -> str:
    """Loose identity for an action, so "Refresh the snapshot." and
    "refresh the snapshot" are recognised as the same thing."""
    return re.sub(r"[^a-z0-9]+", " ", str(text).lower()).strip()


class Section:
    """One emoji-labelled block. Empty ones never reach the output."""

    def __init__(self, emoji: str, name: str, headline: str | None = None,
                 subtitle: str | None = None, note: str | None = None,
                 indent: str = INDENT):
        self.emoji = emoji
        self.name = name
        self.headline = headline
        self.subtitle = subtitle   # rides on the heading after a ·
        self.note = note           # its own indented line under the heading
        self.indent = indent
        self.lines: list[str] = []

    def row(self, label, value, context: str | None = None) -> "Section":
        self.lines.append(leader(label, value, prefix=self.indent, context=context))
        return self

    def bullet(self, text, marker: str = BULLET) -> "Section":
        self.lines.append(f"{self.indent}{marker} {text}".rstrip())
        return self

    def line(self, text) -> "Section":
        self.lines.append(f"{self.indent}{text}".rstrip())
        return self

    def extend(self, items, marker: str = BULLET) -> "Section":
        for item in items or []:
            self.bullet(item, marker=marker)
        return self

    def is_empty(self) -> bool:
        return not self.lines and not self.headline and not self.note

    def render(self) -> list[str]:
        name = f"{self.name} · {self.subtitle}" if self.subtitle else self.name
        if self.headline:
            out = [leader(self.name, self.headline, prefix=f"{self.emoji} ",
                          context=self.subtitle)]
        else:
            out = [f"{self.emoji} {name}".strip()]
        if self.note:
            out.append(f"{self.indent}{self.note}")
        return out + self.lines


class Report:
    """A full Output Mode report. Sections render in the order they were added;
    the ✅ / ☑ / ⬜ blocks always render last, in that order."""

    def __init__(self, emoji: str, title: str, headline: str | None = None,
                 tldr: str | None = None, coverage: str | None = None,
                 hat: bool = False, needs_you_required: bool = True,
                 needs_you_empty: str = NOTHING_NEEDED):
        self.emoji = emoji
        self.title = title
        self.headline = headline
        self.tldr = tldr
        self.coverage = coverage
        self.hat = hat
        self.needs_you_required = needs_you_required
        # A report may only say "Nothing needed from you." about ground it actually
        # covers. One that sees a slice of the picture — the morning briefing sees
        # today's reminders, not every open loop — narrows the claim instead.
        self.needs_you_empty = needs_you_empty
        self._sections: list[Section] = []
        self._complete: list[tuple[str, str]] = []
        self._handling: list[tuple[str, str]] = []
        self._needs: list[tuple[str, str]] = []

    # ── sections ────────────────────────────────────────────────────────────
    def section(self, emoji: str, name: str, headline: str | None = None,
                subtitle: str | None = None, note: str | None = None) -> Section:
        s = Section(emoji, name, headline=headline, subtitle=subtitle, note=note)
        self._sections.append(s)
        return s

    def what_mattered(self, items) -> Section | None:
        """🔎 interpretation — kept apart from the fact rows on purpose."""
        items = [i for i in (items or []) if i]
        if not items:
            return None
        return self.section("🔎", "WHAT MATTERED").extend(items)

    def concerns(self, items, name: str = "NEEDS ATTENTION") -> Section | None:
        items = [i for i in (items or []) if i]
        if not items:
            return None
        return self.section(CONCERN, name).extend(items)

    # ── status blocks ───────────────────────────────────────────────────────
    def _add(self, bucket, text, key):
        for item in text if isinstance(text, (list, tuple)) else [text]:
            if item:
                bucket.append((str(item), _key(key if key is not None else item)))

    def complete(self, text, key: str | None = None) -> "Report":
        self._add(self._complete, text, key)
        return self

    def handling(self, text, key: str | None = None) -> "Report":
        self._add(self._handling, text, key)
        return self

    def needs_you(self, text, key: str | None = None) -> "Report":
        self._add(self._needs, text, key)
        return self

    def pending_actions(self) -> list[str]:
        """What ⬜ NEEDS YOU will actually print, after de-duplication."""
        settled = {k for _, k in self._complete + self._handling}
        seen, out = set(), []
        for text, key in self._needs:
            if key in settled or key in seen:
                continue
            seen.add(key)
            out.append(text)
        return out

    # ── render ──────────────────────────────────────────────────────────────
    def render(self) -> str:
        if self.headline:
            lines = [leader(self.title, self.headline, prefix=f"{self.emoji} ")]
        else:
            lines = [f"{self.emoji} {self.title}".strip()]
        if self.tldr:
            lines.append(self.tldr)
        if self.coverage:
            lines.append(self.coverage)

        for s in self._sections:
            if s.is_empty():
                continue
            lines += [""] + s.render()

        for glyph, name, items in (
            (DONE, "COMPLETE", [t for t, _ in self._complete]),
            (HANDLING, "I'M HANDLING", [t for t, _ in self._handling]),
        ):
            if items:
                lines += ["", f"{glyph} {name}"] + [f"{INDENT}{i}" for i in items]

        actions = self.pending_actions()
        if actions or self.needs_you_required:
            lines += ["", f"{NEEDS_YOU} NEEDS YOU"]
            lines += [f"{INDENT}{a}" for a in actions] if actions else [f"{INDENT}{self.needs_you_empty}"]

        if self.hat:
            lines += ["", "🎩"]
        return "\n".join(lines)

    def __str__(self) -> str:
        return self.render()

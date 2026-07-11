"""AI advisory prompts.

The advisor only ever receives *metadata* - names, sizes, paths, extensions,
counts - never file contents. It explains and recommends; it never deletes.
The calling command does the acting, with the usual dry-run/confirm safeguards.
"""

from __future__ import annotations

from .client import OllamaClient, OllamaUnavailable

SYSTEM_PROMPT = (
    "You are Sifty, a careful Windows maintenance assistant embedded in a CLI/TUI "
    "tool. You are given only file/app metadata, never file contents. Be concise, "
    "practical, and cautious; when unsure whether something is safe to remove, say so. "
    "Format answers in Markdown.\n\n"
    "To remove an installed program, ALWAYS recommend a proper uninstall - Sifty's own "
    "`apps` command (which uses winget under the hood), `winget uninstall`, or Windows "
    "Settings > Apps. NEVER tell the user to manually delete a program's folder under "
    "C:\\Program Files, C:\\Program Files (x86), or to hand-edit files in C:\\Windows, "
    "ProgramData, or their personal documents - Sifty refuses those paths anyway, and "
    "manual deletion leaves the registry and the system in a broken state. Do not "
    "suggest `DISM` or `sfc /scannow` unless the user explicitly reports system file "
    "corruption; they are not part of uninstalling an app.\n\n"
    "Sifty deletes safely (to the Recycle Bin, dry-run by default), so point users at "
    "Sifty's commands rather than destructive manual steps."
)

# Back-compat alias for internal callers.
_SYSTEM = SYSTEM_PROMPT


def _safe(client: OllamaClient, user_prompt: str) -> str | None:
    """Run a prompt, returning None if the AI is unavailable."""
    if not client.is_available():
        return None
    try:
        return client.chat(_SYSTEM, user_prompt)
    except OllamaUnavailable:
        return None


def explain_item(client: OllamaClient, name: str, path: str, size_human: str) -> str | None:
    """Explain what an item is and whether it's safe to remove."""
    return _safe(
        client,
        f"What is this Windows item, and is it generally safe to delete?\n"
        f"Name: {name}\nPath: {path}\nSize: {size_human}\n"
        f"Answer in 2-3 sentences.",
    )


def summarize_disk(client: OllamaClient, items: list[tuple[str, str]], question: str) -> str | None:
    """Answer a natural-language question about the biggest disk items."""
    listing = "\n".join(f"- {name}: {size}" for name, size in items)
    return _safe(
        client,
        f"Here are the largest items in a directory:\n{listing}\n\n"
        f"User question: {question}\n"
        f"Give a brief, practical answer and flag anything risky to delete.",
    )


def summarize_checkup(client: OllamaClient, findings: list, anomalies: list) -> str | None:
    """A short, plain-language read on a checkup + anomalies (metadata only).

    ``findings`` are checkup.Finding objects, ``anomalies`` are anomaly.Anomaly
    objects. Returns None when the AI is unavailable so the caller can hide the
    summary rather than show an error.
    """
    lines = [f"- {f.label}: {f.summary} [{f.severity}]" for f in findings]
    lines += [f"- Change noticed: {a.summary} [{a.severity}]" for a in anomalies]
    if not lines:
        return None
    listing = "\n".join(lines)
    return _safe(
        client,
        "Here is the result of a Windows maintenance checkup on this machine:\n"
        f"{listing}\n\n"
        "In 2-3 sentences, tell the user what matters most and what to do first. "
        "Be concrete and calm; don't invent findings that aren't listed.",
    )


def suggest_organization(client: OllamaClient, sample_names: list[str]) -> str | None:
    """Propose a folder scheme for a messy directory from sample filenames."""
    listing = "\n".join(f"- {n}" for n in sample_names[:40])
    return _safe(
        client,
        f"These are sample filenames from a cluttered folder:\n{listing}\n\n"
        f"Suggest a simple folder structure to organize them. Be concise.",
    )

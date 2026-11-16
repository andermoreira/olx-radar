"""Triagem de anúncios por comentários ou fechamento de Issues.

Permite mudar o status de uma Issue comentando emojis ou palavras-chave:
- "❤️", "👍", "interessante", "/interessante" -> radar:interested
- "📞", "💬", "contato", "/contato" -> radar:contacted
- "❌", "🗑️", "descartar", "/descartar" -> radar:discarded + fecha Issue
"""

from __future__ import annotations

import unicodedata

from .github import GitHubClient

ACTIONS = {
    "interested": {
        "keywords": {"interessante", "interesse", "/interessante", "/interesse", "❤️", "⭐", "👍"},
        "label": "radar:interested",
    },
    "contacted": {
        "keywords": {"contato", "contatado", "/contato", "/contatado", "📞", "💬", "🚀"},
        "label": "radar:contacted",
    },
    "discarded": {
        "keywords": {"descartar", "descarte", "/descartar", "/descarte", "❌", "🗑️", "👎"},
        "label": "radar:discarded",
    },
}

ALL_STATUS_LABELS = {v["label"] for v in ACTIONS.values()} | {"radar:new"}


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in text if not unicodedata.combining(c)).lower()


def evaluate_comment(comment_text: str | None) -> str | None:
    if not comment_text:
        return None
    raw = comment_text.strip()
    norm = _normalize(raw)

    for action, cfg in ACTIONS.items():
        for kw in cfg["keywords"]:
            if kw in raw or (len(kw) > 1 and kw in norm):
                return action
    return None


def apply_triage(action: str, current_labels: list[str]) -> tuple[list[str], bool]:
    if action not in ACTIONS:
        return current_labels, False

    target_label = ACTIONS[action]["label"]
    new_labels = [lb for lb in current_labels if lb not in ALL_STATUS_LABELS]
    new_labels.append(target_label)
    should_close = action == "discarded"
    return new_labels, should_close


def process_triage(
    client: GitHubClient,
    issue_number: int,
    *,
    comment_body: str | None = None,
    is_closed_event: bool = False,
) -> bool:
    issue = client.get_issue(issue_number)
    current_labels = [lb["name"] if isinstance(lb, dict) else lb for lb in issue.get("labels", [])]

    if is_closed_event:
        action = "discarded" if "radar:new" in current_labels else None
    else:
        action = evaluate_comment(comment_body)

    if not action:
        return False

    new_labels, should_close = apply_triage(action, current_labels)
    client.set_labels(issue_number, new_labels)
    if should_close and issue.get("state") != "closed":
        client.close_issue(issue_number)
    return True

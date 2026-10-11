"""Optional deep extraction. LangExtract plus a local Ollama model.

Off unless WT_DEEP_EXTRACT=1. Spans without a character interval, or whose
text does not match the source, are dropped. Nothing here calls a cloud model.
"""

from __future__ import annotations

import logging

from wt.extract_graph.ontology import Ontology, default_ontology
from wt.extract_graph.propose import DisabledDeepMode, MentionCand, Proposal, Proposer

log = logging.getLogger("wt.extract_graph.deep")


class LangExtractProposer(Proposer):
    """Second proposer. Grounded spans only."""

    name = "langextract"

    def __init__(
        self,
        ontology: Ontology | None = None,
        *,
        model_id: str = "",
        model_url: str = "http://127.0.0.1:11434",
        extract=None,
    ):
        self.ontology = ontology or default_ontology()
        self.model_id = model_id
        self.model_url = model_url
        self._extract = extract

    def propose(self, text: str) -> Proposal:
        text = text or ""
        try:
            result = self._run(text)
        except Exception as exc:
            log.warning("deep extract skipped (%s)", exc)
            return Proposal()
        mentions = []
        allowed = set(self.ontology.entity_ids())
        for row in getattr(result, "extractions", []) or []:
            mention = _grounded_mention(text, row, allowed)
            if mention is not None:
                mentions.append(mention)
        mentions.sort(key=lambda row: (row.start, row.end))
        return Proposal(mentions=mentions)

    def _run(self, text: str):
        if self._extract is not None:
            return self._extract(text)
        if not self.model_id:
            raise RuntimeError("WT_DEEP_EXTRACT is on and no Ollama model is set")
        import langextract as lx

        example = lx.data.ExampleData(
            text="CISA added CVE-2024-0001, which affects ExampleSoft Gateway.",
            extractions=[
                lx.data.Extraction(extraction_class="PublicBody", extraction_text="CISA"),
                lx.data.Extraction(extraction_class="Vulnerability", extraction_text="CVE-2024-0001"),
                lx.data.Extraction(extraction_class="SoftwareProduct", extraction_text="ExampleSoft Gateway"),
            ],
        )
        return lx.extract(
            text_or_documents=text,
            prompt_description=(
                "Extract named entities. Use only these classes: "
                + ", ".join(sorted(self.ontology.entity_ids()))
                + ". Copy the surface text exactly."
            ),
            examples=[example],
            model_id=self.model_id,
            model_url=self.model_url,
        )


def build_deep_proposer(
    enabled: bool,
    *,
    model_id: str = "",
    model_url: str = "http://127.0.0.1:11434",
    ontology: Ontology | None = None,
    extract=None,
) -> Proposer:
    if not enabled and extract is None:
        return DisabledDeepMode()
    try:
        if extract is None:
            import langextract  # noqa: F401
    except ImportError:
        log.info("langextract is not installed; deep extraction stays off")
        return DisabledDeepMode()
    return LangExtractProposer(
        ontology,
        model_id=model_id,
        model_url=model_url,
        extract=extract,
    )


def _grounded_mention(text: str, row, allowed: set[str]) -> MentionCand | None:
    interval = getattr(row, "char_interval", None)
    if interval is None:
        return None
    start = getattr(interval, "start_pos", None)
    end = getattr(interval, "end_pos", None)
    if start is None or end is None:
        return None
    start, end = int(start), int(end)
    if start < 0 or end > len(text) or start >= end:
        return None
    surface = text[start:end]
    wanted = str(getattr(row, "extraction_text", "") or "")
    if surface != wanted:
        return None
    type_id = str(getattr(row, "extraction_class", "") or "")
    if type_id not in allowed:
        return None
    return MentionCand(start, end, surface, type_id, 0.55, "langextract")

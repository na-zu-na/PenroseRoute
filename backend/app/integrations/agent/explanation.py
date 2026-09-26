"""Shared exact-permutation contract for trusted explanation facts."""
from .contracts import ExplanationOutline


def arrange_facts(facts, client=None):
    facts = tuple(facts)
    if client is None:
        return facts, "template", ()
    try:
        outline = ExplanationOutline.model_validate(client.arrange(facts))
        ids = tuple(fact.id for fact in facts)
        if len(outline.fact_ids) != len(ids) or set(outline.fact_ids) != set(ids):
            raise ValueError("Expected every trusted fact exactly once")
        lookup = {fact.id: fact for fact in facts}
        return tuple(lookup[key] for key in outline.fact_ids), "model", ()
    except Exception:
        return facts, "fallback", ("AGENT_EXPLANATION_FALLBACK",)


FORMAL_SOURCES = {"model": "agent", "template": "template", "fallback": "template_fallback"}

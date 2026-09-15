"""
B2: MAC-SQL + Security Prompt — injects security constraints into the Decomposer.

Inherits from B1 (MAC-SQL Vanilla). Modifies the Decomposer's system prompt
to include column-level security awareness. Tests whether prompt engineering
alone can reduce information exposure in intermediate results.

The security prompt is injected at init time via monkey-patching
core.const.decompose_template_bird.
"""
import os, sys
from baselines.mac_sql import MACSQLVanilla

SECURITY_APPENDIX = """
【Security Constraints — Data Privacy】
When generating SQL for intermediate sub-queries, you MUST follow these data minimization rules:
- Each sub-query should SELECT only columns that are CONSUMED by a downstream sub-query within the same decomposition plan. Do not select "extra" columns "just in case".
- Personal identifiers (names, emails, phone numbers, SSNs), financial figures (salary, revenue), and medical data should ONLY appear in intermediate results if a downstream sub-query directly references them in its FROM-clause, WHERE-clause, or JOIN condition.
- When a financial or personal column IS needed downstream, use aggregation functions (AVG, SUM, COUNT) rather than raw individual-level values whenever possible.
- Columns used only for filtering (WHERE/JOIN) do NOT need to appear in SELECT.
- Ask yourself for each column in SELECT: "Does a later step actually use this value?" If not, remove it.
"""

SECURITY_APPENDIX_DIRECT = """
【Security Constraints — Data Privacy】
When generating SQL, you MUST follow these data minimization rules:
- SELECT only the columns that the question actually asks for. Do not select extra columns "just in case".
- Personal identifiers (names, emails, phone numbers, SSNs), financial figures (salary, revenue), and medical data should ONLY appear in the output when the question explicitly requests them.
- When a financial or personal column IS requested, prefer aggregate forms (AVG, SUM, COUNT) over raw individual-level values whenever the question allows it.
- Columns used only for filtering (WHERE/JOIN) do NOT need to appear in SELECT.
- Ask yourself for each column in SELECT: "Does the question actually need this value?" If not, remove it.
"""


class MACSQLSafePrompt(MACSQLVanilla):
    """
    B2: MAC-SQL + Security Prompt.

    Same 3-Agent pipeline as B1, but the Decomposer receives an explicit
    security-policy prompt instructing it to minimize information exposure
    in intermediate results.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # The security prompt is injected on demand around B4 decompose calls
        # (see _inject_security_prompt / _restore_security_prompt), so that
        # B0-B3 runs in the same process are never contaminated.

    def _inject_security_prompt(self):
        """Inject security constraints into the BIRD and Spider templates."""
        from core import const
        if getattr(const, '_b2_patched', False):
            return

        # BIRD decomposition template (fewshot mode).
        const._decompose_template_bird_original = const.decompose_template_bird
        marker = "【Constraints】\n"
        if marker in const.decompose_template_bird:
            const.decompose_template_bird = const.decompose_template_bird.replace(
                marker, marker + SECURITY_APPENDIX, 1)
        else:
            const.decompose_template_bird = SECURITY_APPENDIX + "\n" + const.decompose_template_bird

        # Spider direct-SQL template (zeroshot mode).
        const._decompose_template_spider_original = const.decompose_template_spider
        spider_marker = "=========="
        const.decompose_template_spider = const.decompose_template_spider.replace(
            spider_marker, SECURITY_APPENDIX_DIRECT + "\n" + spider_marker, 1)

        const._b2_patched = True

    def _restore_security_prompt(self):
        """Restore the original decompose templates and clear the patch flag.

        Safe to call repeatedly; a no-op when nothing was injected. Called in a
        ``finally`` after every B4 decompose so the global templates are never
        left patched for subsequent B0-B3 calls.
        """
        from core import const
        original = getattr(const, '_decompose_template_bird_original', None)
        if original is not None:
            const.decompose_template_bird = original
            del const._decompose_template_bird_original
        original_sp = getattr(const, '_decompose_template_spider_original', None)
        if original_sp is not None:
            const.decompose_template_spider = original_sp
            del const._decompose_template_spider_original
        const._b2_patched = False

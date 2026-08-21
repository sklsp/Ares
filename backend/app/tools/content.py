"""Content tools.

`analyze_product_content` is deterministic Python so the ranking of "worst
products" is stable and testable. Only the writing tools call the LLM.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from app.llm.base import LLMError, Message
from app.logging_config import get_logger
from app.schemas.domain import ContentAnalysisResult, GeneratedContent
from app.services.content_quality import analyze_product, score_content
from app.tools.registry import ToolAccess, ToolContext, ToolError, ToolRegistry

logger = get_logger(__name__)

CATEGORY = "content"

PREAMBLE = re.compile(
    r"^\s*(here(?:'s| is)[^:\n]*:|sure[!,.][^\n]*|certainly[!,.][^\n]*|"
    r"(new |improved |updated )?(product )?(description|title)\s*:)\s*",
    re.IGNORECASE,
)

DESCRIPTION_PROMPT = """You are a senior e-commerce copywriter.
Rewrite the product description so it is specific, useful and easy to scan.

Rules:
- 70 to 120 words, plain prose, no markdown headings and no bullet characters.
- Open with what the product is and who it is for.
- Include at least one concrete detail (material, size, weight, capacity or care).
- Include one sentence starting with "Ideal for" describing a real use case.
- Never use filler like "high quality product", "amazing quality" or "buy now".
- Stay consistent with the title, category and price. Do not invent warranties,
  certifications, brand names or awards.
- Reply with the description text only. No preamble, no quotes, no labels."""

TITLE_PROMPT = """You are a senior e-commerce copywriter.
Write one improved product title.

Rules:
- Between 4 and 10 words.
- Include the product type and its most useful distinguishing detail.
- Title Case, no ALL CAPS, no emoji, no quotes, no trailing punctuation.
- Do not invent a brand name.
- Reply with the title only."""


class AnalyzeContentInput(BaseModel):
    product_ids: list[int] = Field(
        default_factory=list,
        description="Specific products to analyze. Empty means scan the catalog.",
    )
    category: str = Field(default="", description="Restrict a catalog scan to one category.")
    limit: int = Field(
        default=10, ge=1, le=50, description="How many products to return, weakest first."
    )
    max_score: int = Field(
        default=100,
        ge=0,
        le=100,
        description="Only return products scoring at or below this value.",
    )


class GenerateDescriptionInput(BaseModel):
    product_id: int = Field(ge=1, description="Product to write a new description for.")
    focus: str = Field(
        default="",
        description="Optional angle to emphasise, e.g. 'durability' or 'beginners'.",
    )


class GenerateTitleInput(BaseModel):
    product_id: int = Field(ge=1)
    focus: str = Field(default="")


def _clean(text: str) -> str:
    text = (text or "").strip()
    text = PREAMBLE.sub("", text).strip()
    if len(text) > 1 and text[0] in "\"'" and text[-1] == text[0]:
        text = text[1:-1].strip()
    return text


def _generate(ctx: ToolContext, system: str, user: str) -> str:
    try:
        response = ctx.llm.chat(
            [Message(role="system", content=system), Message(role="user", content=user)],
            temperature=0.4,
        )
    except LLMError as exc:
        raise ToolError(f"Content generation failed: {exc}") from exc

    text = _clean(response.content)
    if not text:
        raise ToolError("The model returned an empty result")
    return text


def register(reg: ToolRegistry) -> None:
    @reg.tool(
        name="analyze_product_content",
        description=(
            "Score product titles and descriptions for quality (0-100) and list the "
            "concrete problems with each. Returns the weakest products first. Use this "
            "before deciding which products need better copy."
        ),
        category=CATEGORY,
        access=ToolAccess.READ,
        input_model=AnalyzeContentInput,
        output_model=ContentAnalysisResult,
    )
    def analyze_product_content(
        ctx: ToolContext, params: AnalyzeContentInput
    ) -> ContentAnalysisResult:
        if params.product_ids:
            details = [ctx.provider.get_product(pid) for pid in params.product_ids[:50]]
        else:
            details, _ = ctx.provider.get_products(
                category=params.category or None, limit=100
            )

        analyses = [analyze_product(d) for d in details]
        analyses = [a for a in analyses if a.score <= params.max_score]
        analyses.sort(key=lambda a: (a.score, a.product_id))
        selected = analyses[: params.limit]

        return ContentAnalysisResult(
            analyzed=len(details),
            average_score=round(
                sum(a.score for a in analyses) / len(analyses), 1
            )
            if analyses
            else 0.0,
            products=selected,
        )

    @reg.tool(
        name="generate_product_description",
        description=(
            "Draft an improved description for one product. This only proposes text - "
            "it does not save anything. Follow it with update_product to apply it."
        ),
        category=CATEGORY,
        access=ToolAccess.READ,
        input_model=GenerateDescriptionInput,
        output_model=GeneratedContent,
    )
    def generate_product_description(
        ctx: ToolContext, params: GenerateDescriptionInput
    ) -> GeneratedContent:
        product = ctx.provider.get_product(params.product_id)
        current_score, issues, suggestions, *_ = score_content(
            product.title, product.description
        )

        user = (
            f"Title: {product.title}\n"
            f"Category: {product.category}\n"
            f"Price: {product.price}\n"
            f"Current description: {product.description or '(empty)'}\n"
            f"Known problems: {'; '.join(issues) or 'none'}\n"
            f"Requested improvements: {'; '.join(suggestions) or 'general polish'}"
        )
        if params.focus:
            user += f"\nEmphasise: {params.focus}"

        proposed = _generate(ctx, DESCRIPTION_PROMPT, user)
        proposed_score = score_content(product.title, proposed)[0]

        # One corrective retry when the draft did not actually improve the score.
        if proposed_score <= current_score:
            logger.info(
                "Regenerating description for product %s (%s -> %s)",
                product.id,
                current_score,
                proposed_score,
            )
            retry_issues = score_content(product.title, proposed)[2]
            retry = _generate(
                ctx,
                DESCRIPTION_PROMPT,
                f"{user}\n\nYour previous draft still had these problems: "
                f"{'; '.join(retry_issues) or 'too generic'}\nPrevious draft: {proposed}",
            )
            retry_score = score_content(product.title, retry)[0]
            if retry_score > proposed_score:
                proposed, proposed_score = retry, retry_score

        return GeneratedContent(
            product_id=product.id,
            sku=product.sku,
            field="description",
            current_value=product.description,
            proposed_value=proposed,
            rationale=(
                f"Addresses: {'; '.join(issues)}" if issues else "Refreshed for clarity"
            ),
            current_score=current_score,
            proposed_score=proposed_score,
        )

    @reg.tool(
        name="generate_product_title",
        description=(
            "Draft an improved title for one product. Proposes text only - use "
            "update_product to apply it."
        ),
        category=CATEGORY,
        access=ToolAccess.READ,
        input_model=GenerateTitleInput,
        output_model=GeneratedContent,
    )
    def generate_product_title(
        ctx: ToolContext, params: GenerateTitleInput
    ) -> GeneratedContent:
        product = ctx.provider.get_product(params.product_id)
        current_score = score_content(product.title, product.description)[0]

        user = (
            f"Current title: {product.title}\n"
            f"Category: {product.category}\n"
            f"Price: {product.price}\n"
            f"Description: {product.description[:400] or '(empty)'}"
        )
        if params.focus:
            user += f"\nEmphasise: {params.focus}"

        proposed = _generate(ctx, TITLE_PROMPT, user).splitlines()[0].strip()
        return GeneratedContent(
            product_id=product.id,
            sku=product.sku,
            field="title",
            current_value=product.title,
            proposed_value=proposed,
            rationale="More specific title including product type and key detail",
            current_score=current_score,
            proposed_score=score_content(proposed, product.description)[0],
        )

"""text_tools.slugify — turn text into a URL-safe slug.

Written against the public SDK only, like any plugin node.
"""

import re
import unicodedata

from akasha.sdk import (
    Node,
    NodeConfigSchema,
    NodeDefinition,
    NodeExecutionContext,
    NodeInput,
    NodeMetadata,
    NodeOutput,
    NodeResult,
    PortType,
)


class SlugifyNode(Node):
    definition = NodeDefinition(
        metadata=NodeMetadata(
            node_id="text_tools.slugify",
            name="Slugify",
            description="Lower-cases text and joins its words with hyphens.",
            version="1.0.0",
            category="text",
        ),
        inputs=(NodeInput("text", PortType.STRING),),
        outputs=(NodeOutput("slug", PortType.STRING),),
        config_schema=NodeConfigSchema(
            {
                "type": "object",
                "properties": {"max_length": {"type": "integer", "minimum": 1}},
                "additionalProperties": False,
            }
        ),
    )

    async def execute(self, context: NodeExecutionContext) -> NodeResult:
        text = unicodedata.normalize("NFKD", context.inputs["text"])
        words = re.findall(
            r"[a-z0-9]+", text.encode("ascii", "ignore").decode().lower()
        )
        slug = "-".join(words)[: context.config.get("max_length")]
        return NodeResult({"slug": slug.rstrip("-")})

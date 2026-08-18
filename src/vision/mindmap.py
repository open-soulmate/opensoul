"""Mind Map Generator — 思维导图生成引擎。"""

import io
import os
import time
from dataclasses import dataclass


@dataclass
class MindMapNode:
    """A node in a mind map."""

    id: str
    label: str
    children: list["MindMapNode"] = None
    color: str = ""

    def __post_init__(self):
        if self.children is None:
            self.children = []


class MindMapGenerator:
    """Generate mind map images."""

    COLORS = [
        "#e11d48",
        "#2563eb",
        "#059669",
        "#d97706",
        "#7c3aed",
        "#0891b2",
        "#65a30d",
        "#c026d3",
        "#ea580c",
        "#4f46e5",
    ]

    def __init__(self, output_dir: str = ""):
        self._output_dir = output_dir or os.path.expanduser("~/.opensoul/vision_output")
        os.makedirs(self._output_dir, exist_ok=True)

    def generate(self, root: dict, title: str = "Mind Map", layout: str = "radial") -> dict:
        """Generate a mind map from a tree structure.

        Args:
            root: {"label": "root", "children": [{"label": "child1"}, ...]}
            title: title of the mind map
            layout: "radial" or "tree"

        Returns:
            {"image_data": bytes, "format": "png", ...}
        """
        start = time.time()

        try:
            from PIL import Image, ImageDraw, ImageFont

            HAS_PIL = True
        except ImportError:
            HAS_PIL = False

        if not HAS_PIL:
            # Text fallback
            text = self._to_text(root, 0)
            return {
                "image_data": text.encode("utf-8"),
                "format": "txt",
                "engine": "text",
                "elapsed_seconds": time.time() - start,
            }

        # Generate with PIL
        w, h = 1200, 800
        img = Image.new("RGB", (w, h), "#fafafa")
        draw = ImageDraw.Draw(img)

        # Simple tree layout
        cx, cy = w // 2, h // 2
        root_node = self._dict_to_node(root)
        self._draw_node(draw, root_node, cx, cy, w, h, 0, 0)

        # Title
        if title:
            draw.text((w // 2, 20), title, fill="#333", anchor="mt")

        buf = io.BytesIO()
        img.save(buf, format="PNG")

        return {
            "image_data": buf.getvalue(),
            "format": "png",
            "width": w,
            "height": h,
            "engine": "pillow",
            "elapsed_seconds": time.time() - start,
        }

    def _dict_to_node(self, d: dict) -> MindMapNode:
        children = [self._dict_to_node(c) for c in d.get("children", [])]
        return MindMapNode(id=d.get("id", ""), label=d.get("label", ""), children=children)

    def _draw_node(
        self, draw, node: MindMapNode, x: int, y: int, w: int, h: int, depth: int, index: int
    ):
        color = self.COLORS[depth % len(self.COLORS)]

        # Draw this node
        bbox = draw.textbbox((x, y), node.label)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        pad = 8
        draw.rounded_rectangle(
            [x - tw // 2 - pad, y - th // 2 - pad, x + tw // 2 + pad, y + th // 2 + pad],
            radius=8,
            fill=color if depth == 0 else "white",
            outline=color,
        )
        draw.text((x, y), node.label, fill="white" if depth == 0 else color, anchor="mm")

        # Draw children
        if node.children:
            n = len(node.children)
            spread = min(w * 0.7, 600) // max(n, 1)
            start_x = x - (n - 1) * spread // 2
            child_y = y + 80 + depth * 20

            for i, child in enumerate(node.children):
                cx = start_x + i * spread
                # Draw connection line
                draw.line([(x, y + th // 2 + pad), (cx, child_y - 15)], fill="#ddd", width=2)
                self._draw_node(draw, child, cx, child_y, w // 2, h, depth + 1, i)

    def _to_text(self, node: dict, depth: int) -> str:
        indent = "  " * depth
        prefix = "●" if depth == 0 else "├──"
        lines = [f"{indent}{prefix} {node.get('label', '')}"]
        for child in node.get("children", []):
            lines.append(self._to_text(child, depth + 1))
        return "\n".join(lines)

"""Chart Generator — 图表生成引擎。"""

import io
import os
import time
from dataclasses import dataclass

# Check available backends
HAS_MATPLOTLIB = False
HAS_PIL = False

try:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.font_manager  # noqa: F401 as fm
    import matplotlib.pyplot as plt

    HAS_MATPLOTLIB = True
except ImportError:
    pass

try:
    from PIL import Image, ImageDraw, ImageFont  # noqa: F401

    HAS_PIL = True
except ImportError:
    pass


@dataclass
class ChartResult:
    """Result of chart generation."""

    image_data: bytes
    format: str = "png"
    width: int = 0
    height: int = 0
    chart_type: str = ""
    engine: str = ""
    elapsed_seconds: float = 0.0
    size_bytes: int = 0

    def __post_init__(self):
        if not self.size_bytes:
            self.size_bytes = len(self.image_data)


class ChartGenerator:
    """Generate charts and visualizations."""

    def __init__(self, output_dir: str = ""):
        self._output_dir = output_dir or os.path.expanduser("~/.opensoul/vision_output")
        os.makedirs(self._output_dir, exist_ok=True)
        self._generated = 0
        self._errors = 0

    @property
    def engine(self) -> str:
        if HAS_MATPLOTLIB:
            return "matplotlib"
        if HAS_PIL:
            return "pillow"
        return "none"

    def bar_chart(
        self,
        labels: list[str],
        values: list[float],
        title: str = "",
        xlabel: str = "",
        ylabel: str = "",
        color: str = "#e11d48",
        figsize: tuple[int, int] = (10, 6),
    ) -> ChartResult:
        """Generate a bar chart."""
        start = time.time()

        if HAS_MATPLOTLIB:
            result = self._mpl_bar(labels, values, title, xlabel, ylabel, color, figsize)
        elif HAS_PIL:
            result = self._pil_bar(labels, values, title, color)
        else:
            result = self._text_chart(labels, values, title, "bar")

        result.elapsed_seconds = time.time() - start
        self._generated += 1
        return result

    def line_chart(
        self,
        x: list,
        series: dict[str, list[float]],
        title: str = "",
        xlabel: str = "",
        ylabel: str = "",
        figsize: tuple[int, int] = (10, 6),
    ) -> ChartResult:
        """Generate a line chart with multiple series."""
        start = time.time()

        if HAS_MATPLOTLIB:
            fig, ax = plt.subplots(figsize=figsize)
            colors = ["#e11d48", "#2563eb", "#059669", "#d97706", "#7c3aed"]
            for i, (name, values) in enumerate(series.items()):
                ax.plot(x, values, label=name, color=colors[i % len(colors)], linewidth=2)
            if title:
                ax.set_title(title, fontsize=14, fontweight="bold")
            if xlabel:
                ax.set_xlabel(xlabel)
            if ylabel:
                ax.set_ylabel(ylabel)
            if len(series) > 1:
                ax.legend()
            ax.grid(True, alpha=0.3)
            fig.tight_layout()

            buf = io.BytesIO()
            fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
            plt.close(fig)
            buf.seek(0)
            data = buf.read()
            self._generated += 1
            return ChartResult(
                image_data=data, format="png", chart_type="line", engine="matplotlib"
            )

        # Fallback
        labels = [str(x_val) for x_val in x]
        first_series = list(series.values())[0] if series else []
        result = self._text_chart(labels, first_series, title, "line")
        result.elapsed_seconds = time.time() - start
        return result

    def pie_chart(
        self,
        labels: list[str],
        values: list[float],
        title: str = "",
        figsize: tuple[int, int] = (8, 8),
    ) -> ChartResult:
        """Generate a pie chart."""
        start = time.time()

        if HAS_MATPLOTLIB:
            fig, ax = plt.subplots(figsize=figsize)
            colors = [
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
            wedges, texts, autotexts = ax.pie(
                values,
                labels=labels,
                colors=colors[: len(labels)],
                autopct="%1.1f%%",
                startangle=90,
                pctdistance=0.85,
            )
            for text in autotexts:
                text.set_fontsize(10)
            if title:
                ax.set_title(title, fontsize=14, fontweight="bold")
            fig.tight_layout()

            buf = io.BytesIO()
            fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
            plt.close(fig)
            buf.seek(0)
            data = buf.read()
            self._generated += 1
            return ChartResult(
                image_data=data,
                format="png",
                chart_type="pie",
                engine="matplotlib",
                elapsed_seconds=time.time() - start,
            )

        result = self._text_chart(labels, values, title, "pie")
        result.elapsed_seconds = time.time() - start
        return result

    def scatter_plot(
        self,
        x: list[float],
        y: list[float],
        title: str = "",
        xlabel: str = "",
        ylabel: str = "",
        figsize: tuple[int, int] = (10, 6),
    ) -> ChartResult:
        """Generate a scatter plot."""
        start = time.time()

        if HAS_MATPLOTLIB:
            fig, ax = plt.subplots(figsize=figsize)
            ax.scatter(x, y, c="#e11d48", alpha=0.7, s=60, edgecolors="white", linewidth=0.5)
            if title:
                ax.set_title(title, fontsize=14, fontweight="bold")
            if xlabel:
                ax.set_xlabel(xlabel)
            if ylabel:
                ax.set_ylabel(ylabel)
            ax.grid(True, alpha=0.3)
            fig.tight_layout()

            buf = io.BytesIO()
            fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
            plt.close(fig)
            buf.seek(0)
            data = buf.read()
            self._generated += 1
            return ChartResult(
                image_data=data,
                format="png",
                chart_type="scatter",
                engine="matplotlib",
                elapsed_seconds=time.time() - start,
            )

        labels = [str(v) for v in x]
        result = self._text_chart(labels, y, title, "scatter")
        result.elapsed_seconds = time.time() - start
        return result

    def _mpl_bar(self, labels, values, title, xlabel, ylabel, color, figsize) -> ChartResult:
        fig, ax = plt.subplots(figsize=figsize)
        bars = ax.bar(labels, values, color=color, edgecolor="white", linewidth=0.5)

        # Add value labels on bars
        for bar, val in zip(bars, values):
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + max(values) * 0.02,
                f"{val:.1f}",
                ha="center",
                va="bottom",
                fontsize=9,
            )

        if title:
            ax.set_title(title, fontsize=14, fontweight="bold")
        if xlabel:
            ax.set_xlabel(xlabel)
        if ylabel:
            ax.set_ylabel(ylabel)
        ax.grid(True, axis="y", alpha=0.3)
        fig.tight_layout()

        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
        plt.close(fig)
        buf.seek(0)
        return ChartResult(
            image_data=buf.read(), format="png", chart_type="bar", engine="matplotlib"
        )

    def _pil_bar(self, labels, values, title, color) -> ChartResult:
        """Simple bar chart using Pillow only."""
        w, h = 800, 500
        img = Image.new("RGB", (w, h), "white")
        draw = ImageDraw.Draw(img)

        max_val = max(values) if values else 1
        bar_w = min(80, (w - 100) // len(values) - 10)
        x_start = 50
        chart_bottom = h - 80

        for i, (label, val) in enumerate(zip(labels, values)):
            bar_h = int((val / max_val) * (chart_bottom - 60))
            x = x_start + i * (bar_w + 10)
            y = chart_bottom - bar_h

            # Parse hex color
            _r, _g, _b = int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)
            draw.rectangle([x, y, x + bar_w, chart_bottom], fill=color, outline="white")
            draw.text((x + bar_w // 2, chart_bottom + 5), label[:8], fill="gray", anchor="mt")
            draw.text((x + bar_w // 2, y - 15), str(val), fill="black", anchor="mb")

        if title:
            draw.text((w // 2, 20), title, fill="black", anchor="mt")

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return ChartResult(
            image_data=buf.getvalue(),
            format="png",
            chart_type="bar",
            engine="pillow",
            width=w,
            height=h,
        )

    def _text_chart(self, labels, values, title, chart_type) -> ChartResult:
        """Ultra-minimal text-based chart (fallback when no graphics library)."""
        lines = []
        if title:
            lines.append(title)
            lines.append("=" * len(title))
        max_val = max(values) if values else 1
        max_label = max(len(str(l)) for l in labels) if labels else 5
        for label, val in zip(labels, values):
            bar_len = int((val / max_val) * 30) if max_val > 0 else 0
            lines.append(f"{str(label):<{max_label}} | {'█' * bar_len} {val}")
        text = "\n".join(lines)
        return ChartResult(
            image_data=text.encode("utf-8"),
            format="txt",
            chart_type=chart_type,
            engine="text",
        )

    def save_output(self, data: bytes, name: str, fmt: str = "png") -> str:
        path = os.path.join(self._output_dir, f"{name}.{fmt}")
        with open(path, "wb") as f:
            f.write(data)
        return path

    def list_outputs(self) -> list[dict]:
        results = []
        for name in sorted(os.listdir(self._output_dir), reverse=True):
            fp = os.path.join(self._output_dir, name)
            if os.path.isfile(fp):
                stat = os.stat(fp)
                results.append(
                    {
                        "filename": name,
                        "size_bytes": stat.st_size,
                        "created_at": stat.st_mtime,
                    }
                )
        return results[:100]

    def stats(self) -> dict:
        return {
            "engine": self.engine,
            "backends": {"matplotlib": HAS_MATPLOTLIB, "pillow": HAS_PIL},
            "total_generated": self._generated,
            "errors": self._errors,
            "output_dir": self._output_dir,
        }

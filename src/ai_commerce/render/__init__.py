"""Provider-neutral frame plans and local, verified FFmpeg rendering."""

from .models import Canvas, EncoderConfig, RenderError, RenderItem, RenderPlan, SourceMediaRef
from .manifest import RenderManifest
from .renderer import FFmpegRenderer, RendererAdapter

__all__ = [
    "Canvas", "EncoderConfig", "RenderError", "RenderItem", "RenderPlan",
    "SourceMediaRef", "RenderManifest", "FFmpegRenderer", "RendererAdapter",
]

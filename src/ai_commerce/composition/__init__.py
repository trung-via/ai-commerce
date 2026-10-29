"""A05 C03 editable composition and provider-neutral compile API.

Creating or saving compositions performs no generation or rendering.
"""

from ._validation import CompositionValidationError, CompositionVersionConflict
from .authoring import compose_images, create_composition, direct_image, edit_composition
from .compiler import (
    CompilationDiagnostic, CompileResult, CompositionPlan, MediaResolver,
    RegistryMediaResolver, ResolvedMedia, compile_composition,
)
from .models import (
    Canvas, CompositionParent, CreativeComposition, FrameRate, RenderSettings,
    SceneItem, Track,
)
from .sources import BriefSnapshot, MediaReference, pinned_selection
from .store import CompositionStore

__all__ = [
    "BriefSnapshot", "Canvas", "CompilationDiagnostic", "CompileResult",
    "CompositionParent", "CompositionPlan", "CompositionStore",
    "CompositionValidationError", "CompositionVersionConflict",
    "CreativeComposition", "FrameRate", "MediaReference", "MediaResolver",
    "RegistryMediaResolver", "RenderSettings", "ResolvedMedia", "SceneItem",
    "Track", "compile_composition", "compose_images", "create_composition",
    "direct_image", "edit_composition", "pinned_selection",
]

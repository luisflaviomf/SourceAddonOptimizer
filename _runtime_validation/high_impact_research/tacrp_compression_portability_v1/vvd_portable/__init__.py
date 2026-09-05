"""Public API for the experiment-scoped portable VVD codec."""

from .decoder import decode_vvd, inspect_container
from .encoder import encode_vvd
from .format import (
    CODEC_DOMAIN,
    FIXED_HEADER_SIZE,
    MAGIC,
    VERSION,
    ContainerSummary,
    EncodeResult,
)

__all__ = (
    "CODEC_DOMAIN",
    "FIXED_HEADER_SIZE",
    "MAGIC",
    "VERSION",
    "ContainerSummary",
    "EncodeResult",
    "decode_vvd",
    "encode_vvd",
    "inspect_container",
)


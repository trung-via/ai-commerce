"""A17.3: offline API definitions and synthetic rehearsal; LIVE is unavailable."""
from .capability import NativeBinding, capability_for, submit_live
from .direct_post import OfflineDirectPostAdapter, propose_human_opt_in
from .multichannel import ChannelProjection, adapter_catalog, aggregate_channels, project_channel, synthetic_metric
from .schemas import AdapterError, DirectPostRequest, HumanConsent, SyntheticReceipt

__all__ = ["AdapterError", "DirectPostRequest", "HumanConsent", "SyntheticReceipt", "NativeBinding",
           "capability_for", "submit_live", "OfflineDirectPostAdapter", "propose_human_opt_in",
           "ChannelProjection", "adapter_catalog", "aggregate_channels", "project_channel", "synthetic_metric"]

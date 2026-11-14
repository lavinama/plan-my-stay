"""The one module every seat imports to talk over BAND (docs/SEAT_PROTOCOL.md, section 6).

`core` holds the SDK-free logic and is what tests exercise; `sdk` is the only module that
touches the BAND SDK, and it imports it lazily, so importing this package needs no SDK.
"""

from plan_my_stay.band_adapter.core import NoReply, Post, RetryTracker, process_incoming
from plan_my_stay.band_adapter.directory import SeatDirectory, SeatDirectoryError
from plan_my_stay.band_adapter.sdk import BandSetupError, build_adapter, build_agent, make_graph_factory, run_seat

__all__ = [
    "BandSetupError",
    "NoReply",
    "Post",
    "RetryTracker",
    "SeatDirectory",
    "SeatDirectoryError",
    "build_adapter",
    "build_agent",
    "make_graph_factory",
    "process_incoming",
    "run_seat",
]

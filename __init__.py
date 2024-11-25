from nautobot.apps.jobs import register_jobs

from .jobs import LoadCATNIXData, RequestPeeringCATNIX

register_jobs(LoadCATNIXData, RequestPeeringCATNIX)

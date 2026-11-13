from hub.models import SqrTrackerSettings


def classic_tracker_visible() -> bool:
    value = SqrTrackerSettings.objects.filter(pk=1).values_list('show_classic_sqr_tracker', flat=True).first()
    return True if value is None else value

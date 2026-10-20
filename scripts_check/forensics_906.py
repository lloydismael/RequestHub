"""One-off DB forensics for REQ-00906 (pk 906). Safe, read-only."""
import django, os, sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'request_hub.settings')
django.setup()

from hub.models import Request, RequestLifecycleEvent, RequestCommunication

r = Request.objects.get(pk=906)
print('=== REQUEST 906 ===')
print('lifecycle_stage:', r.lifecycle_stage)
print('status:', r.status)
print('assignment_revision:', r.assignment_revision)
print('engineer_id:', r.engineer_id)
if r.engineer:
    print('engineer:', r.engineer.username, getattr(r.engineer, 'get_full_name', lambda: '')())
print('updated_at:', r.updated_at)
print('created_at:', getattr(r, 'created_at', None))

print()
print('=== LIFECYCLE EVENTS ===')
for e in RequestLifecycleEvent.objects.filter(request_id=906).order_by('sequence'):
    print(
        'seq={seq} event={ev} stage={st} prev={pv} rev={rev} source={src} '
        'synthetic={syn} key={key} actor={actor} at={at}'.format(
            seq=e.sequence, ev=e.event_type, st=e.stage, pv=e.previous_stage,
            rev=e.assignment_revision, src=e.source, syn=e.is_synthetic,
            key=e.idempotency_key, actor=e.actor, at=e.occurred_at,
        )
    )

print()
print('=== COMMUNICATION (OUTLOOK) ===')
for c in RequestCommunication.objects.filter(request_id=906):
    dtype = getattr(c, 'communication_type', None)
    if dtype is None:
        dtype = getattr(c, 'type', None)
    print(
        'channel={ch} type={ty} subject={subj} created={c} user={u}'.format(
            ch=getattr(c, 'channel', '?'), ty=dtype,
            subj=getattr(c, 'subject', '?'),
            c=getattr(c, 'created_at', '?'),
            u=getattr(c, 'user', None),
        )
    )
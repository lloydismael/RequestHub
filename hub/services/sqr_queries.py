from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.db.models import Case, Count, DateField, DateTimeField, DecimalField, F, Func, Q, Sum, TextField, Value, When
from django.db.models.functions import Cast, Coalesce, TruncDate

from accounts.models import User
from hub.models import MANILA_TZ, SqrSubmission
from hub.services.dashboard_queries import person_name
from hub.services.pagination import paginate


class DisplayText(Func):
    function = 'TO_CHAR'
    output_field = TextField()


class RoundMoney(Func):
    arity = 1
    output_field = DecimalField(max_digits=20, decimal_places=2)

    def as_postgresql(self, compiler, connection, **extra_context):
        value, params = compiler.compile(self.source_expressions[0])
        # Decimal.quantize uses half-even, unlike PostgreSQL's ordinary numeric ROUND.
        return (f'(SELECT CASE WHEN ABS(v * 100 - TRUNC(v * 100)) = 0.5 '
                f'THEN (TRUNC(v * 100) + CASE WHEN MOD(ABS(TRUNC(v * 100)), 2) = 1 '
                f'THEN SIGN(v) ELSE 0 END) / 100 ELSE ROUND(v, 2) END FROM (SELECT ({value}) AS v) AS money)', params)


def price_expression():
    gross = Coalesce('sse_amount', Decimal('0')) + Coalesce('pm_amount', Decimal('0')) + Coalesce('managed_support_amount', Decimal('0'))
    amount = RoundMoney(gross * (Value(Decimal('1')) - Cast(Coalesce('discount_rate', 0), DecimalField(max_digits=10, decimal_places=4)) / Decimal('100')))
    return Coalesce(Case(When(_gross=0, then=F('quotation_total_price')), default=amount), Decimal('0'))


def annotate_tracker(queryset, today):
    closed = Q(proposal_status__in=['closed_lost', 'closed_canceled'])
    won = Q(proposal_status='closed_won')
    billed = Q(revenue_status='billed') | (Q(revenue_status='') & Q(revenue_date__isnull=False))
    finished = Q(overall_status='completed') | Q(delivery_health='completed') | Q(delivery_actual_finish_date__isnull=False)
    queryset = queryset.annotate(
        _pipeline=Case(When(closed, then=Value('closed')), When(~won, then=Value('opportunity')),
                       When(Q(overall_status='cancelled') | billed, then=Value('closed')),
                       When(finished, then=Value('billing')), default=Value('delivery')),
        _gross=Coalesce('sse_amount', Decimal('0')) + Coalesce('pm_amount', Decimal('0')) + Coalesce('managed_support_amount', Decimal('0')),
    ).annotate(_price=price_expression())
    dangerous = Q(status='for_revision') | Q(_pipeline='opportunity', status='reviewed', validity_due_date__lt=today) | Q(_pipeline='delivery', delivery_target_finish_date__lt=today) | Q(_pipeline='delivery', delivery_health='at_risk')
    score = Case(When(status='for_revision', then=3), When(status='submitted', then=2), default=0)
    score += Case(When(_pipeline='opportunity', status='reviewed', validity_due_date__lt=today, then=3),
                  When(_pipeline='opportunity', status='reviewed', validity_due_date__lte=today + timedelta(days=7), then=2), default=0)
    score += Case(When(_pipeline='delivery', delivery_target_finish_date__lt=today, then=3), default=0)
    score += Case(When(_pipeline='delivery', delivery_health='at_risk', then=3), When(_pipeline='delivery', delivery_health='off_track', then=2), default=0)
    return queryset.annotate(_attention=Case(When(status='on_hold', then=False), When(dangerous, then=True), default=False),
                             _score=Case(When(status='on_hold', then=0), default=score))


def filter_tracker(queryset, params, user, *, prototype=False):
    if not prototype and params.get('focus'):
        try:
            focus = int(params['focus'])
        except (TypeError, ValueError):
            return queryset.none()
        return queryset.filter(pk=focus) if 0 < focus <= 2**63 - 1 else queryset.none()
    fields = ['reference_code', 'customer_name', 'project_title', 'customer_company', 'customer_contact', 'remarks', 'hold_reason', 'linked_request__reference_code']
    parts = []
    for field in fields:
        parts.extend([Coalesce(F(field), Value(''), output_field=TextField()), Value(' ')])
    for relation in ('engineer', 'pm_esg_reviewer'):
        parts.extend([person_name(relation), Value(' ')])
    if not prototype:
        for field in SqrSubmission._meta.fields:
            if field.name not in fields and not field.is_relation:
                parts.extend([Coalesce(Cast(F(field.name), TextField()), Value(''), output_field=TextField()), Value(' ')])
                if field.choices:
                    label = Case(*[When(**{field.name: value}, then=Value(text)) for value, text in field.choices], default=Value(''))
                    parts.extend([label, Value(' ')])
                if isinstance(field, (DateField, DateTimeField)):
                    value = TruncDate(field.name, tzinfo=MANILA_TZ) if isinstance(field, DateTimeField) else F(field.name)
                    parts.extend([Coalesce(DisplayText(value, Value('Mon DD, YYYY')), Value(''), output_field=TextField()), Value(' ')])
                if isinstance(field, DecimalField):
                    parts.extend([Coalesce(DisplayText(F(field.name), Value('FM999,999,999,999,999,990.00')), Value(''), output_field=TextField()), Value(' ')])
        parts.extend([Coalesce(DisplayText(F('_price'), Value('FM999,999,999,999,999,990.00')), Value(''), output_field=TextField()), Value(' ')])
        for relation in ('assigned_pm', 'assigned_sse'):
            parts.extend([person_name(relation), Value(' ')])
    query = (params.get('q') or '').strip()[:200]
    if query:
        content = [part for part in parts if not (isinstance(part, Value) and part.value == ' ')]
        queryset = queryset.annotate(_search=Func(Value(' '), *content, function='CONCAT_WS', output_field=TextField()))
        for term in (query.lower().split() if prototype else [query.lower()]):
            queryset = queryset.filter(_search__icontains=term)
    if prototype:
        return queryset
    assigned = params.get('assigned', 'mine' if user.role == User.Roles.PM_ESG else 'all')
    if assigned == 'mine':
        queryset = queryset.filter(pm_esg_reviewer=user)
    for name in ('status', 'proposal_status', 'overall_status', 'delivery_health', 'revenue_source'):
        value = params.get(name)
        if value:
            queryset = queryset.filter(**{name: value})
    if params.get('group'):
        queryset = queryset.filter(customer_company__icontains=params['group'].strip())
    for parameter, lookup in (('date_from', 'created_at__date__gte'), ('date_to', 'created_at__date__lte')):
        try:
            value = date.fromisoformat(params.get(parameter, ''))
        except ValueError:
            continue
        queryset = queryset.filter(**{lookup: value})
    for parameter, lookup in (('amount_min', '_price__gte'), ('amount_max', '_price__lte')):
        try:
            value = Decimal(params.get(parameter, '').replace(',', ''))
            if not value.is_finite():
                continue
        except InvalidOperation:
            continue
        limit = Decimal('1e18')
        if value > limit:
            if parameter == 'amount_min':
                return queryset.none()
            continue
        if value < -limit:
            if parameter == 'amount_max':
                return queryset.none()
            continue
        queryset = queryset.filter(**{lookup: value})
    if params.get('linked') == '1':
        queryset = queryset.filter(linked_request__isnull=False)
    if params.get('sse') == '1':
        queryset = queryset.filter(assigned_sse__isnull=False)
    if params.get('billed') == '1':
        queryset = queryset.filter(Q(revenue_status='billed') | (Q(revenue_status='') & Q(revenue_date__isnull=False)))
    return queryset


def tracker_page(queryset, params, user, today):
    status_keys = {'processing': 'submitted', 'for_revision': 'for_revision', 'approved': 'reviewed', 'on_hold': 'on_hold'}
    proposal_keys = {'submitted_pending': 'submitted_pending', 'negotiation': 'negotiation_review', 'closed_won': 'closed_won', 'closed_lost': 'closed_lost', 'closed_canceled': 'closed_canceled'}
    counts = queryset.aggregate(total=Count('id'), mine=Count('id', filter=Q(pm_esg_reviewer=user)),
                               **{key: Count('id', filter=Q(status=value)) for key, value in status_keys.items()},
                               **{key: Count('id', filter=Q(proposal_status=value)) for key, value in proposal_keys.items()})
    page, pagination = paginate(filter_tracker(annotate_tracker(queryset, today), params, user).order_by('-created_at', '-pk'), params)
    return {'proposal_submissions': list(page.object_list), 'proposal_counts': {k: v for k, v in counts.items() if k != 'mine'},
            'my_assigned_sqr_count': counts['mine'], 'default_my_assigned_filter': user.role == User.Roles.PM_ESG,
            'sqr_my_assigned_active': params.get('assigned', 'mine' if user.role == User.Roles.PM_ESG else 'all') == 'mine',
            'sqr_reports_summary': None, **pagination}


def prototype_page(queryset, params, user, today):
    base = filter_tracker(annotate_tracker(queryset, today), params, user, prototype=True)
    conditions = {'all': Q(), 'mine': Q(pm_esg_reviewer=user), 'attention': Q(_attention=True) | (Q(status='submitted') if user.role in (User.Roles.ADMIN, User.Roles.PM_ESG) else Q(pk__in=[])),
                  'on_hold': Q(status='on_hold'), **{stage: Q(_pipeline=stage) for stage in ('opportunity', 'delivery', 'billing', 'closed')}}
    aggregates = base.aggregate(**{key + '_count': Count('id', filter=q) for key, q in conditions.items()},
                                **{key + '_total': Sum('_price', filter=q, default=Decimal('0')) for key, q in conditions.items()})
    pipeline = {key: {'count': aggregates[key + '_count'], 'total': str(aggregates[key + '_total'])} for key in conditions}
    stage = params.get('stage', 'all')
    if stage not in conditions or (stage == 'mine' and user.role not in (User.Roles.ADMIN, User.Roles.PM_ESG)):
        stage = 'all'
    page, _ = paginate(base.filter(conditions[stage]).order_by('-_score', '-created_at', '-pk'), params)
    return page, pipeline

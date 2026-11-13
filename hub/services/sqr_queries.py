from collections.abc import Collection
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.http import QueryDict
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


FILTER_ERROR = 'Invalid SQR filters. Review the selected options and try again.'
PERFORMANCE_CHOICES = {
    'schedule_performance': SqrSubmission.SchedulePerformance,
    'budget_effort_performance': SqrSubmission.BudgetEffortPerformance,
    'quality_performance': SqrSubmission.QualityPerformance,
}


def multi_values(params: QueryDict, name: str, *, allowed: Collection[str] | None = None) -> tuple[str, ...]:
    raw = params.getlist(name) if hasattr(params, 'getlist') else params.get(name, [])
    if not isinstance(raw, (list, tuple)):
        raw = [raw]
    values = tuple(dict.fromkeys(str(value).strip() for value in raw if value is not None and str(value).strip()))
    if allowed is not None and any(value not in allowed for value in values):
        raise ValidationError(FILTER_ERROR)
    return values


def positive_id(value):
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if 0 < parsed <= 2**63 - 1 else None


def _stage_conditions(user):
    return {'all': Q(), 'mine': Q(pm_esg_reviewer=user),
            'attention': Q(_attention=True) | (Q(status='submitted') if user.role in (User.Roles.ADMIN, User.Roles.PM_ESG) else Q(pk__in=[])),
            'on_hold': Q(status='on_hold'), **{key: Q(_pipeline=key) for key in ('opportunity', 'delivery', 'billing', 'closed')}}


def filter_options(authorized_queryset):
    enums = {'status': SqrSubmission.Status, 'proposal_status': SqrSubmission.ProposalStatus,
             'overall_status': SqrSubmission.OverallStatus, **PERFORMANCE_CHOICES}
    options = {key: [{'value': value, 'label': label} for value, label in choices.choices] for key, choices in enums.items()}
    for key in PERFORMANCE_CHOICES:
        options[key].append({'value': 'not_recorded', 'label': 'N/R — Not Recorded'})
    options['stage'] = [{'value': value, 'label': label} for value, label in (
        ('opportunity', 'Opportunity'), ('delivery', 'Delivery'), ('billing', 'Billing'), ('closed', 'Closed'),
        ('mine', 'My Assigned SQR (reviewer shortcut)'), ('attention', 'Needs action (workflow shortcut)'), ('on_hold', 'SQR On Hold (workflow shortcut)'))]
    options['portfolio'] = [{'value': value, 'label': label} for value, label in (
        ('ongoing', 'Ongoing'), ('pipeline', 'Pipeline'), ('closed', 'Closed'), ('removed', 'Removed'))]
    options['rating'] = [{'value': value, 'label': label} for value, label in (
        ('good', 'Green'), ('warning', 'Amber'), ('danger', 'Red'), ('unknown', 'N/R — Not Rated'))]
    options['scope'] = [{'value': 'active', 'label': 'Active delivery (PPT only)'}]
    options['attention'] = [{'value': value, 'label': label} for value, label in (
        ('performance_risk', 'Recorded performance risk'), ('missing_data', 'Missing delivery data'),
        ('inconsistency', 'Inconsistent delivery data'), ('on_hold', 'Delivery On Hold'),
        ('revision', 'Revision requested'), ('awaiting', 'Awaiting SQR approval'))]
    options['revenue_source'] = [{'value': value, 'label': label} for value, label in SqrSubmission._meta.get_field('revenue_source').choices]
    fields = [prefix + suffix for prefix in ('assigned_pm', 'pm_esg_reviewer', 'assigned_sse')
              for suffix in ('_id', '__first_name', '__last_name', '__username')] + ['customer_company']
    people = {'pm': {}, 'approver': {}, 'assigned_sse': {}}
    groups = set()
    for row in authorized_queryset.order_by().values(*fields).distinct():
        if row['customer_company']:
            groups.add(row['customer_company'])
        for key, prefix in (('pm', 'assigned_pm' if row['assigned_pm_id'] is not None else 'pm_esg_reviewer'),
                            ('approver', 'pm_esg_reviewer'), ('assigned_sse', 'assigned_sse')):
            pk = row[prefix + '_id']
            if pk is not None:
                name = ' '.join(filter(None, (row[prefix + '__first_name'], row[prefix + '__last_name']))).strip()
                people[key][str(pk)] = name or row[prefix + '__username'] or f'User {pk}'
    for key, names in people.items():
        options[key] = [{'value': value, 'label': label} for value, label in sorted(names.items(), key=lambda pair: (pair[1].lower(), pair[0]))]
    options['pm'].insert(0, {'value': 'unassigned', 'label': 'No Assigned PM (reviewer fallback)'})
    options['group'] = [{'value': value, 'label': value} for value in sorted(groups, key=lambda value: (value.lower(), value))]
    return options


def filter_ppt_projects(projects, params):
    portfolios = multi_values(params, 'portfolio', allowed=('ongoing', 'pipeline', 'closed', 'removed'))
    ratings = multi_values(params, 'rating', allowed=('good', 'warning', 'danger', 'unknown'))
    scopes = multi_values(params, 'scope', allowed=('all', 'active'))
    attention = multi_values(params, 'attention', allowed=('attention', 'performance_risk', 'missing_assessment', 'missing_data', 'inconsistency', 'on_hold', 'revision', 'awaiting'))
    def matches_attention(project, value):
        if value == 'attention':
            return project['needs_attention']
        if value == 'missing_assessment':
            return project['missing_assessment']
        if value == 'performance_risk':
            return any(indicator['state'] in ('warning', 'danger') for indicator in project['indicators'].values())
        return any(reason['key'] == ('inconsistent_data' if value == 'inconsistency' else value) for reason in project['reasons'])
    for key in ('needs_attention', 'missing_assessment'):
        if params.get(key, '') not in ('', '0', '1'):
            raise ValidationError(FILTER_ERROR)
    if params.get('focus'):
        focus = positive_id(params['focus'])
        return [project for project in projects if project['pk'] == focus] if focus is not None else []
    return [project for project in projects
            if (not portfolios or project['portfolio_status'] in portfolios)
            and (not ratings or project['overall_rating'] in ratings)
            and (not scopes or 'all' in scopes or project['active_delivery'])
            and (not attention or any(matches_attention(project, value) for value in attention))
            and (params.get('needs_attention') != '1' or project['needs_attention'])
            and (params.get('missing_assessment') != '1' or project['missing_assessment'])]


def annotate_tracker(queryset, today):
    closed = Q(proposal_status__in=['closed_lost', 'closed_canceled'])
    won = Q(proposal_status='closed_won')
    billed = Q(revenue_status='billed') | (Q(revenue_status='') & Q(revenue_date__isnull=False))
    finished = Q(overall_status='completed') | Q(delivery_health='completed') | Q(delivery_actual_finish_date__isnull=False) | Q(delivery_completion_signed_date__isnull=False)
    cancelled = Q(overall_status='cancelled') | Q(delivery_health='cancelled')
    queryset = queryset.annotate(
        _pipeline=Case(When(closed | cancelled | (finished & billed), then=Value('closed')),
                       When(finished, then=Value('billing')), When(~won, then=Value('opportunity')),
                       When(billed, then=Value('closed')), default=Value('delivery')),
        _gross=Coalesce('sse_amount', Decimal('0')) + Coalesce('pm_amount', Decimal('0')) + Coalesce('managed_support_amount', Decimal('0')),
    ).annotate(_price=price_expression())
    red = Q(schedule_performance='delayed') | Q(budget_effort_performance='over_budget') | Q(quality_performance='unmet')
    amber = Q(schedule_performance='at_risk') | Q(budget_effort_performance='at_risk') | Q(quality_performance='at_risk')
    delivery = Q(_pipeline='delivery')
    dangerous = Q(status='for_revision') | Q(_pipeline='opportunity', status='reviewed', validity_due_date__lte=today + timedelta(days=7)) | (delivery & (red | amber | Q(overall_status='on_hold')))
    score = Case(When(status='for_revision', then=3), When(status='submitted', then=2), default=0)
    score += Case(When(_pipeline='opportunity', status='reviewed', validity_due_date__lt=today, then=3),
                  When(_pipeline='opportunity', status='reviewed', validity_due_date__lte=today + timedelta(days=7), then=2), default=0)
    score += Case(When(delivery & red, then=3), When(delivery & (amber | Q(overall_status='on_hold')), then=2), default=0)
    return queryset.annotate(_attention=Case(When(status='on_hold', then=False), When(dangerous, then=True), default=False),
                             _score=Case(When(status='on_hold', then=0), default=score))


def filter_tracker(queryset, params, user, *, prototype=False):
    authorized = queryset
    selected = {}
    choices = {'status': SqrSubmission.Status.values, 'proposal_status': SqrSubmission.ProposalStatus.values,
               'overall_status': SqrSubmission.OverallStatus.values,
               'revenue_source': [value for value, _ in SqrSubmission._meta.get_field('revenue_source').choices]}
    for name, allowed in choices.items():
        selected[name] = multi_values(params, name, allowed=allowed)
    for name, enum in PERFORMANCE_CHOICES.items():
        selected[name] = multi_values(params, name, allowed=(*enum.values, 'not_recorded'))
    legacy = multi_values(params, 'delivery_health', allowed=(*SqrSubmission.DeliveryHealth.values, 'not_set'))
    stages = tuple(value for value in multi_values(params, 'stage', allowed=tuple(_stage_conditions(user))) if value != 'all')
    if 'mine' in stages and user.role not in (User.Roles.ADMIN, User.Roles.PM_ESG):
        raise ValidationError(FILTER_ERROR)
    assigned = params.get('assigned', 'mine' if user.role == User.Roles.PM_ESG else 'all')
    if assigned not in ('all', 'mine'):
        raise ValidationError(FILTER_ERROR)
    if params.get('attention') in ('overdue', 'due_soon') or params.get('overdue') == '1' or params.get('due_soon') == '1':
        raise ValidationError(FILTER_ERROR)

    people = {}
    for name, field in (('pm', 'assigned_pm'), ('approver', 'pm_esg_reviewer'), ('assigned_sse', 'assigned_sse')):
        values = multi_values(params, name)
        ids = []
        for value in values:
            if name == 'pm' and value == 'unassigned':
                continue
            parsed = positive_id(value)
            if parsed is None:
                raise ValidationError(FILTER_ERROR)
            ids.append(parsed)
        if ids:
            scoped = authorized.annotate(_effective_filter_pm=Coalesce('assigned_pm_id', 'pm_esg_reviewer_id')) if name == 'pm' else authorized
            lookup = '_effective_filter_pm' if name == 'pm' else field + '_id'
            available = set(scoped.order_by().values_list(lookup, flat=True).distinct())
            if not set(ids).issubset(available):
                raise ValidationError(FILTER_ERROR)
        people[name] = (tuple(dict.fromkeys(ids)), name == 'pm' and 'unassigned' in values)

    fields = ['reference_code', 'customer_name', 'project_title', 'customer_company', 'customer_contact', 'remarks', 'hold_reason', 'linked_request__reference_code']
    parts = [Coalesce(F(field), Value(''), output_field=TextField()) for field in fields]
    for relation in ('engineer', 'pm_esg_reviewer', 'assigned_pm', 'assigned_sse'):
        parts.append(person_name(relation))
    for field in SqrSubmission._meta.fields:
        if field.name not in fields and not field.is_relation:
            parts.append(Coalesce(Cast(F(field.name), TextField()), Value(''), output_field=TextField()))
            if field.choices:
                parts.append(Case(*[When(**{field.name: value}, then=Value(text)) for value, text in field.choices], default=Value('')))
            if isinstance(field, (DateField, DateTimeField)):
                value = TruncDate(field.name, tzinfo=MANILA_TZ) if isinstance(field, DateTimeField) else F(field.name)
                parts.append(Coalesce(DisplayText(value, Value('Mon DD, YYYY')), Value(''), output_field=TextField()))
            if isinstance(field, DecimalField):
                parts.append(Coalesce(DisplayText(F(field.name), Value('FM999,999,999,999,999,990.00')), Value(''), output_field=TextField()))
    parts.append(Coalesce(DisplayText(F('_price'), Value('FM999,999,999,999,999,990.00')), Value(''), output_field=TextField()))
    query = (params.get('q') or '').strip()[:200]
    if query:
        queryset = queryset.annotate(_search=Func(Value(' '), *parts, function='CONCAT_WS', output_field=TextField()))
        for term in query.lower().split():
            queryset = queryset.filter(_search__icontains=term)
    if assigned == 'mine':
        queryset = queryset.filter(pm_esg_reviewer=user)
    for name, values in selected.items():
        if values:
            queryset = queryset.filter(**{name + '__in': tuple('' if value == 'not_recorded' else value for value in values)})
    if legacy and 'schedule_performance' not in params:
        aliases = {'on_track': 'on_track', 'at_risk': 'at_risk', 'off_track': 'delayed', 'not_set': ''}
        condition = Q(pk__in=[])
        for value in legacy:
            condition |= Q(delivery_health=value) if value in ('completed', 'cancelled') else Q(schedule_performance=aliases[value])
        queryset = queryset.filter(condition)
    for name, field in (('pm', 'assigned_pm'), ('approver', 'pm_esg_reviewer'), ('assigned_sse', 'assigned_sse')):
        ids, unassigned = people[name]
        if ids or unassigned:
            condition = Q(**{field + '__in': ids}) if ids else Q(pk__in=[])
            if name == 'pm':
                if ids:
                    condition |= Q(assigned_pm__isnull=True, pm_esg_reviewer__in=ids)
                if unassigned:
                    condition |= Q(assigned_pm__isnull=True)
            queryset = queryset.filter(condition)
    groups = multi_values(params, 'group')
    if groups:
        condition = Q(pk__in=[])
        for group in groups:
            condition |= Q(customer_company__icontains=group)
        queryset = queryset.filter(condition)
    if stages:
        condition = Q(pk__in=[])
        for stage in stages:
            condition |= _stage_conditions(user)[stage]
        queryset = queryset.filter(condition)
    for parameter, lookup in (('date_from', 'created_at__date__gte'), ('date_to', 'created_at__date__lte')):
        raw = params.get(parameter, '')
        if not raw:
            continue
        try:
            value = date.fromisoformat(raw)
        except (ValueError, TypeError):
            raise ValidationError(FILTER_ERROR)
        queryset = queryset.filter(**{lookup: value})
    for parameter, lookup in (('amount_min', '_price__gte'), ('amount_max', '_price__lte')):
        raw = params.get(parameter, '')
        if not raw:
            continue
        try:
            value = Decimal(raw.replace(',', ''))
            if not value.is_finite():
                raise InvalidOperation
        except (InvalidOperation, ValueError, TypeError):
            raise ValidationError(FILTER_ERROR)
        limit = Decimal('1e18')
        if value > limit:
            if parameter == 'amount_min':
                queryset = queryset.none()
            continue
        if value < -limit:
            if parameter == 'amount_max':
                queryset = queryset.none()
            continue
        queryset = queryset.filter(**{lookup: value})
    for flag in ('linked', 'sse', 'billed'):
        if params.get(flag, '') not in ('', '0', '1'):
            raise ValidationError(FILTER_ERROR)
    if params.get('linked') == '1':
        queryset = queryset.filter(linked_request__isnull=False)
    if params.get('sse') == '1':
        queryset = queryset.filter(assigned_sse__isnull=False)
    if params.get('billed') == '1':
        queryset = queryset.filter(Q(revenue_status='billed') | (Q(revenue_status='') & Q(revenue_date__isnull=False)))
    if params.get('focus'):
        focus = positive_id(params['focus'])
        return authorized.filter(pk=focus) if focus is not None else authorized.none()
    return queryset


def tracker_page(queryset, params, user, today):
    status_keys = {'processing': 'submitted', 'for_revision': 'for_revision', 'approved': 'reviewed', 'on_hold': 'on_hold'}
    proposal_keys = {'submitted_pending': 'submitted_pending', 'negotiation': 'negotiation_review', 'closed_won': 'closed_won', 'closed_lost': 'closed_lost', 'closed_canceled': 'closed_canceled'}
    error = ''
    try:
        filtered = filter_tracker(annotate_tracker(queryset, today), params, user)
    except ValidationError:
        filtered = queryset.none()
        error = FILTER_ERROR
    counts = filtered.aggregate(total=Count('id'), mine=Count('id', filter=Q(pm_esg_reviewer=user)),
                               **{key: Count('id', filter=Q(status=value)) for key, value in status_keys.items()},
                               **{key: Count('id', filter=Q(proposal_status=value)) for key, value in proposal_keys.items()})
    page, pagination = paginate(filtered.order_by('-created_at', '-pk'), params)
    return {'proposal_submissions': list(page.object_list), 'proposal_counts': {k: v for k, v in counts.items() if k != 'mine'},
            'sqr_filter_error': error,
            'my_assigned_sqr_count': counts['mine'], 'default_my_assigned_filter': user.role == User.Roles.PM_ESG,
            'sqr_my_assigned_active': params.get('assigned', 'mine' if user.role == User.Roles.PM_ESG else 'all') == 'mine',
            'sqr_reports_summary': None, **pagination}


def prototype_page(queryset, params, user, today):
    base = filter_tracker(annotate_tracker(queryset, today), params, user, prototype=True)
    conditions = _stage_conditions(user)
    aggregates = base.aggregate(**{key + '_count': Count('id', filter=q) for key, q in conditions.items()},
                                **{key + '_total': Sum('_price', filter=q, default=Decimal('0')) for key, q in conditions.items()})
    pipeline = {key: {'count': aggregates[key + '_count'], 'total': str(aggregates[key + '_total'])} for key in conditions}
    page, _ = paginate(base.order_by('-_score', '-created_at', '-pk'), params)
    return page, pipeline

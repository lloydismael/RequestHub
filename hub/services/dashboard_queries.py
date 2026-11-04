from datetime import timedelta

from django.db.models import Case, Count, DateField, F, Func, IntegerField, Q, TextField, Value, When
from django.db.models.functions import Coalesce, Concat, Lower, NullIf, Trim, TruncDate

from hub.models import MANILA_TZ, Request
from hub.services.pagination import paginate


class WorkingDays(Func):
    output_field = IntegerField()
    arity = 2

    def as_postgresql(self, compiler, connection, **extra_context):
        start, start_params = compiler.compile(self.source_expressions[0])
        end, end_params = compiler.compile(self.source_expressions[1])
        # Match Request.days_since_creation: exclude the start date, include weekdays through the end.
        sql = (
            '(SELECT (days / 7) * 5 + '
            '(SELECT COUNT(*) FROM generate_series(1, days %% 7) AS offsets(n) '
            'WHERE EXTRACT(ISODOW FROM (start_day + n)) <= 5) '
            f'FROM (SELECT GREATEST(({end}) - ({start}), 0) AS days, ({start}) AS start_day) AS span)'
        )
        return sql, [*end_params, *start_params, *start_params]


def person_name(relation):
    full_name = Trim(Concat(F(relation + '__first_name'), Value(' '), F(relation + '__last_name')))
    return Coalesce(NullIf(full_name, Value('')), F(relation + '__username'), Value(''))


def choice_label(field):
    return Case(*[When(**{field: value}, then=Value(label)) for value, label in Request._meta.get_field(field).choices], default=F(field))


def dashboard_data(queryset, filter_form, params, metric_filter, today):
    queryset = filter_form.filter_queryset(queryset).annotate(
        _working_days=WorkingDays(
            Coalesce('start_date', TruncDate('created_at', tzinfo=MANILA_TZ)),
            Coalesce('end_date', Value(today, output_field=DateField())),
        )
    )
    if filter_form.is_valid():
        for name, lookup in (('days_min', '_working_days__gte'), ('days_max', '_working_days__lte')):
            if filter_form.cleaned_data.get(name) is not None:
                queryset = queryset.filter(**{lookup: filter_form.cleaned_data[name]})

    ongoing = Q(status=Request.Status.ONGOING)
    overdue = ongoing & ((Q(priority=Request.Priority.HIGH) & Q(_working_days__gt=3)) |
                         (~Q(priority=Request.Priority.HIGH) & Q(_working_days__gt=5)))
    metrics = {
        'open': ongoing,
        'overdue': overdue,
        'due_soon': ongoing & ~Q(engagement_type__in=[Request.Engagement.DEPLOYMENT, Request.Engagement.CERTIFICATION]) &
                    Q(due_date__gte=today, due_date__lte=today + timedelta(days=3)),
        'completed': Q(status=Request.Status.COMPLETED),
        # The old predicate uses the UTC created_at date, including future-dated rows.
        'new_this_week': Q(created_at__gte=Value(today - timedelta(days=7), output_field=DateField())),
    }
    totals = queryset.aggregate(total=Count('id'), **{key: Count('id', filter=condition) for key, condition in metrics.items()})
    status_counts = {'all': totals.pop('total'), 'ongoing': totals['open'], 'completed': totals['completed']}
    query = (params.get('q') or '').strip()[:200]
    if query:
        expressions = [F('reference_code'), F('account__name'), F('account_manager'), F('description'), F('teams_chat_topic')]
        expressions.extend(choice_label(field) for field in ('engagement_type', 'product_category', 'priority', 'status'))
        for relation in ('requestor', 'engineer', 'backup_engineer'):
            expressions.extend([person_name(relation), F(relation + '__username'), F(relation + '__email')])
        parts = []
        for expression in expressions:
            parts.extend([Coalesce(expression, Value(''), output_field=TextField()), Value(' ')])
        queryset = queryset.annotate(_search=Concat(*parts, output_field=TextField()))
        terms = query.casefold().split()
        ascii_match = Q(_search__regex=r'^[\x01-\x7f]*$')
        for term in terms:
            ascii_match &= Q(_search__icontains=term)
        # PostgreSQL 16 ILIKE cannot reproduce Python casefold expansions such as ß/ss.
        # Project only search text and IDs for Unicode rows, never full related models.
        unicode_ids = [pk for pk, text in queryset.filter(_search__regex=r'[^\x01-\x7f]')
                       .values_list('pk', '_search').iterator(chunk_size=1000)
                       if all(term in text.casefold() for term in terms)]
        queryset = queryset.filter(ascii_match | Q(pk__in=unicode_ids))
    else:
        queryset = queryset.filter(metrics[metric_filter])

    sort_map = {
        'reference_code': Lower('reference_code'), 'account': Lower('account__name'),
        'account_manager': Lower(Case(When(requestor__isnull=False, then=person_name('requestor')), default=Trim('account_manager'))),
        'engineer': Lower(person_name('engineer')), 'backup_engineer': Lower(person_name('backup_engineer')),
        'engagement': F('engagement_type'), 'product_category': F('product_category'), 'status': F('status'),
        'created': F('created_at'), 'end_date': F('end_date'), 'days': F('_working_days'), 'due': F('due_date'),
    }
    sort = params.get('sort', 'created')
    if sort not in sort_map:
        sort = 'created'
    direction = params.get('direction', 'desc')
    if direction not in ('asc', 'desc'):
        direction = 'desc'
    expression = sort_map[sort]
    order = expression.asc(nulls_last=True) if direction == 'asc' else expression.desc(nulls_last=True)
    prefix = []
    if sort in ('engineer', 'backup_engineer', 'account_manager'):
        missing = Q(**{sort + '__isnull': True}) if sort != 'account_manager' else Q(_sort_name='')
        queryset = queryset.annotate(_sort_name=expression, _missing=Case(When(missing, then=Value(1)), default=Value(0)))
        prefix = [F('_missing').asc() if direction == 'asc' else F('_missing').desc()]
    page, pagination = paginate(queryset.order_by(*prefix, order, '-pk'), params)
    rows = list(page.object_list)
    return {
        'requests': rows, 'metrics': totals, 'status_counts': status_counts, 'overdue_count': totals['overdue'],
        'current_sort': sort, 'current_direction': direction, 'dashboard_search_query': query,
        'requests_total_count': page.paginator.count, 'requests_displayed_count': len(rows),
        'requests_list_limited': page.paginator.num_pages > 1, 'requests_list_limit': page.paginator.per_page,
        **pagination,
    }

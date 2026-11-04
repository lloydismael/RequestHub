from django.core.paginator import Paginator


def paginate(queryset, params):
    try:
        page_size = int(params.get('page_size', 50))
    except (TypeError, ValueError):
        page_size = 50
    if page_size not in (25, 50, 100):
        page_size = 50
    page = Paginator(queryset, page_size).get_page(params.get('page', 1))

    def link(number):
        query = params.copy()
        query.pop('version', None)
        query['page'] = str(number)
        query['page_size'] = str(page_size)
        return '?' + query.urlencode()

    return page, {
        'page_obj': page,
        'page_size': page_size,
        'pagination_previous': link(page.previous_page_number()) if page.has_previous() else '',
        'pagination_next': link(page.next_page_number()) if page.has_next() else '',
    }

"""Map business language to tables and preserve join paths within a budget."""

from collections import deque
import re

# Exact schema names only; a hint cannot introduce a table absent from the project.
TABLE_TERMS = {
    "transactions": ("거래", "완료 거래", "완료된 거래", "성사된 거래", "거래금액", "실거래금액"),
    "listings": ("판매글", "판매 글", "등록글", "상품 게시글", "등록 동네", "등록 지역"),
    "neighborhoods": ("동네", "지역", "행정동"),
    "users": ("사용자", "회원", "구매자", "판매자"),
    "categories": ("카테고리", "상품 분류", "상품분류"),
    "offers": ("구매 제안", "구매제안", "제안"),
    "reviews": ("후기", "리뷰", "평점"),
}
_PARTICLES = r"(?:별로|별|으로|에서|에게|까지|부터|의|은|는|이|가|을|를|도|만|에|로|과|와)?"


def matching_terms(question, tables):
    matches = {}
    for table in tables:
        terms = [
            term for term in TABLE_TERMS.get(table, ())
            if re.search(
                rf"(?<![\w]){re.escape(term)}{_PARTICLES}(?![\w])", question
            )
        ]
        if terms:
            matches[table] = terms
    return matches


def expand_tables(ranked, fks, available, limit, *, anchors=()):
    """Keep direct matches, then add complete join paths and outbound references."""
    available = set(available)
    ranked = list(dict.fromkeys(t for t in ranked if t in available))
    primary = list(dict.fromkeys(t for t in anchors if t in available))[:limit]
    # Without explicit term matches, reserve room for join paths between top hits.
    if not primary:
        primary = ranked[:min(3, max(0, (limit + 1) // 2))]
    outgoing = {t: set() for t in available}
    adjacent = {t: set() for t in available}
    for src, links in fks.items():
        if src not in available:
            continue
        for link in links:
            dest = link.get("ref_table")
            if dest in available and dest != src:
                outgoing[src].add(dest)
                adjacent[src].add(dest)
                adjacent[dest].add(src)

    selected = dict.fromkeys(primary)

    def shortest_path(start, end, graph):
        queue = deque([(start,)])
        seen = {start}
        while queue:
            path = queue.popleft()
            if path[-1] == end:
                return path
            for neighbor in sorted(graph[path[-1]], key=lambda t: (t not in selected, t)):
                if neighbor not in seen:
                    seen.add(neighbor)
                    queue.append((*path, neighbor))
        return ()

    for i, start in enumerate(primary):
        for end in primary[i + 1:]:
            # A path through already selected tables needs no extra context.
            present = {t: adjacent[t] & selected.keys() for t in selected}
            if shortest_path(start, end, present):
                continue
            path = shortest_path(start, end, outgoing) or shortest_path(end, start, outgoing)
            path = path or shortest_path(start, end, adjacent)
            missing = [t for t in path if t not in selected]
            if len(selected) + len(missing) <= limit:
                selected.update(dict.fromkeys(missing))

    # Only one outbound hop: reverse edges would pull in unrelated fact tables.
    for table in primary:
        for neighbor in sorted(outgoing[table], key=lambda t: (t not in ranked, t)):
            if len(selected) < limit:
                selected.setdefault(neighbor, None)
    for table in ranked:
        if len(selected) < limit:
            selected.setdefault(table, None)
    return list(selected)

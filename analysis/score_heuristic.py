#!/usr/bin/env python3
"""Create metadata-based judgments from retrieval metadata.

This is deliberately not presented as human relevance judging. It uses the
query, target hints, result URL, title, snippets, and URL-resolution status.
Provider and rank are not used when assigning a judgment.
"""

from __future__ import annotations

import argparse
import csv
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlparse


STOPWORDS = {
    "a", "an", "and", "as", "at", "be", "by", "can", "do", "does", "for", "from",
    "full", "get", "how", "in", "into", "is", "it", "latest", "make", "of", "official",
    "on", "or", "published", "the", "to", "with", "written", "find", "ways", "what",
    "which", "that", "this", "current", "explain", "documentation", "document", "page",
    "release", "notes", "source", "search", "using", "use", "about", "after", "before",
}
LANGUAGE_HINTS = {
    "spanish": {"c\u00f3mo", "solicitar", "certificado", "persona", "f\u00edsica", "alta", "m\u00f3vil", "vacaciones"},
    "french": {"comment", "cong\u00e9", "annuel", "travail", "fran\u00e7ais", "demande"},
    "german": {"deutschen", "urlaub", "mindest", "vollzeit", "gesetz", "urlaubstage"},
    "portuguese": {"como", "obter", "certificado", "f\u00e9rias", "trabalhador", "finan\u00e7as"},
    "japanese": {"\u7533\u8acb", "\u8a3c\u660e\u66f8", "\u756a\u53f7", "\u30c7\u30b8\u30bf\u30eb"},
}
OFFICIAL_HOST_SUFFIXES = (".gov", ".gov.uk", ".gouv.fr", ".gob.es", ".go.jp", ".europa.eu")
SOCIAL_HOSTS = {"reddit.com", "www.reddit.com", "x.com", "twitter.com", "facebook.com"}


def tokens(value: str) -> set[str]:
    normalized = unicodedata.normalize("NFKC", value or "").lower()
    parts = re.findall(r"[^\W_]+", normalized, flags=re.UNICODE)
    return {part for part in parts if len(part) >= 2 and part not in STOPWORDS}


def normalized_url(value: str) -> str:
    return unquote((value or "").strip()).lower().replace("www.", "")


def host(value: str) -> str:
    try:
        return (urlparse(value).hostname or "").lower().removeprefix("www.")
    except ValueError:
        return ""


def hint_parts(value: str) -> list[str]:
    return [normalized_url(part.strip().rstrip("/")) for part in (value or "").split(";") if part.strip()]


def hint_match(url: str, hint: str) -> tuple[bool, bool]:
    current = normalized_url(url)
    current_host = host(url)
    path_match = False
    domain_match = False
    for part in hint_parts(hint):
        target = part.removeprefix("https://").removeprefix("http://")
        target_host = target.split("/", 1)[0].removeprefix("www.")
        if target_host and target_host in current_host:
            domain_match = True
        if target and target in current:
            path_match = True
    return path_match, domain_match


def query_language(query: str) -> str:
    lowered = (query or "").lower()
    if any("\u3040" <= char <= "\u30ff" or "\u4e00" <= char <= "\u9fff" for char in lowered):
        return "japanese"
    scores = {name: sum(word in lowered for word in words) for name, words in LANGUAGE_HINTS.items()}
    language, score = max(scores.items(), key=lambda item: item[1])
    return language if score >= 1 else "english"


def language_match(query: str, text: str, result_url: str) -> bool:
    language = query_language(query)
    if language == "english":
        return True
    lowered = (text or "").lower()
    hints = LANGUAGE_HINTS[language]
    if sum(word in lowered for word in hints) >= 1:
        return True
    result_host = host(result_url)
    return any(result_host.endswith(suffix) for suffix in (".es", ".fr", ".de", ".pt", ".cat", ".jp"))


def parse_date(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def official_result(result_url: str, target_hint: str, category: str) -> bool:
    _, domain_match = hint_match(result_url, target_hint)
    result_host = host(result_url)
    if domain_match:
        return True
    if category in {"primary_source", "freshness_rolling", "multilingual_regional"}:
        return result_host.endswith(OFFICIAL_HOST_SUFFIXES)
    return result_host.endswith(OFFICIAL_HOST_SUFFIXES)


def spam_result(result_url: str, title: str) -> bool:
    value = f"{result_url} {title}".lower()
    return any(marker in value for marker in ("casino", "betting", "porn", "viagra", "coupon"))


def constraint_matches(subtopics: str, text: str) -> tuple[int, int, str]:
    labels = [part.strip() for part in (subtopics or "").split("|") if part.strip()]
    lowered = (text or "").lower()
    matched = [label for label in labels if all(token in lowered for token in tokens(label))]
    return len(matched), len(labels), "|".join(matched)


def relevance_grade(
    *,
    category: str,
    query: str,
    intent: str,
    target_hint: str,
    subtopics: str,
    result_url: str,
    text: str,
    final_url: str,
    broken: bool,
) -> tuple[int, float, int, int, str]:
    if broken:
        return 0, 0.0, 0, len([x for x in (subtopics or "").split("|") if x]), "broken_or_unreachable"

    query_terms = tokens(f"{query} {intent}")
    text_terms = tokens(text)
    title_terms = tokens(text.split(" || ", 1)[0])
    overlap = len(query_terms & text_terms) / max(1, len(query_terms))
    title_overlap = len(query_terms & title_terms) / max(1, len(query_terms))
    path_match, domain_match = hint_match(final_url or result_url, target_hint)
    official = official_result(final_url or result_url, target_hint, category)
    matched_constraints, total_constraints, matched_labels = constraint_matches(subtopics, text)

    if category == "known_item":
        if path_match:
            grade = 3
        elif domain_match and overlap >= 0.18:
            grade = 2
        elif overlap >= 0.48 or title_overlap >= 0.35:
            grade = 2
        elif overlap >= 0.18:
            grade = 1
        else:
            grade = 0
    elif category == "primary_source":
        if official and overlap >= 0.18:
            grade = 3
        elif overlap >= 0.45:
            grade = 2
        elif overlap >= 0.18:
            grade = 1
        else:
            grade = 0
    elif category == "multi_constraint":
        if total_constraints and matched_constraints == total_constraints and (domain_match or overlap >= 0.3):
            grade = 3
        elif matched_constraints or overlap >= 0.35:
            grade = 2
        elif overlap >= 0.15:
            grade = 1
        else:
            grade = 0
    elif category == "multilingual_regional":
        if official and overlap >= 0.18:
            grade = 3
        elif overlap >= 0.4:
            grade = 2
        elif overlap >= 0.16:
            grade = 1
        else:
            grade = 0
    elif category == "freshness_rolling":
        if official and overlap >= 0.2:
            grade = 2
        elif overlap >= 0.45:
            grade = 2
        elif overlap >= 0.18:
            grade = 1
        else:
            grade = 0
    else:
        if overlap >= 0.55 or (title_overlap >= 0.45 and overlap >= 0.3):
            grade = 3
        elif overlap >= 0.3 or title_overlap >= 0.25:
            grade = 2
        elif overlap >= 0.12:
            grade = 1
        else:
            grade = 0

    evidence = f"overlap={overlap:.2f};title={title_overlap:.2f};hint_path={int(path_match)};hint_domain={int(domain_match)};official={int(official)}"
    if total_constraints:
        evidence += f";constraints={matched_constraints}/{total_constraints}"
    return grade, overlap, matched_constraints, total_constraints, f"auto_heuristic_v1;{evidence}"


def load_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_rows(path: Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queries", type=Path, required=True)
    parser.add_argument("--retrieved", type=Path, required=True)
    parser.add_argument("--pooled-primary", type=Path, required=True)
    parser.add_argument("--pooled-reference", type=Path, required=True)
    parser.add_argument("--resolution", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    queries = {row["query_id"]: row for row in load_rows(args.queries)}
    retrieved = load_rows(args.retrieved)
    pooled_primary_rows = load_rows(args.pooled_primary)
    pooled_ref_rows = load_rows(args.pooled_reference)

    primary_dict = {(r["query_id"], r["canonical_url"]): r for r in pooled_primary_rows}
    ref_dict = {(r["query_id"], r["canonical_url"]): r for r in pooled_ref_rows}

    for row in retrieved:
        variant = row["query_variant"]
        key = (row["query_id"], row["canonical_url"])
        if variant == "primary" and key not in primary_dict:
            primary_dict[key] = {"query_id": row["query_id"], "canonical_url": row["canonical_url"], "title": row.get("title", ""), "published_at": row.get("published_at", "")}
        elif variant == "reference" and key not in ref_dict:
            ref_dict[key] = {"query_id": row["query_id"], "canonical_url": row["canonical_url"], "title": row.get("title", ""), "published_at": row.get("published_at", "")}

    pooled_sets = {
        "primary": list(primary_dict.values()),
        "reference": list(ref_dict.values()),
    }
    resolution = {row["original_url"]: row for row in load_rows(args.resolution)}

    evidence: dict[tuple[str, str, str], dict[str, str]] = {}
    for row in retrieved:
        key = (row["query_id"], row["query_variant"], row["canonical_url"])
        current = evidence.setdefault(key, {"title": row.get("title", ""), "snippets": "", "url": row.get("url", "")})
        current["title"] = current["title"] or row.get("title", "")
        snippet = row.get("snippet", "").strip()
        if snippet and snippet not in current["snippets"]:
            current["snippets"] = " || ".join(filter(None, [current["snippets"], snippet]))

    judgment_fields = [
        "query_id", "canonical_url", "title", "assessor_id", "relevance_grade",
        "is_official_or_primary", "is_independent_source", "is_correct_language", "is_broken",
        "is_spam", "constraints_met", "constraints_total", "subtopics", "published_at",
        "is_latest_official", "notes",
    ]
    judgment_outputs: dict[str, list[dict[str, str]]] = {"primary": [], "reference": []}
    labels: dict[tuple[str, str, str], dict[str, str]] = {}

    for variant, pooled_rows in pooled_sets.items():
        for row in pooled_rows:
            q = queries[row["query_id"]]
            query_text = q["query"] if variant == "primary" else (q["reference_query"] or q["query"])
            key = (row["query_id"], variant, row["canonical_url"])
            source = evidence.get(key, {"title": row.get("title", ""), "snippets": "", "url": row["canonical_url"]})
            resolved = resolution.get(row["canonical_url"], {})
            final_url = resolved.get("final_url") or row["canonical_url"]
            page_title = resolved.get("page_title", "")
            text = " || ".join(filter(None, [row.get("title", ""), source.get("title", ""), source.get("snippets", ""), page_title, row["canonical_url"], final_url]))
            broken = resolved.get("broken") == "1" or not resolved.get("http_status")
            grade, _, matched, total, notes = relevance_grade(
                category=q["category"],
                query=query_text,
                intent=q["intent"],
                target_hint=q["target_hint"],
                subtopics=q["subtopics"],
                result_url=row["canonical_url"],
                text=text,
                final_url=final_url,
                broken=broken,
            )
            official = official_result(final_url, q["target_hint"], q["category"])
            independent = not official and host(final_url) not in SOCIAL_HOSTS
            correct_language = language_match(query_text, text, final_url)
            spam = spam_result(final_url, row.get("title", ""))
            if spam:
                grade = 0
            result = {
                "query_id": row["query_id"],
                "canonical_url": row["canonical_url"],
                "title": row.get("title", ""),
                "assessor_id": "auto_heuristic_v1",
                "relevance_grade": str(grade),
                "is_official_or_primary": "1" if official else "0",
                "is_independent_source": "1" if independent else "0",
                "is_correct_language": "1" if correct_language else "0",
                "is_broken": "1" if broken else "0",
                "is_spam": "1" if spam else "0",
                "constraints_met": str(matched),
                "constraints_total": str(total),
                "subtopics": "|".join(x for x in (q["subtopics"] or "").split("|") if x and x.lower() in notes.lower()),
                "published_at": row.get("published_at", ""),
                "is_latest_official": "",
                "notes": notes,
            }
            judgment_outputs[variant].append(result)
            labels[key] = result

    for variant, rows in judgment_outputs.items():
        path = args.out_dir / f"pooled_judgments_{variant}_auto.csv"
        write_rows(path, rows, judgment_fields)

    scored_fields = [
        "query_id", "category", "provider", "repetition", "rank", "url", "canonical_url", "title",
        "relevance_grade", "is_official_or_primary", "is_independent_source", "is_correct_language",
        "is_broken", "is_spam", "constraints_met", "constraints_total", "subtopics", "published_at", "is_latest_official",
    ]
    for variant in ("primary", "reference"):
        scored: list[dict[str, str]] = []
        for row in retrieved:
            if row["query_variant"] != variant:
                continue
            label = labels[(row["query_id"], variant, row["canonical_url"])]
            scored.append({
                "query_id": row["query_id"],
                "category": row["category"],
                "provider": row["provider"],
                "repetition": row["repetition"],
                "rank": row["rank"],
                "url": row["url"],
                "canonical_url": row["canonical_url"],
                "title": row["title"],
                **{field: label[field] for field in scored_fields[8:]},
            })
        write_rows(args.out_dir / f"scored_results_{variant}_auto.csv", scored, scored_fields)

    method = args.out_dir / "judgment-method.md"
    method.write_text(
        "# Automated judgment method\n\n"
        "These labels are `auto_heuristic_v1`, not human gold judgments. The scorer uses only the query text, intent, target hints, subtopics, result URL, title/snippets, limited URL-resolution metadata, and page reachability. It does not use provider or rank when assigning labels.\n\n"
        "Grade 3 means the heuristic found a strong direct match or all declared constraints. Grade 2 means a useful relevant match. Grade 1 means partial or weak topical evidence. Grade 0 means no match, spam, or an unreachable result. The resulting nDCG/MRR/Success charts are automated diagnostics and must not be described as human relevance scores.\n",
        encoding="utf-8",
    )
    print(f"wrote automated judgments and scored results to {args.out_dir}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Prepare blind result-card packets and merge agent relevance judgments.

The packet deliberately omits provider, original rank, and query category. The
merge step restores the original provider/rank only after grades are complete.
This keeps the judge input blind while preserving the benchmark's ranking data.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import defaultdict
from pathlib import Path


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def result_id(query_id: str, canonical_url: str) -> str:
    value = f"{query_id}\0{canonical_url}".encode("utf-8")
    return f"r-{hashlib.sha256(value).hexdigest()[:12]}"


def build_cards(run_dir: Path, queries_path: Path) -> tuple[dict[str, dict], list[dict]]:
    queries = {row["query_id"]: row for row in read_csv(queries_path)}
    pooled = read_csv(run_dir / "pooled_judgments_primary.csv")
    retrieved = read_csv(run_dir / "retrieved_results.csv")
    resolution = {row["original_url"]: row for row in read_csv(run_dir / "url_resolution.csv")}

    snippets: dict[tuple[str, str], list[str]] = defaultdict(list)
    for row in retrieved:
        if row.get("query_variant") != "primary":
            continue
        key = (row["query_id"], row["canonical_url"])
        value = row.get("snippet", "").strip()
        if value and value not in snippets[key]:
            snippets[key].append(value)

    cards: dict[str, dict] = {}
    for row in pooled:
        query_id = row["query_id"]
        canonical_url = row["canonical_url"]
        query = queries[query_id]["query"]
        resolved = resolution.get(canonical_url, {})
        cards[result_id(query_id, canonical_url)] = {
            "result_id": result_id(query_id, canonical_url),
            "query_id": query_id,
            "query": query,
            "title": row.get("title", ""),
            "snippets": snippets[(query_id, canonical_url)],
            "page_title": resolved.get("page_title", ""),
            "url": canonical_url,
        }
    return queries, list(cards.values())


def prepare(args: argparse.Namespace) -> None:
    queries, cards = build_cards(args.run_dir, args.queries)
    query_ids = sorted(queries)
    batch_size = args.queries_per_batch
    packets_dir = args.out_dir / "packets"
    packets_dir.mkdir(parents=True, exist_ok=True)

    cards_by_query: dict[str, list[dict]] = defaultdict(list)
    for card in cards:
        cards_by_query[card["query_id"]].append(card)

    manifest_batches = []
    rubric = (
        "Judge each result independently for the supplied query. Use only the query "
        "and result card. Do not search the web. Do not infer the provider or rank. "
        "Grade 3 = directly satisfies the query; 2 = relevant and useful; "
        "1 = partial or weak match; 0 = irrelevant, wrong intent, spam, misleading, "
        "or clearly broken. Return one JSON object per result with result_id, grade, "
        "and a short reason."
    )

    for offset in range(0, len(query_ids), batch_size):
        batch_number = offset // batch_size + 1
        batch_queries = query_ids[offset : offset + batch_size]
        batch_cards = [card for query_id in batch_queries for card in cards_by_query[query_id]]
        judge_cards = [
            {key: value for key, value in card.items() if key != "query_id"}
            for card in batch_cards
        ]
        packet = {
            "instructions": rubric,
            "batch_id": f"batch-{batch_number:02d}",
            "queries": judge_cards,
        }
        packet_path = packets_dir / f"batch-{batch_number:02d}.json"
        packet_path.write_text(json.dumps(packet, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        manifest_batches.append({
            "batch_id": packet["batch_id"],
            "query_ids": batch_queries,
            "result_count": len(batch_cards),
            "packet": str(packet_path),
        })

    manifest = {
        "method": "agent_blind_card_v1",
        "query_count": len(query_ids),
        "result_count": len(cards),
        "queries_per_batch": batch_size,
        "batches": manifest_batches,
    }
    (args.out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"queries": len(query_ids), "results": len(cards), "batches": len(manifest_batches)}))


def load_judgments(path: Path) -> list[dict]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(value, dict):
        value = value.get("judgments", value.get("results", []))
    if not isinstance(value, list):
        raise ValueError(f"{path}: expected a JSON list or an object containing judgments")
    return value


def merge(args: argparse.Namespace) -> None:
    queries, cards = build_cards(args.run_dir, args.queries)
    expected = {card["result_id"]: card for card in cards}
    grades: dict[str, dict] = {}
    for path in sorted(args.judgments_dir.glob("batch-*.json")):
        batch_id = path.stem
        for judgment in load_judgments(path):
            result = str(judgment.get("result_id", ""))
            if result not in expected:
                raise ValueError(f"{path}: unknown result_id {result!r}")
            if result in grades:
                raise ValueError(f"duplicate judgment for {result}")
            grade = judgment.get("grade")
            if isinstance(grade, bool) or not isinstance(grade, int) or grade not in range(4):
                raise ValueError(f"{path}: grade for {result} must be integer 0..3")
            grades[result] = {
                "grade": str(grade),
                "reason": str(judgment.get("reason", "")).strip(),
                "judge_batch": batch_id,
            }

    missing = sorted(set(expected) - set(grades))
    if missing:
        raise ValueError(f"missing {len(missing)} judgments, first: {missing[:5]}")

    run_rows = read_csv(args.run_dir / "runs.csv")
    retrieved = read_csv(args.run_dir / "retrieved_results.csv")
    resolution = {row["original_url"]: row for row in read_csv(args.run_dir / "url_resolution.csv")}

    pooled_fields = [
        "query_id", "canonical_url", "title", "assessor_id", "relevance_grade",
        "is_official_or_primary", "is_independent_source", "is_correct_language", "is_broken",
        "is_spam", "constraints_met", "constraints_total", "subtopics", "published_at",
        "is_latest_official", "notes",
    ]
    pooled_rows = []
    for result, card in sorted(expected.items()):
        resolved = resolution.get(card["url"], {})
        pooled_rows.append({
            "query_id": card["query_id"],
            "canonical_url": card["url"],
            "title": card["title"],
            "assessor_id": "agent_blind_card_v1",
            "relevance_grade": grades[result]["grade"],
            "is_official_or_primary": "",
            "is_independent_source": "",
            "is_correct_language": "",
            "is_broken": "1" if resolved.get("broken") == "1" else "0",
            "is_spam": "",
            "constraints_met": "",
            "constraints_total": "",
            "subtopics": "",
            "published_at": "",
            "is_latest_official": "",
            "notes": grades[result]["reason"],
        })

    score_fields = [
        "query_id", "category", "provider", "repetition", "rank", "canonical_url",
        "relevance_grade", "is_official_or_primary", "is_independent_source", "is_correct_language",
        "is_broken", "is_spam", "constraints_met", "constraints_total", "subtopics",
        "published_at", "is_latest_official",
    ]
    score_rows = []
    reasoning_rows = []
    for row in retrieved:
        if row.get("query_variant") != "primary":
            continue
        key = result_id(row["query_id"], row["canonical_url"])
        score_rows.append({
            "query_id": row["query_id"],
            "category": row.get("category", ""),
            "provider": row["provider"],
            "repetition": row.get("repetition", "1"),
            "rank": row["rank"],
            "canonical_url": row["canonical_url"],
            "relevance_grade": grades[key]["grade"],
            "is_official_or_primary": "",
            "is_independent_source": "",
            "is_correct_language": "",
            "is_broken": "",
            "is_spam": "",
            "constraints_met": "",
            "constraints_total": "",
            "subtopics": "",
            "published_at": row.get("published_at", ""),
            "is_latest_official": "",
        })
        query = queries[row["query_id"]]
        reasoning_rows.append({
            "query_id": row["query_id"],
            "query": query["query"],
            "category": query["category"],
            "provider": row["provider"],
            "rank": row["rank"],
            "result_id": key,
            "canonical_url": row["canonical_url"],
            "title": row.get("title", ""),
            "relevance_grade": grades[key]["grade"],
            "reason": grades[key]["reason"],
            "judge_batch": grades[key]["judge_batch"],
        })

    reasoning_fields = [
        "query_id", "query", "category", "provider", "rank", "result_id",
        "canonical_url", "title", "relevance_grade", "reason", "judge_batch",
    ]

    args.out_dir.mkdir(parents=True, exist_ok=True)
    write_csv(args.out_dir / "pooled_judgments_primary_ai.csv", pooled_rows, pooled_fields)
    write_csv(args.out_dir / "scored_results_primary_ai.csv", score_rows, score_fields)
    write_csv(args.out_dir / "reasoning_primary.csv", reasoning_rows, reasoning_fields)
    print(json.dumps({
        "judgments": len(grades),
        "score_rows": len(score_rows),
        "reasoning_rows": len(reasoning_rows),
        "run_rows": len(run_rows),
    }))


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.add_argument("--run-dir", type=Path, required=True)
    prepare_parser.add_argument("--queries", type=Path, required=True)
    prepare_parser.add_argument("--out-dir", type=Path, required=True)
    prepare_parser.add_argument("--queries-per-batch", type=int, default=8)
    prepare_parser.set_defaults(func=prepare)

    merge_parser = subparsers.add_parser("merge")
    merge_parser.add_argument("--run-dir", type=Path, required=True)
    merge_parser.add_argument("--queries", type=Path, required=True)
    merge_parser.add_argument("--judgments-dir", type=Path, required=True)
    merge_parser.add_argument("--out-dir", type=Path, required=True)
    merge_parser.set_defaults(func=merge)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

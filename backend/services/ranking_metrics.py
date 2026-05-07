from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class RankingMetrics:
    precision_at_5: float
    precision_at_10: float
    recall_at_10: float
    map: float
    mrr: float
    ndcg_at_10: float
    agreement_score: float


def _dcg(relevances: Sequence[float], k: int) -> float:
    score = 0.0
    for i, rel in enumerate(relevances[:k], start=1):
        score += (2.0 ** rel - 1.0) / math.log2(i + 1)
    return score


def _ndcg(relevances: Sequence[float], k: int) -> float:
    dcg = _dcg(relevances, k)
    ideal = _dcg(sorted(relevances, reverse=True), k)
    return (dcg / ideal) if ideal > 0 else 0.0


def _precision_at_k(binary_rels: Sequence[int], k: int) -> float:
    if k <= 0:
        return 0.0
    topk = binary_rels[:k]
    return float(sum(topk)) / float(k)


def _recall_at_k(binary_rels: Sequence[int], k: int) -> float:
    total_pos = sum(binary_rels)
    if total_pos <= 0:
        return 0.0
    return float(sum(binary_rels[:k])) / float(total_pos)


def _average_precision(binary_rels: Sequence[int]) -> float:
    hits = 0
    acc = 0.0
    for i, rel in enumerate(binary_rels, start=1):
        if rel:
            hits += 1
            acc += hits / i
    return acc / hits if hits else 0.0


def _reciprocal_rank(binary_rels: Sequence[int]) -> float:
    for i, rel in enumerate(binary_rels, start=1):
        if rel:
            return 1.0 / i
    return 0.0


def compute_ranking_metrics(
    ranked_candidate_ids: List[str],
    labels_by_candidate_id: Dict[str, int],
    *,
    positive_label_threshold: int = 2,
    k_ndcg: int = 10,
) -> RankingMetrics:
    """
    Ranking-aware evaluation for a single batch (one query/JD).

    Labels are 0..3:
      3 strong fit
      2 good fit
      1 partial
      0 not relevant

    We treat >=2 as "relevant" for precision/recall/MAP/MRR, while nDCG uses graded labels.
    """
    # Align arrays to ranking order
    graded: List[int] = [int(labels_by_candidate_id.get(cid, 0)) for cid in ranked_candidate_ids]
    binary: List[int] = [1 if rel >= positive_label_threshold else 0 for rel in graded]

    p5 = _precision_at_k(binary, 5)
    p10 = _precision_at_k(binary, 10)
    r10 = _recall_at_k(binary, 10)
    ap = _average_precision(binary)
    mrr = _reciprocal_rank(binary)
    ndcg10 = _ndcg(graded, k_ndcg)

    # Agreement: simple normalized MAE between model rank order and labels
    # We use (1 - MAE/3) where MAE is mean |label - mean_label| in top 10
    top = graded[:10] if graded else []
    if not top:
        agreement = 0.0
    else:
        mean_label = sum(top) / len(top)
        mae = sum(abs(x - mean_label) for x in top) / len(top)
        agreement = max(0.0, min(1.0, 1.0 - (mae / 3.0)))

    return RankingMetrics(
        precision_at_5=round(p5, 4),
        precision_at_10=round(p10, 4),
        recall_at_10=round(r10, 4),
        map=round(ap, 4),
        mrr=round(mrr, 4),
        ndcg_at_10=round(ndcg10, 4),
        agreement_score=round(agreement, 4),
    )


"""
optimizer.py
============
Module for optimizing attendance waivers to maximize internal marks.

Responsibilities:
    1. Compute marks and attendance % subject-wise based on a slab system.
    2. Optimize waiver days selection to maximize total marks.
    3. Generate combinations using pruning and ranking strategies.
    4. Provide explainability for selected waiver days.

Author: AntiGravity
"""

import math
from itertools import combinations
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from index import SubjectRecord

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_COMBINATIONS = 50_000

# ---------------------------------------------------------------------------
# Core Logic
# ---------------------------------------------------------------------------

@dataclass
class SubjectDelta:
    """Represents the reduction in attendance counts for a single subject on a given date."""
    Th_p: int = 0
    Th_t: int = 0
    tu_p: int = 0
    tu_t: int = 0
    PR_p: int = 0
    PR_t: int = 0


def get_marks(percent: float, subject_type: str, class_type: str) -> float:
    """Calculate internal marks based on the specified slab system."""
    if percent <= 55.0:
        return 0.0
    
    if subject_type == "Core":
        if class_type == "Th":
            if percent < 70.0: return 1.0
            if percent < 75.0: return 2.0
            if percent < 80.0: return 3.0
            if percent < 85.0: return 4.0
            return 5.0
        elif class_type == "tu":
            if percent < 70.0: return 1.2
            if percent < 75.0: return 2.4
            if percent < 80.0: return 3.6
            if percent < 85.0: return 4.8
            return 6.0
    elif subject_type == "Elective":
        if class_type == "Th":
            if percent < 70.0: return 0.4
            if percent < 75.0: return 0.8
            if percent < 80.0: return 1.2
            if percent < 85.0: return 1.6
            return 2.0
    
    return 0.0


def compute_metrics(
    records: list[SubjectRecord], 
    days_to_remove: set[str], 
    priorities: Optional[dict[str, float]] = None
) -> tuple[float, float, list[dict[str, Any]], float]:
    """
    Calculate marks and attendance for the given combination.
    Returns: (actual_total_marks, overall_pct, subject_results, weighted_score)
    """
    total_marks = 0.0
    weighted_score = 0.0
    total_p = 0
    total_t = 0
    subject_results = []
    
    p_map = priorities if priorities is not None else {}

    for rec in records:
        Th_p, Th_t = rec.Th.present, rec.Th.total
        tu_p, tu_t = rec.tu.present, rec.tu.total
        PR_p, PR_t = rec.PR.present, rec.PR.total
        
        # apply removals
        for date in days_to_remove:
            if date in rec.raw_date_entries:
                for status, cls_type in rec.raw_date_entries[date]:
                    if cls_type == "Th":
                        Th_t -= 1
                        if status == "P": Th_p -= 1
                    elif cls_type == "tu":
                        tu_t -= 1
                        if status == "P": tu_p -= 1
                    elif cls_type == "PR":
                        PR_t -= 1
                        if status == "P": PR_p -= 1
        
        # calculate percentages
        th_pct = (Th_p / Th_t * 100) if Th_t > 0 else 0.0
        tu_pct = (tu_p / tu_t * 100) if tu_t > 0 else 0.0
        
        # calculate marks
        th_m = get_marks(th_pct, rec.subject_type, "Th")
        tu_m = get_marks(tu_pct, rec.subject_type, "tu")
        
        m_actual = th_m + tu_m
        weight = p_map.get(rec.subject, 1.0)
        
        total_p += (Th_p + tu_p + PR_p)
        total_t += (Th_t + tu_t + PR_t)
        total_marks += m_actual
        weighted_score += m_actual * weight
        
        subject_results.append({
            "subject": rec.subject,
            "Th_pct": th_pct,
            "Th_marks": th_m,
            "tu_pct": tu_pct,
            "tu_marks": tu_m,
        })
        
    overall_pct = (total_p / total_t * 100) if total_t > 0 else 0.0
    return total_marks, overall_pct, subject_results, weighted_score


def explain_single_day(
    records: list[SubjectRecord], 
    date: str, 
    base_marks: float, 
    base_subj_res: list[dict[str, Any]]
) -> str:
    """Generate human-readable explanation of impact for a single waiver day."""
    m_after, pct_after, req_subj_res, score_after = compute_metrics(records, {date})
    lines = []
    lines.append(f"Removing {date}:")
    
    net_impact = m_after - base_marks
    
    for base, after in zip(base_subj_res, req_subj_res):
        for ctype in ["Th", "tu"]:
            b_pct = base[f"{ctype}_pct"]
            a_pct = after[f"{ctype}_pct"]
            b_m = base[f"{ctype}_marks"]
            a_m = after[f"{ctype}_marks"]
            
            if abs(b_pct - a_pct) > 0.005: 
                diff_m = a_m - b_m
                if diff_m > 0:
                    status = f"slab increase -> +{diff_m:g} mark{'s' if diff_m > 1 else ''}"
                elif diff_m < 0:
                    status = f"slab drop -> {diff_m:g} marks"
                else:
                    status = "no slab change"
                
                lines.append(f"  * {base['subject']} ({ctype}): {b_pct:.1f}% -> {a_pct:.1f}% -> {status}")
                
    lines.append(f"  Net impact: {net_impact:+.2g}")
    return "\n".join(lines)


def build_subject_breakdown(
    base_res: list[dict[str, Any]], 
    final_res: list[dict[str, Any]],
    records: list[SubjectRecord]
) -> list[dict[str, Any]]:
    """Format the before/after comparisons for each subject."""
    breakdowns = []
    for b, f, r in zip(base_res, final_res, records):
        breakdowns.append({
            "subject": b["subject"],
            "category": r.subject_type,
            "Th": {
                "pct_before": b["Th_pct"],
                "pct_after": f["Th_pct"],
                "marks_before": b["Th_marks"],
                "marks_after": f["Th_marks"]
            },
            "tu": {
                "pct_before": b["tu_pct"],
                "pct_after": f["tu_pct"],
                "marks_before": b["tu_marks"],
                "marks_after": f["tu_marks"]
            }
        })
    return breakdowns


# ---------------------------------------------------------------------------
# Main Optimization Engine
# ---------------------------------------------------------------------------

def optimize_waivers(
    records: list[SubjectRecord], 
    num_waivers: int, 
    priorities: Optional[dict[str, float]] = None
) -> dict[str, Any]:
    """
    Optimize waivers to maximize internal marks, using priorities if provided.
    """
    base_marks, base_overall_pct, base_subject_results, base_score = compute_metrics(records, set(), priorities)
    
    if base_overall_pct < 54.995:
        return {
            "error": f"Not eligible. Overall attendance is {base_overall_pct:.2f}% (Needs >= 55.00%)"
        }

    # Use unified combination engine
    best_state = find_best_combination(records, num_waivers, priorities)
    
    # Recompute full metrics for the best combinations mapping
    selected_combo = best_state["combo"]
    
    # Sort chronologically for the schedule table
    sorted_combo = sorted(selected_combo, key=lambda x: datetime.strptime(x.split(',')[0], "%d %b %Y"))
    
    final_m, final_pct, final_subj_results, final_score = compute_metrics(records, set(selected_combo), priorities)
    
    # Explanability
    explanations = []
    for date in sorted_combo:
        explanations.append(explain_single_day(records, date, base_marks, base_subject_results))
        
    return {
        "best_result": {
            "waiver_days": list(sorted_combo),
            "marks_before": base_marks,
            "marks_after": final_m,
            "net_increase": final_m - base_marks,
            "explainability": explanations
        },
        "top_combinations": [
            {
                "days_removed": list(selected_combo),
                "total_marks": final_m,
                "attendance_pct": final_pct
            }
        ],
        "subject_breakdown": build_subject_breakdown(base_subject_results, final_subj_results, records),
        "benchmarks": calculate_benchmarks(records, priorities),
        "optimal_waivers": find_optimal_waivers(records, priorities)["n"]
    }


def find_best_combination(records: list[SubjectRecord], n: int, priorities: Optional[dict[str, float]] = None) -> dict[str, Any]:
    """Perform accurate combination search for N waivers."""
    base_m, base_pct, _, base_score = compute_metrics(records, set(), priorities)
    
    candidate_dates = set()
    for rec in records:
        for date, entries in rec.raw_date_entries.items():
            if any(s == "A" for s, _ in entries):
                candidate_dates.add(date)

    if n == 0 or not candidate_dates:
        return {"marks": base_m, "score": base_score, "pct": base_pct, "combo": []}

    # Isolated impact assessment
    day_scores = []
    for date in candidate_dates:
        m, pct, _, score = compute_metrics(records, {date}, priorities)
        day_scores.append({
            "date": date, 
            "score_inc": score - base_score,
            "m_inc": m - base_m, 
            "pct_inc": pct - base_pct
        })
    day_scores.sort(key=lambda x: (x["score_inc"], x["m_inc"], x["pct_inc"]), reverse=True)
    
    # Combination search constants
    top_k = min(25, len(day_scores))
    actual_N = min(n, len(candidate_dates))
    while top_k > actual_N and math.comb(top_k, actual_N) > 40_000:
        top_k -= 1
    
    top_candidate_dates = [x["date"] for x in day_scores[:top_k]]
    p_map = priorities if priorities is not None else {}
    
    # Pre-build deltas
    date_deltas_list = []
    for date in top_candidate_dates:
        deltas = []
        for rec in records:
            d = SubjectDelta()
            entries = rec.raw_date_entries.get(date)
            if entries:
                for status, cls_type in entries:
                    if cls_type == "Th":
                        d.Th_t += 1
                        if status == "P": d.Th_p += 1
                    elif cls_type == "tu":
                        d.tu_t += 1
                        if status == "P": d.tu_p += 1
                    elif cls_type == "PR":
                        d.PR_t += 1
                        if status == "P": d.PR_p += 1
            deltas.append(d)
        date_deltas_list.append(deltas)

    best_m = -1.0
    best_score = -1.0
    best_pct = 0.0
    best_combo = []

    for combo_indices in combinations(range(len(top_candidate_dates)), actual_N):
        total_m = 0.0; total_score = 0.0; tot_p = 0; tot_t = 0
        for i, rec in enumerate(records):
            Th_p, Th_t = rec.Th.present, rec.Th.total
            tu_p, tu_t = rec.tu.present, rec.tu.total
            PR_p, PR_t = rec.PR.present, rec.PR.total
            for idx in combo_indices:
                d = date_deltas_list[idx][i]
                Th_p -= d.Th_p; Th_t -= d.Th_t
                tu_p -= d.tu_p; tu_t -= d.tu_t
                PR_p -= d.PR_p; PR_t -= d.PR_t
                
            th_pct = (Th_p / Th_t * 100) if Th_t > 0 else 0.0
            tu_pct = (tu_p / tu_t * 100) if tu_t > 0 else 0.0
            th_m = get_marks(th_pct, rec.subject_type, "Th")
            tu_m = get_marks(tu_pct, rec.subject_type, "tu")
            
            m_sum = th_m + tu_m
            total_m += m_sum
            total_score += m_sum * p_map.get(rec.subject, 1.0)
            
            tot_p += (Th_p + tu_p + PR_p)
            tot_t += (Th_t + tu_t + PR_t)
            
        cur_pct = (tot_p / tot_t * 100) if tot_t > 0 else 0.0
        if cur_pct >= 55.0:
            if total_score > best_score:
                best_score = total_score; best_m = total_m; best_pct = cur_pct
                best_combo = [top_candidate_dates[idx] for idx in combo_indices]
            elif abs(total_score - best_score) < 0.001 and total_m > best_m:
                 best_m = total_m; best_pct = cur_pct
                 best_combo = [top_candidate_dates[idx] for idx in combo_indices]
            elif abs(total_score - best_score) < 0.001 and abs(total_m - best_m) < 0.001 and cur_pct > best_pct:
                best_pct = cur_pct
                best_combo = [top_candidate_dates[idx] for idx in combo_indices]

    if best_m < 0: return {"marks": base_m, "score": base_score, "pct": base_pct, "combo": []}
    return {"marks": best_m, "score": best_score, "pct": best_pct, "combo": best_combo}


def calculate_benchmarks(records: list[SubjectRecord], priorities: Optional[dict[str, float]] = None) -> list[dict[str, Any]]:
    """Calculate marks for standard waiver counts + the true optimal peak."""
    # Start with standard counts
    target_counts = {0, 5, 10, 15, 20, 25}
    
    # Add the optimal peak count
    peak_info = find_optimal_waivers(records, priorities)
    target_counts.add(peak_info["n"])
    
    # Sort and compute
    sorted_counts = sorted(list(target_counts))
    benchmarks = []
    for n in sorted_counts:
        res = find_best_combination(records, n, priorities)
        benchmarks.append({
            "n": n,
            "marks": res["marks"],
            "pct": res["pct"]
        })
    return benchmarks


def find_optimal_waivers(records: list[SubjectRecord], priorities: Optional[dict[str, float]] = None) -> dict[str, Any]:
    """Find the waiver count that yields maximum score (weighted marks)."""
    results = []
    # Scan up to 25 waivers
    for n in range(26):
        res = find_best_combination(records, n, priorities)
        results.append({"n": n, "marks": res["marks"], "score": res["score"], "pct": res["pct"]})
    
    # Sort by score descending, then marks descending, then n ascending (prefer fewer waivers for same marks)
    results.sort(key=lambda x: (x["score"], x["marks"], -x["n"]), reverse=True)
    return results[0]

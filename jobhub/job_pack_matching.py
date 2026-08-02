"""Conservative matching for JobHub Job Pack imports.

The importer must never create a duplicate just because punctuation, a street
suffix, or a company suffix differs.  It also must not guess when several jobs
share a broad address such as a retirement-village site.  This module keeps the
matching rules independent of Streamlit so they are easy to test.
"""

from __future__ import annotations

from difflib import SequenceMatcher
import re
import unicodedata
from typing import Any, Iterable, Mapping


_ADDRESS_TOKENS = {
    "street": "st",
    "st": "st",
    "road": "rd",
    "rd": "rd",
    "drive": "dr",
    "dr": "dr",
    "court": "ct",
    "ct": "ct",
    "place": "pl",
    "pl": "pl",
    "avenue": "ave",
    "ave": "ave",
    "av": "ave",
    "crescent": "cres",
    "cres": "cres",
    "circuit": "cct",
    "cct": "cct",
    "terrace": "tce",
    "tce": "tce",
    "parade": "pde",
    "pde": "pde",
    "highway": "hwy",
    "hwy": "hwy",
    "close": "cl",
    "cl": "cl",
    "boulevard": "bvd",
    "blvd": "bvd",
    "bvd": "bvd",
    "lane": "ln",
    "ln": "ln",
}

_COMPANY_NOISE = {
    "pty",
    "ltd",
    "limited",
    "australia",
    "australian",
    "company",
}

_LOCALITY_CORRECTIONS = {
    "maroochydoore": "maroochydore",
    "glasshouse": "glass house",
}


def _ascii_words(value: Any) -> list[str]:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(character for character in text if not unicodedata.combining(character))
    text = text.casefold()
    for wrong, corrected in _LOCALITY_CORRECTIONS.items():
        text = text.replace(wrong, corrected)
    return re.findall(r"[a-z0-9]+", text)


def normalise_identifier(value: Any) -> str:
    """Return a punctuation-insensitive job/PO identifier."""
    return "".join(_ascii_words(value))


def normalise_name(value: Any) -> str:
    return " ".join(_ascii_words(value))


def normalise_builder(value: Any) -> str:
    words = [word for word in _ascii_words(value) if word not in _COMPANY_NOISE]
    return " ".join(words)


def normalise_address(value: Any) -> str:
    words = _ascii_words(value)
    if not words:
        return ""
    normalised = [_ADDRESS_TOKENS.get(word, word) for word in words]
    # State and postcode do not distinguish two records at the same site.
    normalised = [word for word in normalised if word not in {"qld", "queensland"}]
    if normalised and re.fullmatch(r"\d{4}", normalised[-1]):
        normalised.pop()
    return " ".join(normalised)


def _similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    return SequenceMatcher(None, left, right).ratio()


def _number_tokens(value: str) -> set[str]:
    return set(re.findall(r"\b\d+[a-z]?\b", value))


def _address_similarity(left: str, right: str) -> float:
    similarity = _similarity(left, right)
    if not left or not right:
        return similarity
    left_numbers = _number_tokens(left)
    right_numbers = _number_tokens(right)
    if left_numbers and right_numbers and left_numbers != right_numbers:
        return similarity
    left_words = [word for word in left.split() if word not in left_numbers]
    right_words = [word for word in right.split() if word not in right_numbers]
    if left_words and left_words == right_words:
        return max(similarity, 0.96)
    return similarity


def _contract_close(left: Any, right: Any) -> bool:
    try:
        incoming = float(left or 0)
        existing = float(right or 0)
    except (TypeError, ValueError):
        return False
    if incoming <= 0 or existing <= 0:
        return False
    return abs(incoming - existing) <= max(10.0, incoming * 0.01)


def _candidate(summary: Mapping[str, Any], job: Mapping[str, Any]) -> dict[str, Any]:
    incoming_job_no = normalise_identifier(summary.get("job_no"))
    existing_job_no = normalise_identifier(job.get("job_no"))
    incoming_address = normalise_address(summary.get("site_address"))
    existing_address = normalise_address(job.get("site_address"))
    incoming_builder = normalise_builder(summary.get("builder_client"))
    existing_builder = normalise_builder(
        job.get("builder_client") or job.get("builder") or job.get("builder_name")
    )
    incoming_name = normalise_name(summary.get("job_name"))
    existing_name = normalise_name(job.get("job_name"))

    reasons: list[str] = []
    decisive = False
    score = 0.0

    if incoming_job_no and incoming_job_no == existing_job_no:
        score = 100.0
        decisive = True
        reasons.append("exact job number")
    else:
        address_similarity = _address_similarity(incoming_address, existing_address)
        builder_similarity = _similarity(incoming_builder, existing_builder)
        name_similarity = _similarity(incoming_name, existing_name)
        same_address_numbers = _number_tokens(incoming_address) == _number_tokens(existing_address)
        has_address_number = bool(_number_tokens(incoming_address))

        address_exact = bool(incoming_address and incoming_address == existing_address)
        builder_exact = bool(incoming_builder and incoming_builder == existing_builder)
        name_exact = bool(incoming_name and incoming_name == existing_name)

        if address_exact and builder_exact:
            score = 96.0
            reasons.extend(["same address", "same builder/client"])
        elif address_exact and name_exact:
            score = 94.0
            reasons.extend(["same address", "same job name"])
        elif address_exact and has_address_number:
            score = 90.0
            reasons.append("same numbered address")
        elif address_similarity >= 0.92 and same_address_numbers and builder_exact:
            score = 92.0
            reasons.extend(["near-identical address", "same builder/client"])
        elif name_exact and builder_exact:
            score = 88.0
            reasons.extend(["same job name", "same builder/client"])
        elif name_similarity >= 0.90 and builder_similarity >= 0.90 and _contract_close(
            summary.get("contract_value_ex_gst"), job.get("contract_value")
        ):
            score = 86.0
            reasons.extend(["similar job name", "similar builder/client", "matching contract value"])

    label = f"{job.get('job_no', '')} - {job.get('job_name', '')}".strip(" -")
    return {
        "job_id": int(job["id"]),
        "job_no": str(job.get("job_no") or ""),
        "job_name": str(job.get("job_name") or ""),
        "label": label,
        "score": score,
        "decisive": decisive,
        "reasons": reasons,
    }


def match_job_pack_to_jobs(
    summary: Mapping[str, Any],
    jobs: Iterable[Mapping[str, Any]],
    *,
    threshold: float = 85.0,
    minimum_margin: float = 6.0,
) -> dict[str, Any]:
    """Return a conservative automatic match or an explicit review state."""
    candidates = sorted(
        (_candidate(summary, dict(job)) for job in jobs),
        key=lambda item: (-item["score"], item["job_no"], item["job_id"]),
    )
    viable = [item for item in candidates if item["score"] >= threshold]
    if not viable:
        return {"status": "no_match", "match": None, "candidates": candidates[:5]}

    top = viable[0]
    second_score = viable[1]["score"] if len(viable) > 1 else 0.0
    if top["decisive"] or top["score"] - second_score >= minimum_margin:
        return {"status": "matched", "match": top, "candidates": viable[:5]}

    return {"status": "ambiguous", "match": None, "candidates": viable[:5]}

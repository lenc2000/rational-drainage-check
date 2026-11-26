"""
주소 → 위·경도 (OpenStreetMap Nominatim, 무료·키 없음)

지번(예: 563-2)까지는 못 찾는 경우가 많아서, 못 찾으면 뒤에서부터 한 단어씩 빼며
읍·면·리 단위까지 다시 찾는다. 찾은 단계는 결과의 matched 로 알려 준다.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass

URL = "https://nominatim.openstreetmap.org/search"
HEADERS = {"User-Agent": "rational-drainage-check/1.0 (github.com/lenc2000/rational-drainage-check)",
           "Accept-Language": "ko"}

ABBR = {"경북": "경상북도", "경남": "경상남도", "충북": "충청북도", "충남": "충청남도",
        "전북": "전북특별자치도", "전남": "전라남도", "경기": "경기도", "강원": "강원특별자치도"}


@dataclass
class GeoResult:
    lat: float
    lon: float
    display: str     # 지도 서비스가 찾은 이름
    matched: str     # 실제로 찾는 데 쓴 검색어


def _query(q: str, timeout: float = 8.0):
    params = urllib.parse.urlencode({"q": q, "format": "jsonv2", "limit": 1, "countrycodes": "kr"})
    req = urllib.request.Request(f"{URL}?{params}", headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode("utf-8"))
    return data[0] if data else None


def geocode(address: str) -> GeoResult | None:
    toks = [ABBR.get(t, t) for t in address.replace(",", " ").split()]
    while toks:
        q = " ".join(toks)
        hit = _query(q)
        if hit:
            return GeoResult(float(hit["lat"]), float(hit["lon"]), hit.get("display_name", q), q)
        toks = toks[:-1]          # 번지 → 리 → 면 순으로 줄여 가며 다시 찾기
    return None

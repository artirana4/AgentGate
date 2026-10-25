"""
Data provenance tracking for AgentGate.
Registers extracted entities (emails, amounts, filenames) along with source and trust level.
Supports exact, normalized, and bounded difflib approximate matching.
"""

import re
import difflib
import datetime
from typing import Dict, Any, List, Optional, Tuple
from gateway.storage import get_db_connection, init_db

EMAIL_REGEX = re.compile(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+')
AMOUNT_REGEX = re.compile(r'\$?\b\d+(?:,\d{3})*(?:\.\d{1,2})?\b')
FILENAME_REGEX = re.compile(r'\b[a-zA-Z0-9_.-]+\.(?:csv|txt|pdf|json)\b', re.IGNORECASE)


def normalize_value(val: Any) -> str:
    """Normalize string value for matching (lower, stripped, no currency symbols or commas)."""
    s = str(val).strip().lower()
    # Normalize currency/amounts if numeric
    s_clean = s.replace("$", "").replace(",", "")
    return s_clean


def extract_entities_from_text(text: str) -> List[Tuple[str, str]]:
    """Extract (entity_type, value) tuples from text."""
    entities = []
    # Emails
    for match in EMAIL_REGEX.findall(text):
        entities.append(("email", match))
    # Filenames
    for match in FILENAME_REGEX.findall(text):
        entities.append(("filename", match))
    return entities


def register_provenance(
    session_id: str,
    source_id: str,
    source_type: str,
    trust_level: str,
    extracted_value: str,
    db_path: Optional[str] = None
) -> None:
    """Register a value into the session provenance log."""
    init_db(db_path)
    norm = normalize_value(extracted_value)
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn = get_db_connection(db_path)
    with conn:
        conn.execute("""
            INSERT INTO provenance (
                session_id, source_id, source_type, trust_level, extracted_value, normalized_value, timestamp
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (session_id, source_id, source_type, trust_level, extracted_value, norm, now_iso))
    conn.close()


def register_text_provenance(
    session_id: str,
    source_id: str,
    source_type: str,
    trust_level: str,
    text: str,
    db_path: Optional[str] = None
) -> List[Tuple[str, str]]:
    """Extract and register all entities from a body of text (e.g. user instruction or document)."""
    entities = extract_entities_from_text(text)
    for ent_type, val in entities:
        register_provenance(
            session_id=session_id,
            source_id=source_id,
            source_type=source_type,
            trust_level=trust_level,
            extracted_value=val,
            db_path=db_path
        )
    return entities


def get_session_provenance(session_id: str, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Retrieve all provenance records for a session."""
    init_db(db_path)
    conn = get_db_connection(db_path)
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM provenance WHERE session_id = ? ORDER BY id ASC", (session_id,))
        rows = cur.fetchall()
        return [
            {
                "id": r["id"],
                "session_id": r["session_id"],
                "source_id": r["source_id"],
                "source_type": r["source_type"],
                "trust_level": r["trust_level"],
                "extracted_value": r["extracted_value"],
                "normalized_value": r["normalized_value"],
                "timestamp": r["timestamp"]
            }
            for r in rows
        ]
    finally:
        conn.close()


def find_provenance_match(
    session_id: str,
    target_value: Any,
    db_path: Optional[str] = None,
    similarity_cutoff: float = 0.85
) -> Dict[str, Any]:
    """
    Search for target_value in session provenance.
    Evaluates:
      1. Exact match
      2. Normalized match
      3. Approximate match using bounded difflib
    
    Returns match details including trust levels found.
    Note: Provenance matching is approximate and can produce false positives and false negatives.
    Approximate matching alone does not establish a trusted source.
    """
    records = get_session_provenance(session_id, db_path)
    target_str = str(target_value).strip()
    target_norm = normalize_value(target_str)
    
    exact_matches = []
    normalized_matches = []
    approx_matches = []
    
    for rec in records:
        # 1. Exact
        if rec["extracted_value"] == target_str:
            exact_matches.append(rec)
            continue
        # 2. Normalized
        if rec["normalized_value"] == target_norm:
            normalized_matches.append(rec)
            continue
        # 3. Approximate (bounded difflib)
        ratio = difflib.SequenceMatcher(None, target_norm, rec["normalized_value"]).ratio()
        if ratio >= similarity_cutoff:
            approx_rec = dict(rec)
            approx_rec["similarity"] = ratio
            approx_matches.append(approx_rec)
            
    has_trusted = False
    has_untrusted = False
    sources = set()
    match_type = "none"
    matched_records = []
    
    if exact_matches:
        match_type = "exact"
        matched_records = exact_matches
    elif normalized_matches:
        match_type = "normalized"
        matched_records = normalized_matches
    elif approx_matches:
        match_type = "approximate"
        matched_records = approx_matches
        
    for r in matched_records:
        sources.add(r["source_id"])
        if r["trust_level"] == "user" and match_type in ("exact", "normalized"):
            has_trusted = True
        elif r["trust_level"] == "untrusted":
            has_untrusted = True
            
    return {
        "found": bool(matched_records),
        "match_type": match_type,
        "matched_records": matched_records,
        "has_trusted": has_trusted,
        "has_untrusted": has_untrusted,
        "sources": list(sources),
        "only_untrusted": has_untrusted and not has_trusted
    }

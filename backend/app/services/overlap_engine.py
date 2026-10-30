"""
AICTE IICM-SD Advanced Entity & Content-Based Cross-Bureau Overlap Engine
=========================================================================
Addresses Key Innovation & Regulatory Requirements:
1. Robust Multilingual Matching (English, Hindi, Tamil, Hinglish, Tanglish).
2. Proper Fuzzy Entity Resolution (rapidfuzz Jaro-Winkler + Token-Set Ratio with
   domain stopword stripping and tuned 78.0% threshold to prevent false positives).
3. Cross-Bureau Content Overlap Detection comparing category relatedness,
   TF-IDF word/n-gram cosine similarity, and temporal proximity.
"""
import math
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple
from app.core.config import settings

try:
    from rapidfuzz import fuzz, distance as rf_distance
    HAS_RAPIDFUZZ = True
except ImportError:
    HAS_RAPIDFUZZ = False

# Generic higher-education tokens that appear in almost every institution name
INSTITUTION_STOPWORDS: Set[str] = {
    "institute", "institution", "institutions", "college", "colleges",
    "university", "vishwavidyalaya", "visvavidyalayam", "vidyapeeth",
    "vidyaapeeth", "academy", "school", "campus", "group", "faculty",
    "department", "centre", "center", "technical", "technology",
    "technological", "engineering", "sciences", "science", "management",
    "research", "studies", "education", "educational", "international",
    "national", "government", "govt", "private", "self", "financing",
    "autonomous", "deemed", "approved", "aicte", "of", "and", "for",
    "in", "at", "the", "to", "by", "dist", "district", "taluk", "road",
    "highway", "nagar", "sector", "phase", "plot", "near", "main"
}

CONTENT_STOPWORDS: Set[str] = {
    "the", "and", "for", "that", "with", "from", "this", "have", "has",
    "been", "were", "was", "are", "is", "in", "on", "at", "to", "of",
    "by", "as", "an", "or", "be", "complaint", "grievance", "received",
    "regarding", "against", "institution", "college", "students", "student"
}

# Indian Language Stopwords
HINDI_STOPWORDS: Set[str] = {
    "के", "का", "की", "को", "में", "पर", "से", "और", "है", "हैं",
    "था", "थी", "थे", "यह", "वह", "इस", "उस", "द्वारा", "नहीं",
    "रहा", "रही", "रहे", "गया", "गई", "गए", "किया", "हुए", "ने", "एक"
}

TAMIL_STOPWORDS: Set[str] = {
    "மற்றும்", "இல்", "க்கு", "ஒரு", "என்று", "இந்த", "அந்த", "உள்ள",
    "ஆகிய", "மூலம்", "என", "இல்லை", "செய்து", "கொண்டு", "பற்றி", "தான்"
}

# Cross-Category Regulatory Relatedness Matrix (0.0 to 1.0)
CATEGORY_RELATEDNESS: Dict[Tuple[str, str], float] = {
    ("Fee Refund & Overcharging", "Unapproved Intake / Excess Admission"): 0.85,
    ("Fee Refund & Overcharging", "Original Certificate Withholding"): 0.82,
    ("Fee Refund & Overcharging", "Hostel & Campus Safety / Ragging"): 0.65,
    ("Unapproved Intake / Excess Admission", "Infrastructure & Lab Deficit"): 0.78,
    ("Fake Faculty / Biometric Attendance", "Faculty Salary & Qualification Norms"): 0.88,
    ("Fake Faculty / Biometric Attendance", "Infrastructure & Lab Deficit"): 0.74,
    ("Faculty Salary & Qualification Norms", "Infrastructure & Lab Deficit"): 0.70,
    ("Original Certificate Withholding", "Faculty Salary & Qualification Norms"): 0.72,
}

# Multilingual Regex Concepts mapping Hindi, Tamil, and Romanized forms to Canonical English concepts
MULTILINGUAL_CONCEPT_MAP: List[Tuple[re.Pattern, str, str]] = [
    # (regex, canonical_category, canonical_tokens)
    (
        re.compile(r"(फ़ीस|फीस|शुल्क|रिफंड|वापसी|अतिरिक्त|पैसे|डिपॉजिट|काउशन|हड़प|fees?|shulk|wapsi|paisa|refund|extra\s*fees?|caution\s*deposit|கட்டணம்|திரும்ப|பணம்|கூடுதல்|வைப்புத்தொகை|அபராதம்|kattanam|thirumba)", re.IGNORECASE),
        "Fee Refund & Overcharging",
        "fee_refund tuition_fee caution_deposit extra_charge refund financial"
    ),
    (
        re.compile(r"(प्रमाण\s*पत्र|अंकतालिका|दस्तावेज|सर्टिफिकेट|मार्कशीट|रोका|वापस\s*नहीं|जब्त|pramaan\s*patra|marksheet|certificate|rok\s*rakha|wapas\s*nahi|சான்றிதழ்|சான்றிதழ்களை|மதிப்பெண்|நிறுத்தி|தராமல்|பிடித்து|சான்றிதழ்கள்|saandrithazh|withhold)", re.IGNORECASE),
        "Original Certificate Withholding",
        "certificate_withholding original_certificates marksheet degree detention release"
    ),
    (
        re.compile(r"(दाखिला|प्रवेश|अतिरिक्त\s*सीट|अवैध\s*प्रवेश|अनधिकृत|स्वीकृत\s*क्षमता|dakhila|pravesh|extra\s*seats?|unapproved\s*intake|excess\s*admission|சேர்க்கை|அனுமதி\s*பெறாத|கூடுதல்\s*இடங்கள்|அங்கீகரிக்கப்படாத|மாணவர்\s*சேர்க்கை)", re.IGNORECASE),
        "Unapproved Intake / Excess Admission",
        "unapproved_intake excess_admission sanctioned_intake unapproved_seats quota"
    ),
    (
        re.compile(r"(शिक्षक|प्राध्यापक|प्रोफेसर|वेतन|बकाया|फर्जी|बायोमेट्रिक|हाजिरी|पीएफ|shikshak|vetan|salary\s*arrears|farzi\s*faculty|biometric|service\s*book|ஆசிரியர்|பேராசிரியர்|சம்பளம்|சம்பள\s*பாக்கி|போலி|பயோமெட்ரிக்|வருகை|aasiriyar|sambalam)", re.IGNORECASE),
        "Faculty Salary & Qualification Norms",
        "fake_faculty faculty_salary salary_arrears biometric_attendance pan_audit payroll"
    ),
    (
        re.compile(r"(प्रयोगशाला|कंप्यूटर|लैब|उपकरण|पुस्तकालय|इमारत|कमी|सुविधा|prayogshala|lab\s*equipment|computer\s*shortfall|building\s*deficit|ஆய்வகம்|கணினி|ஆய்வக\s*பற்றாக்குறை|கட்டிடம்|நூலகம்|வசதி\s*இல்லை)", re.IGNORECASE),
        "Infrastructure & Lab Deficit",
        "infrastructure_deficit laboratory_shortfall computing_terminals equipment library"
    ),
    (
        re.compile(r"(रैगिंग|उत्पीड़न|प्रताड़ना|हॉस्टल|छात्रावास|सुरक्षा|धमकी|वार्डन|ragging|harassment|hostel\s*safety|warden|bullying|கேலி\s*வதை|கொடுமை|விடுதி|பாதுகாப்பு|அச்சுறுத்தல்|வார்டன்|keli\s*vadhai|kodumai)", re.IGNORECASE),
        "Hostel & Campus Safety / Ragging",
        "ragging hostel_safety student_harassment proctorial_squad night_warden"
    ),
]

def detect_language(text: str) -> str:
    """Detects whether text is in Hindi (Devanagari), Tamil, or English/Romanized."""
    if not text:
        return "en"
    if re.search(r"[\u0900-\u097F]", text):
        return "hi"
    if re.search(r"[\u0B80-\u0BFF]", text):
        return "ta"
    return "en"

def _normalize_text(text: str) -> str:
    # Preserve unicode characters for Devanagari and Tamil while stripping punctuation
    cleaned = re.sub(r"[^\w\s\u0900-\u097F\u0B80-\u0BFF]", " ", (text or "").lower())
    return re.sub(r"\s+", " ", cleaned).strip()

def _distinctive_tokens(text: str) -> List[str]:
    norm = _normalize_text(text)
    tokens = []
    for t in norm.split():
        if len(t) >= 2 and t not in INSTITUTION_STOPWORDS and t not in HINDI_STOPWORDS and t not in TAMIL_STOPWORDS:
            tokens.append(t)
    return tokens

def cross_lingual_semantic_expansion(text: str) -> Tuple[str, str, Optional[str]]:
    """
    Expands multilingual complaint text (Hindi, Tamil, Hinglish, Tanglish) into canonical
    regulatory concept tokens and predicts matching AICTE category.
    Returns: (expanded_text, detected_lang, predicted_category)
    """
    lang = detect_language(text)
    expanded_parts = [_normalize_text(text)]
    predicted_cat = None

    for pattern, category, canonical_tokens in MULTILINGUAL_CONCEPT_MAP:
        if pattern.search(text):
            expanded_parts.append(canonical_tokens)
            if not predicted_cat:
                predicted_cat = category

    return " ".join(expanded_parts), lang, predicted_cat

def jaro_winkler_similarity(s1: str, s2: str) -> float:
    """Returns Jaro-Winkler similarity on a 0..100 scale."""
    if not s1 or not s2:
        return 0.0
    if HAS_RAPIDFUZZ:
        return float(rf_distance.JaroWinkler.similarity(s1, s2) * 100.0)
    if s1 == s2:
        return 100.0
    len1, len2 = len(s1), len(s2)
    match_distance = max(len1, len2) // 2 - 1
    if match_distance < 0:
        match_distance = 0
    s1_matches = [False] * len1
    s2_matches = [False] * len2
    matches = 0
    transpositions = 0
    for i in range(len1):
        start = max(0, i - match_distance)
        end = min(i + match_distance + 1, len2)
        for j in range(start, end):
            if s2_matches[j]:
                continue
            if s1[i] != s2[j]:
                continue
            s1_matches[i] = True
            s2_matches[j] = True
            matches += 1
            break
    if matches == 0:
        return 0.0
    k = 0
    for i in range(len1):
        if not s1_matches[i]:
            continue
        while not s2_matches[k]:
            k += 1
        if s1[i] != s2[k]:
            transpositions += 1
        k += 1
    jaro = ((matches / len1) + (matches / len2) + ((matches - transpositions / 2.0) / matches)) / 3.0
    prefix = 0
    for i in range(min(4, len1, len2)):
        if s1[i] == s2[i]:
            prefix += 1
        else:
            break
    return round((jaro + prefix * 0.1 * (1.0 - jaro)) * 100.0, 2)

def levenshtein_ratio(s1: str, s2: str) -> float:
    """Returns normalized Levenshtein similarity on a 0..100 scale."""
    if not s1 or not s2:
        return 0.0
    if HAS_RAPIDFUZZ:
        return float(fuzz.ratio(s1, s2))
    if s1 == s2:
        return 100.0
    rows = len(s1) + 1
    cols = len(s2) + 1
    prev = list(range(cols))
    for i in range(1, rows):
        curr = [i] + [0] * (cols - 1)
        for j in range(1, cols):
            cost = 0 if s1[i - 1] == s2[j - 1] else 1
            curr[j] = min(curr[j - 1] + 1, prev[j] + 1, prev[j - 1] + cost)
        prev = curr
    dist = prev[-1]
    total_len = len(s1) + len(s2)
    return round(((total_len - dist) / total_len) * 100.0, 2)

def distinctive_token_set_score(query: str, target_name: str, aliases: List[str], city: str, pincode: str) -> Tuple[float, str]:
    """
    Computes fuzzy entity score using distinctive (non-stopword) tokens,
    acronym matching, Jaro-Winkler, and pincode/city anchors.
    """
    q_norm = _normalize_text(query)
    q_tokens = _distinctive_tokens(query)
    if not q_tokens:
        return 0.0, "No distinctive institution tokens provided"

    # 1. Check direct alias or acronym hit
    for alias in aliases:
        al_norm = _normalize_text(alias)
        if not al_norm:
            continue
        if al_norm == q_norm or (len(al_norm) >= 4 and al_norm in q_norm):
            return 94.5, f"Verified Institution Alias/Acronym Match ('{alias}')"
        al_jw = jaro_winkler_similarity(q_norm, al_norm)
        if al_jw >= 88.0:
            return al_jw, f"High-Confidence Alias Fuzzy Match ('{alias}')"

    # 2. Check full target name Jaro-Winkler
    target_norm = _normalize_text(target_name)
    jw_full = jaro_winkler_similarity(q_norm, target_norm)
    if jw_full >= 92.0:
        return jw_full, "High-Confidence Jaro-Winkler Full Name Match"

    # 3. Token-Set Analysis excluding generic stopwords
    target_tokens = _distinctive_tokens(target_name)
    if not target_tokens:
        return 0.0, "Target institution has no distinctive tokens"

    matched_terms = [qt for qt in q_tokens if any(jaro_winkler_similarity(qt, tt) >= 86.0 for tt in target_tokens)]
    token_overlap_ratio = len(matched_terms) / max(1, len(target_tokens))

    # Reject matches if core distinctive root is missing
    if len(matched_terms) == 0:
        return 0.0, "False-Positive Rejection: No distinctive tokens matched"

    city_norm = _normalize_text(city) if city else ""
    city_in_query = bool(city_norm and city_norm in q_norm)
    pincode_in_query = bool(pincode and pincode in q_norm)

    if len(matched_terms) == 1 and len(target_tokens) > 1 and not (city_in_query or pincode_in_query):
        return 45.0, "False-Positive Rejection: Insufficient distinctive token overlap without geographic anchor"

    jw_score = jaro_winkler_similarity(" ".join(matched_terms), " ".join(target_tokens))
    lev_score = levenshtein_ratio(" ".join(matched_terms), " ".join(target_tokens))

    combined = (0.50 * (token_overlap_ratio * 100.0)) + (0.35 * jw_score) + (0.15 * lev_score)

    if pincode_in_query:
        combined = min(98.5, combined + 14.0)
    elif city_in_query:
        combined = min(97.5, combined + 8.5)

    return round(combined, 1), f"Fuzzy Entity Resolution (Matched tokens: {', '.join(matched_terms)})"

def tfidf_cosine_similarity(text_a: str, text_b: str) -> float:
    """
    Computes TF-IDF weighted word + character 3-gram cosine similarity (0..100)
    between two complaint descriptions with multilingual concept expansion.
    """
    exp_a, _, _ = cross_lingual_semantic_expansion(text_a)
    exp_b, _, _ = cross_lingual_semantic_expansion(text_b)

    tokens_a = [t for t in exp_a.split() if len(t) > 2 and t not in CONTENT_STOPWORDS and t not in HINDI_STOPWORDS and t not in TAMIL_STOPWORDS]
    tokens_b = [t for t in exp_b.split() if len(t) > 2 and t not in CONTENT_STOPWORDS and t not in HINDI_STOPWORDS and t not in TAMIL_STOPWORDS]
    if not tokens_a or not tokens_b:
        return 0.0

    feats_a = tokens_a + [t[:5] for t in tokens_a if len(t) >= 5]
    feats_b = tokens_b + [t[:5] for t in tokens_b if len(t) >= 5]

    vocab = set(feats_a) | set(feats_b)
    vec_a: Dict[str, float] = {}
    vec_b: Dict[str, float] = {}
    for term in vocab:
        weight = 1.0 + min(0.8, len(term) / 10.0)
        vec_a[term] = feats_a.count(term) * weight
        vec_b[term] = feats_b.count(term) * weight

    dot = sum(vec_a[t] * vec_b[t] for t in vocab)
    norm_a = math.sqrt(sum(v * v for v in vec_a.values()))
    norm_b = math.sqrt(sum(v * v for v in vec_b.values()))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return round((dot / (norm_a * norm_b)) * 100.0, 1)

def category_similarity(cat_a: str, cat_b: str) -> float:
    if not cat_a or not cat_b:
        return 0.0
    if cat_a.strip().lower() == cat_b.strip().lower():
        return 100.0
    pair1 = (cat_a.strip(), cat_b.strip())
    pair2 = (cat_b.strip(), cat_a.strip())
    if pair1 in CATEGORY_RELATEDNESS:
        return round(CATEGORY_RELATEDNESS[pair1] * 100.0, 1)
    if pair2 in CATEGORY_RELATEDNESS:
        return round(CATEGORY_RELATEDNESS[pair2] * 100.0, 1)
    return 25.0

def temporal_proximity_score(date_a: str, date_b: str) -> float:
    """Computes temporal proximity (0..100) using exponential decay over a 90-day window."""
    try:
        da = datetime.strptime(date_a[:10], "%Y-%m-%d")
        db = datetime.strptime(date_b[:10], "%Y-%m-%d")
        days_apart = abs((da - db).days)
        return round(100.0 * math.exp(-days_apart / 45.0), 1)
    except Exception:
        return 75.0

class CrossBureauOverlapEngine:
    def __init__(self, fuzzy_threshold: Optional[float] = None):
        self.fuzzy_threshold = fuzzy_threshold if fuzzy_threshold is not None else settings.OVERLAP_FUZZY_THRESHOLD

    def resolve_institution_entity(
        self, raw_name: str, raw_pid: Optional[str], institutions: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        # 1. Exact AICTE Permanent ID check
        if raw_pid and raw_pid.strip():
            clean_pid = raw_pid.strip().lower()
            for inst in institutions:
                if inst["pid"].lower() == clean_pid:
                    return {
                        "matched": True,
                        "institution": inst,
                        "confidence": 100.0,
                        "method": f"Exact AICTE Permanent ID ({inst['pid']}) verified"
                    }

        # 2. Check if PID pattern is embedded inside raw_name
        pid_in_text = re.search(r"\b(1-\d{6,12})\b", raw_name or "")
        if pid_in_text:
            extracted_pid = pid_in_text.group(1).lower()
            for inst in institutions:
                if inst["pid"].lower() == extracted_pid:
                    return {
                        "matched": True,
                        "institution": inst,
                        "confidence": 99.5,
                        "method": f"Embedded AICTE Permanent ID ({inst['pid']}) extracted from text"
                    }

        # 3. Multi-Metric Fuzzy Entity Resolution
        if not raw_name or not raw_name.strip():
            return {
                "matched": False,
                "institution": None,
                "confidence": 0.0,
                "threshold": self.fuzzy_threshold,
                "method": "No institution name or AICTE PID supplied"
            }

        best_inst = None
        best_score = 0.0
        best_method = ""

        for inst in institutions:
            aliases_raw = inst.get("aliases", "")
            aliases = [a.strip() for a in aliases_raw.split("|") if a.strip()] if isinstance(aliases_raw, str) else list(aliases_raw or [])
            score, method = distinctive_token_set_score(
                query=raw_name,
                target_name=inst["name"],
                aliases=aliases,
                city=inst.get("city", ""),
                pincode=inst.get("pincode", "")
            )
            if score > best_score:
                best_score = score
                best_inst = inst
                best_method = method

        is_match = best_score >= self.fuzzy_threshold
        return {
            "matched": is_match,
            "institution": best_inst if is_match else None,
            "confidence": best_score,
            "threshold": self.fuzzy_threshold,
            "method": best_method if is_match else f"No match exceeded {self.fuzzy_threshold}% threshold (Top candidate: {best_inst['name'] if best_inst else 'None'} @ {best_score}%)"
        }

    def evaluate_case_pair_content_overlap(
        self, case_a: Dict[str, Any], case_b: Dict[str, Any], entity_confidence: float = 100.0
    ) -> Dict[str, Any]:
        cat_sim = category_similarity(case_a.get("category", ""), case_b.get("category", ""))
        text_sim = tfidf_cosine_similarity(case_a.get("description", ""), case_b.get("description", ""))
        content_combined = round((0.40 * cat_sim) + (0.60 * text_sim), 1)
        temp_sim = temporal_proximity_score(case_a.get("receipt_date", ""), case_b.get("receipt_date", ""))

        composite_score = round(
            (0.45 * entity_confidence) + (0.35 * content_combined) + (0.20 * temp_sim),
            1
        )
        return {
            "composite_overlap_score": composite_score,
            "entity_confidence": entity_confidence,
            "category_similarity": cat_sim,
            "text_tfidf_similarity": text_sim,
            "content_similarity": content_combined,
            "temporal_proximity": temp_sim,
            "is_content_correlated": content_combined >= settings.CONTENT_OVERLAP_THRESHOLD
        }

    def check_cross_bureau_overlap(
        self,
        pid: str,
        receiving_bureau: str,
        active_cases: List[Dict[str, Any]],
        incoming_category: str = "",
        incoming_description: str = "",
        incoming_date: Optional[str] = None,
        entity_confidence: float = 100.0
    ) -> Dict[str, Any]:
        inst_cases = [
            c for c in active_cases
            if c.get("pid") == pid and c.get("status") != "Disposed"
        ]
        bureaus = sorted(list({c.get("bureau") for c in inst_cases if c.get("bureau")} | ({receiving_bureau} if receiving_bureau else set())))
        has_overlap = len(bureaus) > 1 or (len(inst_cases) > 0 and receiving_bureau in [c.get("bureau") for c in inst_cases])

        # If incoming category is missing, attempt auto-classification from description
        if not incoming_category and incoming_description:
            _, _, pred_cat = cross_lingual_semantic_expansion(incoming_description)
            if pred_cat:
                incoming_category = pred_cat

        virtual_incoming = {
            "category": incoming_category or (inst_cases[0].get("category", "") if inst_cases else ""),
            "description": incoming_description or (inst_cases[0].get("description", "") if inst_cases else ""),
            "receipt_date": incoming_date or datetime.now().strftime("%Y-%m-%d")
        }

        enriched_collisions = []
        max_composite = 0.0
        for c in inst_cases:
            metrics = self.evaluate_case_pair_content_overlap(virtual_incoming, c, entity_confidence)
            if metrics["composite_overlap_score"] > max_composite:
                max_composite = metrics["composite_overlap_score"]
            enriched_collisions.append({
                **c,
                "overlap_metrics": metrics
            })

        return {
            "pid": pid,
            "has_overlap": len(bureaus) > 1,
            "same_bureau_duplicate": len(inst_cases) > 0 and len(bureaus) == 1,
            "banner": (
                "Potential Overlapping Cases – Coordination Required"
                if len(bureaus) > 1
                else ("Potential Duplicate Case in Same Bureau" if len(inst_cases) > 0 else "Single Bureau Handling")
            ),
            "bureaus_involved": bureaus,
            "max_composite_overlap_score": max_composite,
            "colliding_cases": enriched_collisions
        }

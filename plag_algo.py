import os
import re
import math
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from html import unescape
from typing import Callable, Dict, List, Optional, Sequence, Set, Tuple
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import requests
from PyPDF2 import PdfReader
from docx import Document


WORD_RE = re.compile(r"[A-Za-z0-9']+")
SENTENCE_RE = re.compile(r"[^.!?\n]+[.!?]?")
TAG_RE = re.compile(r"<[^>]+>")
PARAGRAPH_RE = re.compile(r"[^\n]+(?:\n(?!\s*\n)[^\n]+)*", flags=re.MULTILINE)

STOPWORDS: Set[str] = {
  "a", "about", "above", "after", "again", "against", "all", "am", "an", "and",
  "any", "are", "as", "at", "be", "because", "been", "before", "being", "below",
  "between", "both", "but", "by", "can", "could", "did", "do", "does", "doing",
  "down", "during", "each", "few", "for", "from", "further", "had", "has", "have",
  "having", "he", "her", "here", "hers", "herself", "him", "himself", "his", "how",
  "i", "if", "in", "into", "is", "it", "its", "itself", "just", "me", "more", "most",
  "my", "myself", "no", "nor", "not", "now", "of", "off", "on", "once", "only", "or",
  "other", "our", "ours", "ourselves", "out", "over", "own", "same", "she", "should",
  "so", "some", "such", "than", "that", "the", "their", "theirs", "them", "themselves",
  "then", "there", "these", "they", "this", "those", "through", "to", "too", "under",
  "until", "up", "very", "was", "we", "were", "what", "when", "where", "which", "while",
  "who", "whom", "why", "with", "would", "you", "your", "yours", "yourself", "yourselves",
}

SEMANTIC_CANONICAL_MAP: Dict[str, str] = {
  # Common academic/technical variants mapped to a shared concept root.
  "analyze": "analysis",
  "analyses": "analysis",
  "analytical": "analysis",
  "method": "methodology",
  "methods": "methodology",
  "approach": "methodology",
  "approaches": "methodology",
  "model": "framework",
  "models": "framework",
  "frameworks": "framework",
  "result": "outcome",
  "results": "outcome",
  "outcomes": "outcome",
  "evidence": "finding",
  "findings": "finding",
  "interpret": "inference",
  "interpretation": "inference",
  "infer": "inference",
  "comparison": "compare",
  "comparing": "compare",
  "compared": "compare",
  "evaluation": "evaluate",
  "evaluating": "evaluate",
  "evaluated": "evaluate",
  "student": "learner",
  "students": "learner",
  "learning": "learn",
  "education": "learn",
  "educational": "learn",
}


def _repair_character_spaced_text(text: str) -> str:
  """Fix OCR/PDF artifacts like 'T h i s  i s  t e x t' when strongly detected."""
  if not text:
    return ""

  alpha_tokens = re.findall(r"[A-Za-z]+", text)
  if len(alpha_tokens) < 40:
    return text

  single_letter_ratio = (
    sum(1 for token in alpha_tokens if len(token) == 1) / max(1, len(alpha_tokens))
  )
  if single_letter_ratio < 0.52:
    return text

  return re.sub(r"(?<=\b[A-Za-z])\s+(?=[A-Za-z]\b)", "", text)


def _simple_stem(token: str) -> str:
  token = (token or "").lower()
  if len(token) <= 3:
    return token

  for suffix in ("ization", "ational", "fulness", "ousness", "iveness", "tional", "ement"):
    if token.endswith(suffix) and len(token) > len(suffix) + 2:
      return token[: -len(suffix)]

  for suffix in ("ingly", "edly", "ments", "ment", "ation", "ities", "ity", "ing", "ers", "er", "ed", "ly", "es", "s"):
    if token.endswith(suffix) and len(token) > len(suffix) + 2:
      return token[: -len(suffix)]

  return token


def _canonical_token(token: str) -> str:
  return SEMANTIC_CANONICAL_MAP.get(token, token)


def _content_tokens(tokens: Sequence[str]) -> List[str]:
  stemmed = [_simple_stem(token) for token in tokens if token]
  content = [_canonical_token(token) for token in stemmed if token and token not in STOPWORDS]
  return content if content else [_canonical_token(token) for token in stemmed if token]


def _char_ngrams(text: str, n: int = 4) -> Counter:
  normalized = _normalize_text(text)
  if not normalized:
    return Counter()
  if len(normalized) <= n:
    return Counter({normalized: 1})

  return Counter(normalized[index : index + n] for index in range(len(normalized) - n + 1))


def _counter_cosine_similarity(left: Counter, right: Counter) -> float:
  if not left or not right:
    return 0.0

  dot = sum(value * right.get(key, 0) for key, value in left.items())
  if dot <= 0:
    return 0.0

  left_norm = math.sqrt(sum(value * value for value in left.values()))
  right_norm = math.sqrt(sum(value * value for value in right.values()))
  if left_norm == 0.0 or right_norm == 0.0:
    return 0.0

  return max(0.0, min(1.0, dot / (left_norm * right_norm)))


def _set_jaccard_similarity(left: Set[str], right: Set[str]) -> float:
  if not left or not right:
    return 0.0

  union = left | right
  if not union:
    return 0.0
  return len(left & right) / len(union)


def _angle_similarity(cosine_value: float) -> float:
  """Convert cosine to angle-based similarity: 1 - sin(theta)."""
  clamped = max(0.0, min(1.0, cosine_value))
  sine_theta = math.sqrt(max(0.0, 1.0 - (clamped * clamped)))
  return 1.0 - sine_theta


def _build_span_features(tokens: Sequence[str], normalized_text: str) -> Dict[str, object]:
  content_tokens = _content_tokens(tokens)
  content_counter = Counter(content_tokens)
  concept_set = set(content_tokens)

  return {
    "content_tokens": content_tokens,
    "term_freq": content_counter,
    "concept_set": concept_set,
    "chargrams": _char_ngrams(normalized_text, n=4),
    "ngrams": _make_ngrams(content_tokens, n=3),
  }


def _span_similarity(left: Dict[str, object], right: Dict[str, object]) -> Tuple[float, Dict[str, float]]:
  token_cos = _counter_cosine_similarity(left["term_freq"], right["term_freq"])
  char_cos = _counter_cosine_similarity(left["chargrams"], right["chargrams"])
  concept_jaccard = _set_jaccard_similarity(left["concept_set"], right["concept_set"])
  sequence_ratio = SequenceMatcher(None, left["norm"], right["norm"]).ratio()
  angle_similarity = _angle_similarity(token_cos)

  # Multi-factor blend: lexical, structural, and approximate semantic alignment.
  score = (
    (0.34 * token_cos)
    + (0.18 * char_cos)
    + (0.18 * sequence_ratio)
    + (0.18 * concept_jaccard)
    + (0.12 * angle_similarity)
  )

  return (
    max(0.0, min(1.0, score)),
    {
      "token_cos": token_cos,
      "char_cos": char_cos,
      "sequence": sequence_ratio,
      "concept": concept_jaccard,
      "angle": angle_similarity,
    },
  )


def _paragraph_spans(text: str, min_words: int = 12) -> List[Dict[str, object]]:
  paragraphs: List[Dict[str, object]] = []
  for match in PARAGRAPH_RE.finditer(text or ""):
    raw = _collapse_whitespace(match.group())
    if not raw:
      continue

    tokens = _tokenize(raw)
    if len(tokens) < min_words:
      continue

    normalized = " ".join(tokens)
    paragraph = {
      "raw": raw,
      "norm": normalized,
      "tokens": tokens,
      "start": match.start(),
      "end": match.end(),
    }
    paragraph.update(_build_span_features(tokens, normalized))
    paragraphs.append(paragraph)

  return paragraphs


def _assign_paragraph_ids(sentences: List[Dict[str, object]], paragraphs: Sequence[Dict[str, object]]) -> None:
  paragraph_index = 0
  total_paragraphs = len(paragraphs)

  for sentence in sentences:
    sentence["paragraph_id"] = -1
    if total_paragraphs == 0:
      continue

    start = int(sentence["start"])
    while paragraph_index + 1 < total_paragraphs and start >= int(paragraphs[paragraph_index]["end"]):
      paragraph_index += 1

    paragraph = paragraphs[paragraph_index]
    if int(paragraph["start"]) <= start < int(paragraph["end"]):
      sentence["paragraph_id"] = paragraph_index


def process_file(file_path: str) -> str:
  """Read text from supported file types."""
  try:
    if file_path.endswith(".txt"):
      with open(file_path, "r", encoding="utf-8", errors="ignore") as file:
        content = file.read()
    elif file_path.endswith(".docx"):
      doc = Document(file_path)
      content = "\n".join(para.text for para in doc.paragraphs)
    elif file_path.endswith(".pdf"):
      reader = PdfReader(file_path)
      content = "\n".join((page.extract_text() or "") for page in reader.pages)
    else:
      return ""
  except Exception:
    return ""

  cleaned = _repair_character_spaced_text(content)
  return cleaned.strip()


def _collapse_whitespace(text: str) -> str:
  return re.sub(r"\s+", " ", text or "").strip()


def _normalize_text(text: str) -> str:
  return " ".join(WORD_RE.findall((text or "").lower()))


def _tokenize(text: str) -> List[str]:
  normalized = _normalize_text(text)
  return normalized.split() if normalized else []


def extract_query_keywords(text: str, top_k: int = 8) -> str:
  """Extract a concise keyword query from input text for API searching."""
  tokens = _tokenize(text)
  filtered = [
    token for token in tokens
    if len(token) >= 4 and token not in STOPWORDS and not token.isdigit()
  ]

  if not filtered:
    return ""

  counts = Counter(filtered)
  selected = [word for word, _count in counts.most_common(max(4, top_k))]

  # Keep ordering stable based on first appearance in text.
  seen = set()
  ordered = []
  for token in filtered:
    if token in selected and token not in seen:
      seen.add(token)
      ordered.append(token)
    if len(ordered) >= top_k:
      break

  return " ".join(ordered)


def _emit_progress(
  progress_callback: Optional[Callable[[str], None]],
  message: str,
) -> None:
  if not progress_callback:
    return

  try:
    progress_callback(message)
  except Exception:
    # Progress updates should never interrupt core processing.
    pass


def _build_paragraph_search_queries(
  query: str,
  source_text: str = "",
  max_queries: int = 4,
  allow_fallback: bool = True,
) -> List[str]:
  """Build paragraph-level search queries from user text and optional explicit query."""
  cleaned_query = _collapse_whitespace(query)
  paragraph_candidates: List[Tuple[int, int, int, int, int, str]] = []

  # For long documents, bias focus terms toward the opening section/topic statement.
  if cleaned_query:
    focus_seed = cleaned_query
  else:
    leading_tokens = _tokenize(source_text)[:220]
    focus_seed = " ".join(leading_tokens)

  focus_terms = set(extract_query_keywords(focus_seed, top_k=12).split())
  if cleaned_query:
    focus_terms.update(
      token for token in _tokenize(cleaned_query) if len(token) >= 4 and token not in STOPWORDS
    )

  if source_text:
    for index, match in enumerate(PARAGRAPH_RE.finditer(source_text)):
      raw_block = match.group()
      raw_paragraph = _collapse_whitespace(raw_block)
      if not raw_paragraph:
        continue

      tokens = _tokenize(raw_paragraph)
      if len(tokens) < 14:
        continue

      content_tokens = [
        token for token in tokens if token not in STOPWORDS and len(token) >= 4
      ]
      content_diversity = len(set(content_tokens))
      if content_diversity < 6:
        continue

      focus_overlap = len(set(content_tokens) & focus_terms) if focus_terms else 0
      list_penalty = raw_block.count(";") + max(0, raw_block.count("\n") - 1)
      position_bias = 1 if index < 4 else 0

      query_tokens = tokens[:26]
      query_text = " ".join(query_tokens)
      paragraph_candidates.append(
        (
          focus_overlap + position_bias,
          list_penalty,
          content_diversity,
          len(query_tokens),
          index,
          query_text,
        )
      )

  selected_queries: List[str] = []
  seen = set()
  for _focus, _list_penalty, _diversity, _token_count, _index, candidate in sorted(
    paragraph_candidates,
    key=lambda value: (-value[0], value[1], -value[2], -value[3], value[4]),
  ):
    normalized_candidate = _normalize_text(candidate)
    if not normalized_candidate or normalized_candidate in seen:
      continue

    seen.add(normalized_candidate)
    selected_queries.append(candidate)
    if len(selected_queries) >= max_queries:
      break

  if selected_queries:
    return selected_queries

  if not allow_fallback:
    return []

  fallback_queries: List[str] = []
  if cleaned_query:
    fallback_queries.append(" ".join(cleaned_query.split()[:20]))

  if source_text:
    fallback_keyword_query = extract_query_keywords(source_text, top_k=12)
    if fallback_keyword_query and _normalize_text(fallback_keyword_query) not in {
      _normalize_text(value) for value in fallback_queries
    }:
      fallback_queries.append(fallback_keyword_query)

  if fallback_queries:
    return fallback_queries[: max(1, min(2, max_queries))]

  return [cleaned_query] if cleaned_query else []


def _deduplicate_reference_items(
  references: Sequence[Dict[str, str]],
  max_results: int = 0,
) -> List[Dict[str, str]]:
  deduplicated: List[Dict[str, str]] = []
  seen = set()

  for reference in references:
    url_key = _collapse_whitespace(reference.get("url", "")).lower()
    title_key = _collapse_whitespace(reference.get("title", "")).lower()
    text_key = _normalize_text((reference.get("text", "") or "")[:240])
    key = url_key or f"{title_key}|{text_key}"
    if not key or key in seen:
      continue

    seen.add(key)
    deduplicated.append(reference)
    if max_results > 0 and len(deduplicated) >= max_results:
      break

  return deduplicated


def _fetch_firsthand_web_sources(
  queries: Sequence[str],
  timeout: int,
  max_results: int,
  progress_callback: Optional[Callable[[str], None]] = None,
) -> Tuple[List[Dict[str, str]], List[str], str]:
  issues: List[str] = []
  providers: Sequence[Tuple[str, Callable[..., List[Dict[str, str]]]]] = (
    ("DuckDuckGo", fetch_duckduckgo_results),
    ("Grokipedia", fetch_grokipedia_results),
    ("Wikipedia", fetch_wikipedia_results),
  )

  for source_name, provider in providers:
    _emit_progress(progress_callback, f"Collecting web sources: {source_name}")
    source_results: List[Dict[str, str]] = []

    for query in queries:
      try:
        fetched = provider(
          query,
          max_results=max_results,
          timeout=min(timeout, 7),
        )
      except Exception as exc:
        issues.append(f"{source_name} unavailable ({exc.__class__.__name__})")
        continue

      if not fetched:
        continue

      source_results.extend(fetched)
      source_results = _deduplicate_reference_items(source_results, max_results=max_results)
      if len(source_results) >= max_results:
        break

    source_results = _deduplicate_reference_items(source_results, max_results=max_results)
    if source_results:
      return source_results, issues, source_name

  return [], issues, ""


def _fetch_web_sources_for_queries(
  queries: Sequence[str],
  timeout: int,
  serpapi_api_key: str,
  max_results: int = 3,
  progress_callback: Optional[Callable[[str], None]] = None,
) -> Tuple[List[Dict[str, str]], List[str], str]:
  if not queries:
    return [], ["No paragraph query available for web search."], ""

  first_hand_results, issues, first_hand_source = _fetch_firsthand_web_sources(
    queries=queries,
    timeout=timeout,
    max_results=max_results,
    progress_callback=progress_callback,
  )
  if first_hand_results:
    return first_hand_results, issues, first_hand_source

  if not serpapi_api_key:
    issues.append("SerpApi unavailable (missing SERPAPI_API_KEY)")
    return [], issues, ""

  _emit_progress(progress_callback, "Collecting web sources: SerpApi fallback")
  serpapi_results: List[Dict[str, str]] = []
  for query in queries:
    fetched = fetch_serpapi_results(
      query,
      api_key=serpapi_api_key,
      max_results=max_results,
      timeout=min(timeout, 7),
    )
    if not fetched:
      continue

    serpapi_results.extend(fetched)
    serpapi_results = _deduplicate_reference_items(serpapi_results, max_results=max_results)
    if len(serpapi_results) >= max_results:
      break

  if serpapi_results:
    return serpapi_results, issues, "SerpApi"

  return [], issues, ""


def _make_ngrams(tokens: Sequence[str], n: int = 3) -> set:
  if not tokens:
    return set()
  if len(tokens) < n:
    return {" ".join(tokens)}
  return {" ".join(tokens[i : i + n]) for i in range(len(tokens) - n + 1)}


def _sentence_spans(text: str, min_words: int = 3) -> List[Dict[str, object]]:
  sentences: List[Dict[str, object]] = []
  for match in SENTENCE_RE.finditer(text or ""):
    raw = _collapse_whitespace(match.group())
    if not raw:
      continue

    tokens = _tokenize(raw)
    if len(tokens) < min_words:
      continue

    normalized = " ".join(tokens)
    sentence = {
      "raw": raw,
      "norm": normalized,
      "tokens": tokens,
      "start": match.start(),
      "end": match.end(),
    }
    sentence.update(_build_span_features(tokens, normalized))

    sentences.append(sentence)
  return sentences


def _classify_match(score: float) -> str:
  if score >= 0.92:
    return "Exact Match"
  if score >= 0.78:
    return "Near Match"
  return "Paraphrased Overlap"


def _strip_html_text(text: str) -> str:
  text = TAG_RE.sub(" ", text or "")
  return _collapse_whitespace(unescape(text))


def _openalex_abstract_from_index(inverted_index: Dict[str, List[int]]) -> str:
  if not inverted_index:
    return ""

  last_position = max(
    (max(positions) for positions in inverted_index.values() if positions),
    default=-1,
  )
  if last_position < 0:
    return ""

  words = [""] * (last_position + 1)
  for token, positions in inverted_index.items():
    for position in positions:
      if 0 <= position <= last_position:
        words[position] = token

  return _collapse_whitespace(" ".join(word for word in words if word))


def fetch_duckduckgo_results(query: str, max_results: int = 10, timeout: int = 6) -> List[Dict[str, str]]:
  """Fetch web search results from DuckDuckGo Lite (free, no API key)."""
  results = []
  try:
    encoded_query = quote_plus(query)
    url = f"https://lite.duckduckgo.com/lite/?q={encoded_query}"
    response = requests.get(
      url,
      timeout=timeout,
      headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
    )
    response.raise_for_status()
    
    html = response.text
    # DuckDuckGo Lite uses single quotes for class attributes on many pages.
    link_pattern = re.compile(
      r"<a(?=[^>]*class=['\"][^'\"]*result-link[^'\"]*['\"])(?=[^>]*href=['\"]([^'\"]+)['\"])[^>]*>(.*?)</a>",
      flags=re.IGNORECASE | re.DOTALL,
    )
    snippet_pattern = re.compile(
      r"<td[^>]+class=['\"][^'\"]*result-snippet[^'\"]*['\"][^>]*>(.*?)</td>",
      flags=re.IGNORECASE | re.DOTALL,
    )
    
    links = link_pattern.findall(html)
    snippets = snippet_pattern.findall(html)
    
    for i, (link, title_html) in enumerate(links[:max_results]):
      snippet = snippets[i] if i < len(snippets) else ""
      snippet_text = _strip_html_text(snippet)
      title = _strip_html_text(title_html)

      if link.startswith("//duckduckgo.com/l/?") or link.startswith("/l/?"):
        parsed_url = f"https:{link}" if link.startswith("//") else f"https://duckduckgo.com{link}"
        parsed = parse_qs(urlparse(parsed_url).query)
        target = parsed.get("uddg", [""])[0]
        if target:
          link = unquote(target)
      
      if len(snippet_text) < 30:
        continue
        
      results.append({
        "title": title[:200],
        "url": link,
        "text": snippet_text[:1500],
        "source": "DuckDuckGo",
      })
  except Exception:
    pass
  
  return results


def fetch_wikipedia_results(query: str, max_results: int = 5, timeout: int = 6) -> List[Dict[str, str]]:
  """Fetch encyclopedia content from Wikipedia API (free, no API key)."""
  results = []
  try:
    # Search for relevant articles
    search_url = "https://en.wikipedia.org/w/api.php"
    search_params = {
      "action": "opensearch",
      "search": query,
      "limit": max_results,
      "format": "json",
    }
    search_response = requests.get(search_url, params=search_params, timeout=timeout)
    search_response.raise_for_status()
    search_data = search_response.json()
    
    titles = search_data[1] if len(search_data) > 1 else []
    urls = search_data[3] if len(search_data) > 3 else []
    
    # Fetch extracts for each article
    for i, title in enumerate(titles[:max_results]):
      extract_params = {
        "action": "query",
        "titles": title,
        "prop": "extracts",
        "exintro": True,
        "explaintext": True,
        "format": "json",
      }
      extract_response = requests.get(search_url, params=extract_params, timeout=timeout)
      extract_response.raise_for_status()
      extract_data = extract_response.json()
      
      pages = extract_data.get("query", {}).get("pages", {})
      for page in pages.values():
        extract = page.get("extract", "")
        if len(extract) >= 100:
          results.append({
            "title": title,
            "url": urls[i] if i < len(urls) else "",
            "text": extract[:3000],
            "source": "Wikipedia",
          })
          break
  except Exception:
    pass
  
  return results


def fetch_grokipedia_results(query: str, max_results: int = 5, timeout: int = 6) -> List[Dict[str, str]]:
  """Fetch results from Grokipedia, trying API first then HTML search fallback."""
  results: List[Dict[str, str]] = []
  headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
  }

  try:
    api_response = requests.get(
      "https://grokipedia.com/api/search",
      params={"q": query, "limit": max_results},
      timeout=timeout,
      headers=headers,
    )
    if api_response.ok:
      payload = api_response.json()
      if isinstance(payload, dict):
        items = payload.get("results") or payload.get("items") or []
      elif isinstance(payload, list):
        items = payload
      else:
        items = []

      for item in items[:max_results]:
        if not isinstance(item, dict):
          continue

        title = _collapse_whitespace(item.get("title", ""))
        link = _collapse_whitespace(item.get("url", "") or item.get("link", ""))
        snippet = _collapse_whitespace(
          item.get("snippet", "")
          or item.get("summary", "")
          or item.get("description", "")
        )
        if not link or len(snippet) < 45:
          continue

        results.append(
          {
            "title": title[:200] if title else "Untitled",
            "url": link,
            "text": snippet[:1800],
            "source": "Grokipedia",
          }
        )

      results = _deduplicate_reference_items(results, max_results=max_results)
      if results:
        return results
  except Exception:
    pass

  try:
    page_response = requests.get(
      "https://grokipedia.com/search",
      params={"q": query},
      timeout=timeout,
      headers=headers,
    )
    page_response.raise_for_status()
    html = page_response.text

    link_pattern = re.compile(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL)
    snippet_pattern = re.compile(r'<p[^>]*>(.*?)</p>', re.IGNORECASE | re.DOTALL)
    snippets = [_strip_html_text(value) for value in snippet_pattern.findall(html)]

    for index, (link, title_html) in enumerate(link_pattern.findall(html)):
      title = _strip_html_text(title_html)
      if len(title) < 6:
        continue

      if link.startswith("/"):
        link = f"https://grokipedia.com{link}"
      if not link.startswith("http"):
        continue

      snippet = snippets[index] if index < len(snippets) else ""
      if len(snippet) < 60:
        continue

      results.append(
        {
          "title": title[:200],
          "url": link,
          "text": snippet[:1800],
          "source": "Grokipedia",
        }
      )
      if len(results) >= max_results:
        break
  except Exception:
    pass

  return _deduplicate_reference_items(results, max_results=max_results)


def fetch_serpapi_results(query: str, api_key: str = "", max_results: int = 10, timeout: int = 6) -> List[Dict[str, str]]:
  """Fetch web results from SerpApi as a second-hand fallback."""
  results = []
  if not api_key:
    return results

  try:
    language = os.getenv("SERPAPI_HL", "en").strip() or "en"
    region = os.getenv("SERPAPI_GL", "us").strip() or "us"
    engine = os.getenv("SERPAPI_ENGINE", "google").strip() or "google"

    response = requests.get(
      "https://serpapi.com/search.json",
      params={
        "engine": engine,
        "q": query,
        "api_key": api_key,
        "num": min(10, max(1, max_results)),
        "hl": language,
        "gl": region,
      },
      timeout=timeout,
      headers={"User-Agent": "AuthentiText/1.0"},
    )
    response.raise_for_status()
    payload = response.json()

    for item in payload.get("organic_results", []):
      title = _collapse_whitespace(item.get("title", ""))
      link = _collapse_whitespace(item.get("link", ""))
      snippet = _collapse_whitespace(item.get("snippet", ""))
      if not link or len(snippet) < 45:
        continue

      results.append(
        {
          "title": title[:200] if title else "Untitled",
          "url": link,
          "text": snippet[:1800],
          "source": "SerpApi",
        }
      )
      if len(results) >= max_results:
        break
  except Exception:
    pass

  return results


def fetch_reference_from_url(url: str, timeout: int = 8) -> Tuple[Optional[Dict[str, str]], str]:
  """Fetch plain text content from a URL as a manual reference source."""
  try:
    response = requests.get(
      url,
      timeout=timeout,
      headers={"User-Agent": "AuthentiText/1.0"},
    )
    response.raise_for_status()
  except requests.RequestException as exc:
    return None, f"Unable to fetch URL source: {exc}"

  html_text = response.text
  title_match = re.search(
    r"<title[^>]*>(.*?)</title>", html_text, flags=re.IGNORECASE | re.DOTALL
  )
  title = _strip_html_text(title_match.group(1)) if title_match else url

  html_text = re.sub(
    r"<(script|style)[^>]*>.*?</\1>",
    " ",
    html_text,
    flags=re.IGNORECASE | re.DOTALL,
  )
  text_content = _strip_html_text(html_text)

  if len(text_content) < 180:
    return None, "The URL does not contain enough readable text for comparison."

  return (
    {
      "title": title or url,
      "url": url,
      "text": text_content[:18000],
      "source": "URL Import",
    },
    "URL source imported successfully.",
  )


def _fetch_readable_text_from_url(
  url: str,
  timeout: int = 8,
  max_chars: int = 22000,
) -> Tuple[str, str]:
  """Fetch and clean readable text from a result URL for deeper similarity checks."""
  cleaned_url = _collapse_whitespace(url)
  if not cleaned_url or not cleaned_url.lower().startswith(("http://", "https://")):
    return "", ""

  try:
    response = requests.get(
      cleaned_url,
      timeout=timeout,
      headers={
        "User-Agent": "AuthentiText/1.0",
        "Accept": "text/html,application/xhtml+xml",
      },
    )
    response.raise_for_status()
  except requests.RequestException:
    return "", ""

  html_text = response.text or ""
  if len(html_text) < 200:
    return "", ""

  title_match = re.search(
    r"<title[^>]*>(.*?)</title>", html_text, flags=re.IGNORECASE | re.DOTALL
  )
  title = _strip_html_text(title_match.group(1)) if title_match else ""

  html_text = re.sub(
    r"<(script|style|noscript|svg)[^>]*>.*?</\1>",
    " ",
    html_text,
    flags=re.IGNORECASE | re.DOTALL,
  )
  text_content = _strip_html_text(html_text)
  if len(text_content) < 260:
    return title, ""

  return title, text_content[:max_chars]


def _expand_web_references_with_page_text(
  references: Sequence[Dict[str, str]],
  timeout: int,
  progress_callback: Optional[Callable[[str], None]] = None,
) -> Tuple[List[Dict[str, str]], int]:
  """Replace short snippets with fetched page text for top web references."""
  expanded_references: List[Dict[str, str]] = []
  expanded_count = 0
  total = len(references)

  for index, reference in enumerate(references, start=1):
    current = dict(reference)
    source_name = _collapse_whitespace(current.get("source", ""))
    url = _collapse_whitespace(current.get("url", ""))

    if source_name in {"DuckDuckGo", "Grokipedia", "SerpApi", "Wikipedia"} and url:
      _emit_progress(progress_callback, f"Expanding web pages ({index}/{total})")
      page_title, page_text = _fetch_readable_text_from_url(
        url,
        timeout=min(timeout, 8),
      )
      if len(page_text) >= 260:
        current["text"] = page_text
        if page_title and len(page_title) >= 4:
          current["title"] = page_title[:200]
        expanded_count += 1

    expanded_references.append(current)

  return expanded_references, expanded_count


def fetch_web_reference_texts(
  source_text: str,
  query: str = "",
  max_results: int = 3,
  timeout: int = 8,
  progress_callback: Optional[Callable[[str], None]] = None,
) -> Tuple[List[Dict[str, str]], str]:
  """Fetch references from web-only sources using paragraph-driven queries."""
  cleaned_query = _collapse_whitespace(query)
  cleaned_source_text = (source_text or "").strip()
  if not cleaned_query and not cleaned_source_text:
    return [], "Enter or upload text first to run paragraph-based web search."

  _emit_progress(progress_callback, "Preparing paragraph web queries")
  web_queries = _build_paragraph_search_queries(
    cleaned_query,
    source_text=cleaned_source_text,
    max_queries=4,
    allow_fallback=True,
  )
  if not web_queries:
    return [], "Unable to build paragraph search queries from the provided text."

  serpapi_api_key = (
    os.getenv("SERPAPI_API_KEY", "").strip()
    or os.getenv("SERPAPI_KEY", "").strip()
  )
  web_results, issues, source_name = _fetch_web_sources_for_queries(
    queries=web_queries,
    timeout=timeout,
    serpapi_api_key=serpapi_api_key,
    max_results=max(1, min(3, max_results)),
    progress_callback=progress_callback,
  )
  web_results = _deduplicate_reference_items(
    web_results,
    max_results=max(1, min(3, max_results)),
  )

  if web_results:
    web_results, expanded_pages = _expand_web_references_with_page_text(
      web_results,
      timeout=timeout,
      progress_callback=progress_callback,
    )
  else:
    expanded_pages = 0

  if web_results:
    source_used = source_name or "web search"
    message = (
      f"Loaded {len(web_results)} web references from {source_used}. "
      "Search order: DuckDuckGo -> Grokipedia -> Wikipedia -> SerpApi fallback."
    )
    if expanded_pages > 0:
      message += f" Expanded {expanded_pages} web pages for deeper comparison."
    if issues:
      message += " Some providers were unavailable during lookup."
    if not serpapi_api_key and source_used != "SerpApi":
      message += " SerpApi fallback is disabled (missing SERPAPI_API_KEY)."
    return web_results, message

  if not serpapi_api_key:
    issues.append("SerpApi unavailable (missing SERPAPI_API_KEY)")

  if issues:
    return [], "No web references found. " + "; ".join(sorted(set(issues)))

  return [], "No web references found from DuckDuckGo, Grokipedia, Wikipedia, or SerpApi."


def fetch_reference_texts(
  query: str,
  max_results: int = 20,
  timeout: int = 8,
  source_text: str = "",
  progress_callback: Optional[Callable[[str], None]] = None,
) -> Tuple[List[Dict[str, str]], str]:
  """Fetch references for topic/DOI mode: academic docs + web/SerpApi lookups."""
  cleaned_query = _collapse_whitespace(query)
  if not cleaned_query:
    return [], "Enter a topic, DOI, title, or URL first."

  _emit_progress(progress_callback, "Preparing paragraph web queries")
  web_queries = _build_paragraph_search_queries(
    cleaned_query,
    source_text=source_text,
    max_queries=4,
    allow_fallback=True,
  )
  primary_query = cleaned_query

  references: List[Dict[str, str]] = []
  issues: List[str] = []
  hints: List[str] = []

  _emit_progress(progress_callback, "Collecting sources: Crossref")
  try:
    crossref_rows = min(48, max(16, max_results * 2))
    crossref_response = requests.get(
      "https://api.crossref.org/works",
      params={"query.bibliographic": primary_query, "rows": crossref_rows},
      timeout=timeout,
      headers={
        "User-Agent": "AuthentiText/1.0 (mailto:example@example.com)",
      },
    )
    crossref_response.raise_for_status()
    items = crossref_response.json().get("message", {}).get("items", [])

    for item in items:
      title_values = item.get("title") or []
      title = _collapse_whitespace(title_values[0]) if title_values else "Untitled"
      abstract = _strip_html_text(item.get("abstract", ""))
      if not abstract:
        continue

      doi = item.get("DOI")
      url = item.get("URL") or (f"https://doi.org/{doi}" if doi else "")
      references.append(
        {
          "title": title,
          "url": url,
          "text": abstract,
          "source": "Crossref",
        }
      )
  except requests.RequestException as exc:
    issues.append(f"Crossref unavailable ({exc.__class__.__name__})")

  # OpenAlex has broader free abstract coverage and works well as fallback.
  if len(references) < max_results:
    _emit_progress(progress_callback, "Collecting sources: OpenAlex")
    try:
      per_page = min(32, max(14, max_results))
      for page in range(1, 3):
        if len(references) >= max_results * 2:
          break

        openalex_response = requests.get(
          "https://api.openalex.org/works",
          params={"search": primary_query, "per-page": per_page, "page": page},
          timeout=timeout,
        )
        openalex_response.raise_for_status()
        results = openalex_response.json().get("results", [])

        for item in results:
          abstract = _openalex_abstract_from_index(
            item.get("abstract_inverted_index") or {}
          )
          if not abstract:
            continue

          references.append(
            {
              "title": _collapse_whitespace(item.get("display_name", "Untitled")),
              "url": item.get("id", ""),
              "text": abstract,
              "source": "OpenAlex",
            }
          )

        if not results:
          break
    except requests.RequestException as exc:
      issues.append(f"OpenAlex unavailable ({exc.__class__.__name__})")

  # Semantic Scholar often returns abstracts for broad topics and has free unauthenticated access.
  if len(references) < max_results:
    _emit_progress(progress_callback, "Collecting sources: Semantic Scholar")
    try:
      ss_limit = min(34, max(14, max_results + 8))
      ss_response = requests.get(
        "https://api.semanticscholar.org/graph/v1/paper/search",
        params={
          "query": primary_query,
          "limit": ss_limit,
          "fields": "title,abstract,url,externalIds",
        },
        timeout=timeout,
        headers={"User-Agent": "AuthentiText/1.0"},
      )
      ss_response.raise_for_status()
      papers = ss_response.json().get("data", [])

      for paper in papers:
        abstract = _collapse_whitespace(paper.get("abstract", ""))
        if len(abstract) < 80:
          continue

        title = _collapse_whitespace(paper.get("title", "Untitled"))
        url = _collapse_whitespace(paper.get("url", ""))
        if not url:
          doi = (paper.get("externalIds") or {}).get("DOI")
          if doi:
            url = f"https://doi.org/{doi}"

        references.append(
          {
            "title": title,
            "url": url,
            "text": abstract,
            "source": "Semantic Scholar",
          }
        )
    except requests.RequestException as exc:
      issues.append(f"Semantic Scholar unavailable ({exc.__class__.__name__})")

  _emit_progress(progress_callback, "Collecting sources: Web")
  serpapi_api_key = (
    os.getenv("SERPAPI_API_KEY", "").strip()
    or os.getenv("SERPAPI_KEY", "").strip()
  )

  web_results, web_issues, web_source_name = _fetch_web_sources_for_queries(
    queries=web_queries,
    timeout=timeout,
    serpapi_api_key=serpapi_api_key,
    max_results=3,
    progress_callback=progress_callback,
  )
  web_results = _deduplicate_reference_items(web_results, max_results=3)

  if web_results:
    web_results, expanded_pages = _expand_web_references_with_page_text(
      web_results,
      timeout=timeout,
      progress_callback=progress_callback,
    )
  else:
    expanded_pages = 0

  references.extend(web_results)
  issues.extend(web_issues)

  if not serpapi_api_key:
    hints.append(
      "SerpApi fallback disabled. Set SERPAPI_API_KEY in .env to enable it."
    )

  _emit_progress(progress_callback, "Cleaning up collected sources")
  deduplicated = _deduplicate_reference_items(references)

  # Keep web references compact in the listing while preserving academic docs.
  web_sources = {"DuckDuckGo", "Grokipedia", "Wikipedia", "SerpApi"}
  web_refs = [ref for ref in deduplicated if ref.get("source") in web_sources]
  academic_refs = [ref for ref in deduplicated if ref.get("source") not in web_sources]
  web_refs = web_refs[:3]

  academic_limit = max(0, max_results - len(web_refs))
  blended = academic_refs[:academic_limit] + web_refs
  if len(blended) < max_results:
    for ref in deduplicated:
      if ref in blended:
        continue
      blended.append(ref)
      if len(blended) >= max_results:
        break

  blended = blended[:max_results]

  if blended:
    query_note = f"Ran {len(web_queries)} paragraph-based web query variants."
    if web_source_name:
      query_note += f" First successful web provider: {web_source_name}."
    message = f"Loaded {len(blended)} references from academic and web sources. {query_note}"
    if web_refs:
      message += f" Web references shown: {len(web_refs)} (max 3)."
    if expanded_pages > 0:
      message += f" Expanded {expanded_pages} web pages for deeper comparison."
    if issues:
      message += " Some sources were unavailable, but fallback succeeded."
    if hints:
      message += " " + " ".join(hints)
    return blended, message

  if issues:
    base_message = " ".join(hints).strip()
    return (
      [],
      "Unable to load API references right now. "
      "Check your internet and try again, or use local files. "
      + "; ".join(issues)
      + (f" {base_message}" if base_message else ""),
    )

  return [], "No usable sources found. Try a more specific topic or URL, then fetch again."


def _offline_self_overlap_result(
  text: str,
  sentences: List[Dict[str, object]],
  note: str,
) -> Dict[str, object]:
  counts = Counter(sentence["norm"] for sentence in sentences)
  repeated_matches = []

  for sentence in sentences:
    repetitions = counts[sentence["norm"]]
    if repetitions <= 1:
      continue

    score = min(0.98, 0.70 + (repetitions - 1) * 0.08)
    repeated_matches.append(
      {
        "text_start": sentence["start"],
        "text_end": sentence["end"],
        "plagiarized_paragraph": sentence["raw"],
        "match_type": "Internal Repetition",
        "source": ("No external source loaded", ""),
        "score": round(score * 100, 1),
      }
    )

  coverage = [0] * len(text)
  for match in repeated_matches:
    for index in range(match["text_start"], min(match["text_end"], len(text))):
      coverage[index] = 1

  plag_percent = round((sum(coverage) / max(1, len(text))) * 100, 1)
  plagiarized_contents = {
    f"content{index}": value for index, value in enumerate(repeated_matches, start=1)
  }

  return {
    "plagiarized_contents": plagiarized_contents,
    "data": [plag_percent, round(max(0.0, 100 - plag_percent), 1)],
    "mode": "offline",
    "note": note,
  }


def analyze_text_against_references(
  text: str,
  references: Sequence[Dict[str, str]],
  min_score: float = 0.66,
  max_matches: int = 12,
  progress_callback: Optional[Callable[[str], None]] = None,
) -> Dict[str, object]:
  """
  Compare text to external references with hybrid scoring over words, sentences, and paragraphs.
  Returns data ready for UI presentation.
  """
  cleaned_text = (text or "").strip()
  if not cleaned_text:
    return {
      "plagiarized_contents": {},
      "data": [0.0, 100.0],
      "mode": "empty",
      "note": "No text provided.",
    }

  _emit_progress(progress_callback, "Extracting text spans")
  target_sentences = _sentence_spans(cleaned_text, min_words=3)
  target_paragraphs = _paragraph_spans(cleaned_text, min_words=8)
  _assign_paragraph_ids(target_sentences, target_paragraphs)

  if not target_sentences:
    return {
      "plagiarized_contents": {},
      "data": [0.0, 100.0],
      "mode": "empty",
      "note": "No valid sentence spans found in the input text.",
    }

  if not references:
    return _offline_self_overlap_result(
      cleaned_text,
      target_sentences,
      note="No API references loaded. Result uses internal overlap only.",
    )

  _emit_progress(progress_callback, "Building source index")
  reference_sentences: List[Dict[str, object]] = []
  reference_paragraphs: List[Dict[str, object]] = []
  ngram_index = defaultdict(set)
  concept_index = defaultdict(set)
  total_references = len(references)
  ref_progress_step = max(1, total_references // 5)

  for ref_number, reference in enumerate(references, start=1):
    title = _collapse_whitespace(reference.get("title", "Reference"))
    url = _collapse_whitespace(reference.get("url", ""))
    source_name = _collapse_whitespace(reference.get("source", "API"))

    if ref_number == 1 or ref_number % ref_progress_step == 0 or ref_number == total_references:
      _emit_progress(
        progress_callback,
        f"Indexing source passages ({ref_number}/{total_references})",
      )

    reference_text = _repair_character_spaced_text(reference.get("text", ""))
    ref_paragraphs = _paragraph_spans(reference_text, min_words=8)
    ref_sentences = _sentence_spans(reference_text, min_words=3)
    _assign_paragraph_ids(ref_sentences, ref_paragraphs)

    paragraph_offset = len(reference_paragraphs)
    for paragraph in ref_paragraphs:
      paragraph["title"] = title
      paragraph["url"] = url
      paragraph["source_name"] = source_name
      reference_paragraphs.append(paragraph)

    for sentence in ref_sentences:
      sentence["title"] = title
      sentence["url"] = url
      sentence["source_name"] = source_name

      paragraph_id = int(sentence.get("paragraph_id", -1))
      if paragraph_id >= 0:
        sentence["paragraph_id"] = paragraph_offset + paragraph_id

      ref_index = len(reference_sentences)
      reference_sentences.append(sentence)

      for ngram in sentence["ngrams"]:
        ngram_index[ngram].add(ref_index)
      for concept in sentence["concept_set"]:
        concept_index[concept].add(ref_index)

  if not reference_sentences:
    return _offline_self_overlap_result(
      cleaned_text,
      target_sentences,
      note="Loaded references had no extractable sentence content.",
    )

  _emit_progress(progress_callback, "Checking against sources")
  all_matches: List[Dict[str, object]] = []
  total_sentences = len(target_sentences)
  sentence_progress_step = max(8, total_sentences // 10)

  for sentence_index, sentence in enumerate(target_sentences, start=1):
    if (
      sentence_index == 1
      or sentence_index % sentence_progress_step == 0
      or sentence_index == total_sentences
    ):
      _emit_progress(
        progress_callback,
        f"Checking against sources ({sentence_index}/{total_sentences})",
      )

    candidate_counter = Counter()
    for ngram in sentence["ngrams"]:
      for ref_idx in ngram_index.get(ngram, set()):
        candidate_counter[ref_idx] += 3

    for concept in sentence["concept_set"]:
      for ref_idx in concept_index.get(concept, set()):
        candidate_counter[ref_idx] += 1

    if not candidate_counter:
      continue

    # Filter to candidates with sufficient overlap (performance optimization)
    # At least 2 n-gram or 4 concept hits to be worth detailed evaluation
    viable_candidates = [(idx, hits) for idx, hits in candidate_counter.most_common(35) if hits >= 2]
    
    if not viable_candidates:
      continue

    best_score = 0.0
    best_reference: Optional[Dict[str, object]] = None
    sentence_parts: Dict[str, float] = {}
    paragraph_parts: Dict[str, float] = {}

    target_paragraph: Optional[Dict[str, object]] = None
    target_paragraph_id = int(sentence.get("paragraph_id", -1))
    if 0 <= target_paragraph_id < len(target_paragraphs):
      target_paragraph = target_paragraphs[target_paragraph_id]

    # Evaluate likely candidate sentences; include paragraph-context scoring.
    # Reduced from 80 to 35 candidates for better performance.
    for ref_idx, _hits in viable_candidates:
      ref_sentence = reference_sentences[ref_idx]
      sent_score, sent_parts = _span_similarity(sentence, ref_sentence)
      
      # Early exit if sentence score is too low to matter
      if sent_score < (min_score * 0.8):
        continue

      paragraph_score = 0.0
      current_paragraph_parts: Dict[str, float] = {}
      ref_paragraph_id = int(ref_sentence.get("paragraph_id", -1))
      if target_paragraph is not None and 0 <= ref_paragraph_id < len(reference_paragraphs):
        ref_paragraph = reference_paragraphs[ref_paragraph_id]
        paragraph_score, current_paragraph_parts = _span_similarity(target_paragraph, ref_paragraph)

      # Combine sentence-level alignment with paragraph-level context consistency.
      score = (0.72 * sent_score) + (0.28 * paragraph_score)

      if score > best_score:
        best_score = score
        best_reference = ref_sentence
        sentence_parts = sent_parts
        paragraph_parts = current_paragraph_parts

    if best_reference and best_score >= min_score:
      source_title = f"{best_reference['title']} ({best_reference['source_name']})"
      all_matches.append(
        {
          "plagiarized_paragraph": sentence["raw"],
          "match_type": _classify_match(best_score),
          "source": (source_title, best_reference.get("url", "")),
          "text_start": sentence["start"],
          "text_end": sentence["end"],
          "score": round(best_score * 100, 1),
          "sentence_cosine": round(sentence_parts.get("token_cos", 0.0) * 100, 1),
          "sentence_sequence": round(sentence_parts.get("sequence", 0.0) * 100, 1),
          "paragraph_context": round(paragraph_parts.get("token_cos", 0.0) * 100, 1),
        }
      )

  all_matches.sort(key=lambda value: value["score"], reverse=True)

  if max_matches <= 0:
    display_matches = all_matches
  else:
    display_matches = all_matches[:max_matches]

  _emit_progress(progress_callback, "Cleaning up results")
  # Coverage uses all detected matches, not only the displayed top-N list.
  coverage = [0] * len(cleaned_text)
  for match in all_matches:
    for index in range(match["text_start"], min(match["text_end"], len(cleaned_text))):
      coverage[index] = 1

  plag_percent = round((sum(coverage) / max(1, len(cleaned_text))) * 100, 1)
  original_percent = round(max(0.0, 100 - plag_percent), 1)

  plagiarized_contents = {
    f"content{index}": value for index, value in enumerate(display_matches, start=1)
  }

  note = (
    "Compared against external references with hybrid word/sentence/paragraph scoring. "
    f"Detected {len(all_matches)} matching spans."
  )
  if max_matches > 0 and len(all_matches) > len(display_matches):
    note += f" Showing top {len(display_matches)} in the UI."

  return {
    "plagiarized_contents": plagiarized_contents,
    "data": [plag_percent, original_percent],
    "mode": "external",
    "note": note,
  }


def check_against_reference_text(ref_file: str, text_file: str) -> float:
  """Compatibility wrapper for local file-to-file comparison."""
  ref_text = process_file(ref_file)
  text = process_file(text_file)

  return compare_texts(ref_text, text)


def compare_texts(
  reference_text: str,
  text: str,
  progress_callback: Optional[Callable[[str], None]] = None,
) -> float:
  """Compare already loaded text strings and return plagiarism percent."""
  ref_text = _repair_character_spaced_text(reference_text or "").strip()
  cleaned_text = _repair_character_spaced_text(text or "").strip()

  if not ref_text or not cleaned_text:
    return 0.0

  result = analyze_text_against_references(
    text=cleaned_text,
    references=[
      {
        "title": "Reference File",
        "url": "",
        "text": ref_text,
        "source": "Local File",
      }
    ],
    min_score=0.60,
    max_matches=0,
    progress_callback=progress_callback,
  )
  return float(result.get("data", [0.0, 100.0])[0])


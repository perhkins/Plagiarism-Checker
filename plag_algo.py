import re
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from html import unescape
from typing import Dict, List, Optional, Sequence, Tuple

import requests
from PyPDF2 import PdfReader
from docx import Document


WORD_RE = re.compile(r"[A-Za-z0-9']+")
SENTENCE_RE = re.compile(r"[^.!?\n]+[.!?]?")
TAG_RE = re.compile(r"<[^>]+>")


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

  return content.strip()


def _collapse_whitespace(text: str) -> str:
  return re.sub(r"\s+", " ", text or "").strip()


def _normalize_text(text: str) -> str:
  return " ".join(WORD_RE.findall((text or "").lower()))


def _tokenize(text: str) -> List[str]:
  normalized = _normalize_text(text)
  return normalized.split() if normalized else []


def _make_ngrams(tokens: Sequence[str], n: int = 3) -> set:
  if not tokens:
    return set()
  if len(tokens) < n:
    return {" ".join(tokens)}
  return {" ".join(tokens[i : i + n]) for i in range(len(tokens) - n + 1)}


def _sentence_spans(text: str, min_words: int = 4) -> List[Dict[str, object]]:
  sentences: List[Dict[str, object]] = []
  for match in SENTENCE_RE.finditer(text or ""):
    raw = _collapse_whitespace(match.group())
    if not raw:
      continue

    tokens = _tokenize(raw)
    if len(tokens) < min_words:
      continue

    sentences.append(
      {
        "raw": raw,
        "norm": " ".join(tokens),
        "tokens": tokens,
        "ngrams": _make_ngrams(tokens, n=3),
        "start": match.start(),
        "end": match.end(),
      }
    )
  return sentences


def _classify_match(score: float) -> str:
  if score >= 0.93:
    return "Exact Match"
  if score >= 0.85:
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


def fetch_reference_texts(
  query: str,
  max_results: int = 8,
  timeout: int = 8,
) -> Tuple[List[Dict[str, str]], str]:
  """
  Fetch free reference abstracts from Crossref and OpenAlex.
  Returns a list of references and a status message.
  """
  cleaned_query = _collapse_whitespace(query)
  if not cleaned_query:
    return [], "Enter a topic, DOI, title, or URL first."

  references: List[Dict[str, str]] = []
  issues: List[str] = []

  try:
    crossref_response = requests.get(
      "https://api.crossref.org/works",
      params={"query.bibliographic": cleaned_query, "rows": max_results},
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
  if len(references) < max(2, max_results // 3):
    try:
      openalex_response = requests.get(
        "https://api.openalex.org/works",
        params={"search": cleaned_query, "per-page": max_results},
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
    except requests.RequestException as exc:
      issues.append(f"OpenAlex unavailable ({exc.__class__.__name__})")

  deduplicated: List[Dict[str, str]] = []
  seen = set()
  for reference in references:
    key = (
      reference["title"].lower(),
      reference["text"][:240].lower(),
    )
    if key in seen:
      continue
    seen.add(key)
    deduplicated.append(reference)

  if deduplicated:
    message = f"Loaded {len(deduplicated)} reference abstracts from free APIs."
    if issues:
      message += " Some sources were unavailable, but fallback succeeded."
    return deduplicated, message

  if issues:
    return (
      [],
      "Unable to load API references right now. "
      "Check your internet and try again, or use local files. "
      + "; ".join(issues),
    )

  return [], "No usable abstracts found. Try a more specific query or use local files."


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
  min_score: float = 0.82,
  max_matches: int = 12,
) -> Dict[str, object]:
  """
  Compare text to external references using n-gram candidate search + similarity scoring.
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

  target_sentences = _sentence_spans(cleaned_text, min_words=4)
  if not references:
    return _offline_self_overlap_result(
      cleaned_text,
      target_sentences,
      note="No API references loaded. Result uses internal overlap only.",
    )

  reference_sentences: List[Dict[str, object]] = []
  ngram_index = defaultdict(set)

  for reference in references:
    title = _collapse_whitespace(reference.get("title", "Reference"))
    url = _collapse_whitespace(reference.get("url", ""))
    source_name = _collapse_whitespace(reference.get("source", "API"))

    for sentence in _sentence_spans(reference.get("text", ""), min_words=4):
      sentence["title"] = title
      sentence["url"] = url
      sentence["source_name"] = source_name

      ref_index = len(reference_sentences)
      reference_sentences.append(sentence)

      for ngram in sentence["ngrams"]:
        ngram_index[ngram].add(ref_index)

  if not reference_sentences:
    return _offline_self_overlap_result(
      cleaned_text,
      target_sentences,
      note="Loaded references had no extractable sentence content.",
    )

  matches: List[Dict[str, object]] = []
  for sentence in target_sentences:
    candidate_counter = Counter()
    for ngram in sentence["ngrams"]:
      for ref_idx in ngram_index.get(ngram, set()):
        candidate_counter[ref_idx] += 1

    if not candidate_counter:
      continue

    sentence_token_set = set(sentence["tokens"])
    best_score = 0.0
    best_reference: Optional[Dict[str, object]] = None

    # Evaluate only the most likely candidate sentences to keep it fast.
    for ref_idx, _hits in candidate_counter.most_common(40):
      ref_sentence = reference_sentences[ref_idx]
      ref_token_set = set(ref_sentence["tokens"])
      union = sentence_token_set | ref_token_set
      jaccard = len(sentence_token_set & ref_token_set) / len(union) if union else 0.0
      ratio = SequenceMatcher(None, sentence["norm"], ref_sentence["norm"]).ratio()
      score = (0.60 * ratio) + (0.40 * jaccard)

      if score > best_score:
        best_score = score
        best_reference = ref_sentence

    if best_reference and best_score >= min_score:
      source_title = f"{best_reference['title']} ({best_reference['source_name']})"
      matches.append(
        {
          "plagiarized_paragraph": sentence["raw"],
          "match_type": _classify_match(best_score),
          "source": (source_title, best_reference.get("url", "")),
          "text_start": sentence["start"],
          "text_end": sentence["end"],
          "score": round(best_score * 100, 1),
        }
      )

  matches.sort(key=lambda value: value["score"], reverse=True)
  matches = matches[:max_matches]

  coverage = [0] * len(cleaned_text)
  for match in matches:
    for index in range(match["text_start"], min(match["text_end"], len(cleaned_text))):
      coverage[index] = 1

  plag_percent = round((sum(coverage) / max(1, len(cleaned_text))) * 100, 1)
  original_percent = round(max(0.0, 100 - plag_percent), 1)

  plagiarized_contents = {
    f"content{index}": value for index, value in enumerate(matches, start=1)
  }

  return {
    "plagiarized_contents": plagiarized_contents,
    "data": [plag_percent, original_percent],
    "mode": "external",
    "note": "Compared against external references.",
  }


def check_against_reference_text(ref_file: str, text_file: str) -> float:
  """Compatibility wrapper for local file-to-file comparison."""
  ref_text = process_file(ref_file)
  text = process_file(text_file)

  if not ref_text or not text:
    return 0.0

  result = analyze_text_against_references(
    text=text,
    references=[
      {
        "title": "Reference File",
        "url": "",
        "text": ref_text,
        "source": "Local File",
      }
    ],
    min_score=0.78,
    max_matches=20,
  )
  return float(result.get("data", [0.0, 100.0])[0])


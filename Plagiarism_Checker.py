import os
import re
import webbrowser
from collections import Counter, defaultdict
from pathlib import Path
import requests
from tkinter import (
    BOTH,
    END,
    LEFT,
    RIGHT,
    X,
    Y,
    Button,
    Canvas,
    Entry,
    Frame,
    Label,
    Scrollbar,
    Text,
    Tk,
    filedialog,
    messagebox,
)
from tkinter import ttk

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*_args, **_kwargs):
        return False


try:
    from openai import OpenAI
except ImportError:
    OpenAI = None


try:
    from PIL import Image, ImageTk
except ImportError:
    Image = None
    ImageTk = None


from plag_algo import (
    analyze_text_against_references,
    compare_texts,
    fetch_reference_from_url,
    fetch_reference_texts,
    fetch_web_reference_texts,
    process_file,
)


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
TOGETHER_API_KEY = os.getenv("TOGETHER_API_KEY", "").strip()
client = OpenAI(api_key=OPENAI_API_KEY) if OpenAI and OPENAI_API_KEY else None


full_text = ""
rewritten_text = ""
api_references = []
reference_cache_mode = "none"
reference_cache_query = ""
reference_cache_source_key = ""
plagiarized_data = {
    "plagiarized_contents": {},
    "data": [0.0, 100.0],
    "mode": "empty",
    "note": "",
}
plagiarized_contents = plagiarized_data["plagiarized_contents"]
data = plagiarized_data["data"]
image_cache = []
ref_file = ""
text_file = ""
reference_status_label = None


BG_MAIN = "#0f172a"
BG_SURFACE = "#111827"
BG_HEADER = "#1e293b"
TEXT_MAIN = "#f8fafc"
TEXT_MUTED = "#cbd5e1"
ACCENT = "#38bdf8"
SUCCESS = "#22c55e"
WARNING = "#f59e0b"
DANGER = "#ef4444"
BUTTON_PRIMARY = "#2563eb"
BUTTON_SUCCESS = "#15803d"
BUTTON_DANGER = "#b91c1c"
REFERENCE_FETCH_LIMIT = 24
DETAILS_COLLAPSED_PREVIEW = 170
EDUREPLICA_LONG_DOC_THRESHOLD = 5500
EDUREPLICA_CHUNK_WORDS = 260
EDUREPLICA_CHUNK_STRIDE = 190
EDUREPLICA_MAX_CHUNKS = 180
EDUREPLICA_CHUNK_SELECTION_LIMIT = 14
EDUREPLICA_MAX_WORDS_PER_SIDE = 5200

EDUREPLICA_STOPWORDS = {
    "a", "about", "after", "all", "also", "an", "and", "any", "are", "as", "at",
    "be", "been", "but", "by", "can", "do", "for", "from", "had", "has", "have",
    "if", "in", "into", "is", "it", "its", "more", "most", "no", "not", "of", "on",
    "or", "our", "out", "over", "so", "such", "than", "that", "the", "their", "them",
    "then", "there", "these", "they", "this", "to", "too", "under", "up", "was", "we",
    "were", "what", "when", "which", "while", "with", "you", "your",
}


loading_message = ""
loading_phase = 0
loading_after_id = None
loading_frame = None
loading_anchor_widget = None
loading_widgets = {}
progress_label = None
progress_bar = None


def set_loading_anchor(widget):
    global loading_anchor_widget
    loading_anchor_widget = widget


def set_loading_context(context):
    global loading_frame, progress_label, progress_bar, loading_anchor_widget

    if context not in loading_widgets:
        context = "review"

    widgets = loading_widgets.get(context)
    if not widgets:
        return

    loading_frame = widgets["frame"]
    progress_label = widgets["label"]
    progress_bar = widgets["bar"]
    loading_anchor_widget = None


def set_feedback(message, color=TEXT_MUTED):
    feedback_label.config(text=message, fg=color)


def _animate_loading_label():
    global loading_after_id, loading_phase

    if progress_label is None:
        return

    dots = "." * ((loading_phase % 3) + 1)
    progress_label.config(text=f"{loading_message}{dots}")
    loading_phase += 1
    loading_after_id = progress_label.after(280, _animate_loading_label)


def set_loading_phase(message):
    global loading_message, loading_phase

    if loading_frame is None or progress_label is None or progress_bar is None:
        return

    loading_message = message
    loading_phase = 0

    if loading_frame is not None and not loading_frame.winfo_manager():
        anchor_widget = loading_anchor_widget
        if anchor_widget is not None:
            try:
                if (not anchor_widget.winfo_exists()) or (anchor_widget.master != loading_frame.master):
                    anchor_widget = None
            except Exception:
                anchor_widget = None

        if anchor_widget is None and "plag_check_button" in globals():
            try:
                if plag_check_button.winfo_exists() and plag_check_button.master == loading_frame.master:
                    anchor_widget = plag_check_button
            except Exception:
                anchor_widget = None

        if anchor_widget is not None:
            loading_frame.pack(after=anchor_widget, fill=X, padx=30, pady=(0, 8))
        else:
            loading_frame.pack(fill=X, padx=30, pady=(0, 8))
    if not progress_label.winfo_manager():
        progress_label.pack(anchor="w")
    if not progress_bar.winfo_manager():
        progress_bar.pack(fill=X, pady=(4, 0))

    progress_label.config(text=loading_message)
    try:
        root.update()
    except Exception:
        pass


def start_loading(message):
    global loading_after_id

    if loading_frame is None or progress_label is None or progress_bar is None:
        return

    set_loading_phase(message)

    progress_bar.config(mode="indeterminate")
    progress_bar.start(12)
    if loading_after_id is None:
        _animate_loading_label()

    # Ensure the loading state is visible before blocking work starts.
    root.update()


def stop_loading():
    global loading_after_id

    if loading_frame is None or progress_label is None or progress_bar is None:
        return

    if loading_after_id:
        try:
            progress_label.after_cancel(loading_after_id)
        except Exception:
            pass
        loading_after_id = None

    progress_bar.stop()
    progress_bar.config(mode="determinate", value=0)
    progress_label.pack_forget()
    progress_bar.pack_forget()
    if loading_frame is not None:
        loading_frame.pack_forget()

    try:
        root.update_idletasks()
    except Exception:
        pass


def reset_review_output(clear_message=""):
    global plagiarized_data, plagiarized_contents, data

    plagiarized_data = {
        "plagiarized_contents": {},
        "data": [0.0, 100.0],
        "mode": "empty",
        "note": "",
    }
    plagiarized_contents = plagiarized_data["plagiarized_contents"]
    data = plagiarized_data["data"]

    for widget in report_frame.winfo_children():
        widget.destroy()
    report_frame.pack_forget()

    if clear_message:
        set_feedback(clear_message, TEXT_MUTED)


def safe_load_image(file_name, size):
    if Image is None or ImageTk is None:
        return None

    image_path = BASE_DIR / file_name
    if not image_path.exists():
        return None

    try:
        image = Image.open(image_path)
        image = image.resize(size)
        photo = ImageTk.PhotoImage(image)
        image_cache.append(photo)
        return photo
    except Exception:
        return None


def render_donut_chart(parent, values):
    plagiarized = max(0.0, min(100.0, float(values[0] if values else 0.0)))
    original = max(0.0, 100.0 - plagiarized)

    chart_canvas = Canvas(parent, width=340, height=220, bg=BG_SURFACE, highlightthickness=0)
    chart_canvas.pack(pady=10, padx=20, anchor="nw")

    x0, y0, x1, y1 = 20, 10, 200, 190
    plag_extent = (plagiarized / 100.0) * 360.0
    original_extent = 360.0 - plag_extent

    if plagiarized > 0:
        chart_canvas.create_arc(
            x0,
            y0,
            x1,
            y1,
            start=90,
            extent=-plag_extent,
            fill=DANGER,
            outline="",
        )

    if original > 0:
        chart_canvas.create_arc(
            x0,
            y0,
            x1,
            y1,
            start=90 - plag_extent,
            extent=-original_extent,
            fill=SUCCESS,
            outline="",
        )

    chart_canvas.create_oval(70, 60, 150, 140, fill=BG_SURFACE, outline="")
    chart_canvas.create_text(
        110,
        100,
        text=f"{plagiarized:.1f}%",
        fill=TEXT_MAIN,
        font=("Helvetica", 16, "bold"),
    )

    chart_canvas.create_rectangle(230, 55, 246, 70, fill=DANGER, outline="")
    chart_canvas.create_text(
        254,
        62,
        text="Plagiarized",
        fill=TEXT_MAIN,
        font=("Arial", 10),
        anchor="w",
    )
    chart_canvas.create_rectangle(230, 90, 246, 105, fill=SUCCESS, outline="")
    chart_canvas.create_text(
        254,
        97,
        text="Original",
        fill=TEXT_MAIN,
        font=("Arial", 10),
        anchor="w",
    )

    return chart_canvas


def open_link(_event, url):
    if url:
        webbrowser.open(url)


def suggest_improvement(text):
    fallback = (
        "Add citation for the original idea, then rewrite the point using your own reasoning "
        "instead of preserving the same sentence pattern."
    )
    cleaned = (text or "").strip()
    if not cleaned:
        return fallback

    if not client:
        return fallback

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a plagiarism expert. Suggest one concise improvement to reduce "
                        "plagiarism risk in the provided text. Do not rewrite the text."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Suggest one way to improve this paragraph:\n\n{cleaned}",
                },
            ],
        )
        suggestion = (response.choices[0].message.content or "").strip()
        return suggestion or fallback
    except Exception:
        return fallback


def _extract_chat_response_text(payload):
    choices = payload.get("choices") if isinstance(payload, dict) else []
    if not choices:
        return ""

    message = (choices[0] or {}).get("message") if isinstance(choices[0], dict) else {}
    content = message.get("content", "") if isinstance(message, dict) else ""

    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                text_part = (item.get("text") or "").strip()
                if text_part:
                    parts.append(text_part)
            elif isinstance(item, str):
                stripped = item.strip()
                if stripped:
                    parts.append(stripped)
        content = "\n".join(parts)

    return str(content or "").strip()


def _rewrite_with_provider_http(
    provider_name,
    endpoint,
    api_key,
    model,
    system_prompt,
    user_prompt,
    extra_headers=None,
    timeout=35,
):
    if not api_key:
        return "", f"{provider_name}: missing key"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if extra_headers:
        headers.update(extra_headers)

    payload = {
        "model": model,
        "temperature": 0.35,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }

    try:
        response = requests.post(endpoint, json=payload, headers=headers, timeout=timeout)
        response.raise_for_status()
        data = response.json()
    except Exception as exc:
        return "", f"{provider_name}: {exc.__class__.__name__}"

    rewritten = _extract_chat_response_text(data)
    if rewritten:
        return rewritten, ""

    return "", f"{provider_name}: empty response"


def _local_tone_rewrite(text, tone):
    cleaned = re.sub(r"\s+", " ", (text or "")).strip()
    if not cleaned:
        return ""

    phrase_swaps = [
        (r"\bin order to\b", "to"),
        (r"\bdue to the fact that\b", "because"),
        (r"\bin the event that\b", "if"),
        (r"\ba large number of\b", "many"),
        (r"\bhas the ability to\b", "can"),
        (r"\bit is important to note that\b", "notably,"),
        (r"\butilize\b", "use"),
        (r"\bdemonstrate\b", "show"),
        (r"\bfurthermore\b", "in addition"),
        (r"\btherefore\b", "as a result"),
    ]

    sentences = [
        segment.strip()
        for segment in re.split(r"(?<=[.!?])\s+", cleaned)
        if segment.strip()
    ]
    if not sentences:
        sentences = [cleaned]

    rewritten_sentences = []
    for sentence in sentences:
        updated = sentence
        for pattern, replacement in phrase_swaps:
            updated = re.sub(pattern, replacement, updated, flags=re.IGNORECASE)
        rewritten_sentences.append(updated)

    tone_prefixes = {
        "professional": "From a professional standpoint",
        "creative": "Seen through a more imaginative lens",
        "formal": "From a formal perspective",
        "casual": "Put simply",
    }
    prefix = tone_prefixes.get((tone or "").strip().lower(), "In clearer terms")

    first_sentence = rewritten_sentences[0].strip()
    if first_sentence:
        lowered = first_sentence[0].lower() + first_sentence[1:] if len(first_sentence) > 1 else first_sentence.lower()
        rewritten_sentences[0] = f"{prefix}, {lowered}"

    return " ".join(rewritten_sentences).strip()


def rewrite_with_tone(text, tone):
    cleaned = (text or "").strip()
    if not cleaned:
        return "Kindly enter text to rewrite."

    selected_tone = (tone or "professional").strip()
    system_prompt = (
        "You are a professional rewriter. Keep factual meaning, improve originality, "
        "and rewrite clearly in the requested tone."
    )
    user_prompt = f"Rewrite this text in a {selected_tone} tone:\n\n{cleaned}"

    provider_errors = []

    if client:
        try:
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
            rewritten = (response.choices[0].message.content or "").strip()
            if rewritten:
                return rewritten
        except Exception as exc:
            provider_errors.append(f"OpenAI: {exc.__class__.__name__}")

    provider_chain = [
        (
            "OpenRouter",
            "https://openrouter.ai/api/v1/chat/completions",
            OPENROUTER_API_KEY,
            os.getenv("OPENROUTER_MODEL", "meta-llama/llama-3.1-8b-instruct:free").strip() or "meta-llama/llama-3.1-8b-instruct:free",
            {
                "HTTP-Referer": "https://papercritic.local",
                "X-Title": "PaperCritic",
            },
        ),
        (
            "Groq",
            "https://api.groq.com/openai/v1/chat/completions",
            GROQ_API_KEY,
            os.getenv("GROQ_MODEL", "llama-3.1-8b-instant").strip() or "llama-3.1-8b-instant",
            None,
        ),
        (
            "Together",
            "https://api.together.xyz/v1/chat/completions",
            TOGETHER_API_KEY,
            os.getenv("TOGETHER_MODEL", "meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo").strip() or "meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo",
            None,
        ),
    ]

    for name, endpoint, api_key, model, headers in provider_chain:
        if not api_key:
            continue
        rewritten, error_message = _rewrite_with_provider_http(
            provider_name=name,
            endpoint=endpoint,
            api_key=api_key,
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            extra_headers=headers,
        )
        if rewritten:
            return rewritten
        if error_message:
            provider_errors.append(error_message)

    local_rewrite = _local_tone_rewrite(cleaned, selected_tone)
    if local_rewrite:
        return local_rewrite

    if provider_errors:
        return "Rewrite failed across configured providers. " + "; ".join(provider_errors[:3])

    return cleaned


def file_upload():
    set_loading_context("review")
    set_loading_anchor(file_upload_button)

    file_path = filedialog.askopenfilename(
        title="Select a file",
        filetypes=[("Supported Files", "*.txt *.docx *.pdf"), ("All Files", "*.*")],
    )
    if not file_path:
        return ""

    start_loading("Uploading file")

    content = process_file(file_path)

    stop_loading()

    if not content:
        messagebox.showwarning("Error", "Unsupported file type or empty content.")
        return ""

    text_box.delete("1.0", END)
    text_box.insert(END, content)
    uploaded_file_label.config(text=f"Loaded file: {Path(file_path).name}", fg=ACCENT)
    reset_review_output("Loaded a new file. Previous plagiarism report was cleared.")
    return file_path


def build_source_cache_key(source_text):
    normalized = " ".join((source_text or "").lower().split())
    return normalized[:1200]


def resolve_reference_mode(source_text):
    raw_query = reference_query_entry.get().strip()
    if raw_query:
        if raw_query.lower().startswith(("http://", "https://")):
            return "url", raw_query, ""
        return "topic", raw_query, ""

    if source_text:
        return (
            "web",
            "",
            (
                "No topic/DOI entered. Running paragraph web search "
                "(DuckDuckGo -> Grokipedia -> Wikipedia -> SerpApi fallback)."
            ),
        )

    return "web", "", "Enter a topic, DOI, URL, or upload text before fetching references."


def fetch_references_for_mode(mode, query, source_text):
    if mode == "url":
        set_loading_phase("Collecting sources: URL import")
        reference, status = fetch_reference_from_url(query, timeout=8)
        return ([reference] if reference else []), status

    if mode == "topic":
        set_loading_phase("Collecting sources: reference docs + web")
        return fetch_reference_texts(
            query,
            max_results=REFERENCE_FETCH_LIMIT,
            timeout=8,
            source_text=source_text,
            progress_callback=set_loading_phase,
        )

    set_loading_phase("Collecting sources: web + SerpApi")
    return fetch_web_reference_texts(
        source_text=source_text,
        query=query,
        max_results=3,
        timeout=8,
        progress_callback=set_loading_phase,
    )


def import_references():
    global api_references, reference_cache_mode, reference_cache_query
    global reference_cache_source_key

    set_loading_context("review")
    set_loading_anchor(reference_btn_frame)

    source_text = text_box.get("1.0", END).strip()
    mode, query, mode_note = resolve_reference_mode(source_text)
    if mode == "web" and not source_text:
        messagebox.showwarning("Missing Input", mode_note)
        return

    start_loading("Collecting sources")

    try:
        api_references, status = fetch_references_for_mode(mode, query, source_text)
    finally:
        stop_loading()

    if mode_note and mode_note not in status:
        status = f"{mode_note} {status}".strip()

    if api_references:
        reference_cache_mode = mode
        reference_cache_query = query.strip().lower() if mode in {"topic", "url"} else ""
        reference_cache_source_key = build_source_cache_key(source_text) if mode == "web" else ""
    else:
        reference_cache_mode = "none"
        reference_cache_query = ""
        reference_cache_source_key = ""

    if api_references:
        set_feedback(status, SUCCESS)
        update_reference_status(status, SUCCESS)
    else:
        set_feedback(status, WARNING)
        update_reference_status(status, WARNING)


def update_reference_status(message="", color=TEXT_MUTED):
    if reference_status_label is None:
        return

    count = len(api_references)
    status_text = f"Loaded references: {count}"
    if message:
        status_text = f"{status_text} | {message}"
    reference_status_label.config(text=status_text, fg=color)


def preview_loaded_references():
    if not api_references:
        messagebox.showinfo(
            "No References",
            (
                "No references loaded yet.\n"
                "Use a topic/DOI/URL, or leave it blank to run web search + SerpApi."
            ),
        )
        return

    preview_lines = []
    for index, reference in enumerate(api_references[:18], start=1):
        title = (reference.get("title") or "Untitled Source").strip()
        source_name = (reference.get("source") or "API").strip()
        preview_lines.append(f"{index}. {title} [{source_name}]")

    if len(api_references) > 18:
        preview_lines.append(f"...and {len(api_references) - 18} more references.")

    messagebox.showinfo("Loaded References", "\n".join(preview_lines))


def clear_loaded_references():
    global api_references, reference_cache_mode, reference_cache_query
    global reference_cache_source_key

    api_references = []
    reference_cache_mode = "none"
    reference_cache_query = ""
    reference_cache_source_key = ""
    update_reference_status("Reference cache cleared.", WARNING)
    set_feedback(
        "Reference cache cleared. Fetch again, or run check with blank query for web mode.",
        WARNING,
    )


def quick_suggestion(match_type):
    if match_type == "Exact Match":
        return "Quote and cite this source directly, or rewrite this passage from scratch in your own voice."
    if match_type == "Near Match":
        return "Restructure sentence flow and add citation where the idea originates from the source."
    if match_type == "Paraphrased Overlap":
        return "Keep the idea but vary argument structure and vocabulary, then cite the original source."
    return "Review this sentence and add citation or deeper rewrite to reduce similarity risk."


def toggle_match_details(details_frame, toggle_button):
    if details_frame.winfo_manager():
        details_frame.pack_forget()
        toggle_button.config(text="Show Details")
    else:
        details_frame.pack(fill=X, padx=12, pady=(6, 10))
        toggle_button.config(text="Hide Details")


def request_review(text, search_text=""):
    global plagiarized_data, plagiarized_contents, data, api_references
    global reference_cache_mode, reference_cache_query, reference_cache_source_key

    source_for_search = (search_text or text or "").strip()
    mode, query, mode_note = resolve_reference_mode(source_for_search)

    references = list(api_references)
    status_message = ""

    current_query_key = query.strip().lower()
    current_source_key = build_source_cache_key(source_for_search)
    must_refresh = (
        mode == "url"
        or mode != reference_cache_mode
        or (mode in {"topic", "url"} and current_query_key != reference_cache_query)
        or (mode == "web" and current_source_key != reference_cache_source_key)
    )

    if must_refresh:
        references = []
        api_references = []
        reference_cache_mode = "none"
        reference_cache_query = ""
        reference_cache_source_key = ""

    if not references:
        if mode == "web" and not source_for_search:
            status_message = mode_note
        else:
            references, status_message = fetch_references_for_mode(mode, query, source_for_search)
            if references:
                api_references = references
                reference_cache_mode = mode
                reference_cache_query = (
                    current_query_key if mode in {"topic", "url"} else ""
                )
                reference_cache_source_key = (
                    current_source_key if mode == "web" else ""
                )

        if mode_note and mode_note not in status_message:
            status_message = f"{mode_note} {status_message}".strip()

    set_loading_phase("Checking against sources")
    plagiarized_data = analyze_text_against_references(
        text,
        references,
        progress_callback=set_loading_phase,
    )
    plagiarized_contents = plagiarized_data.get("plagiarized_contents", {})
    data = plagiarized_data.get("data", [0.0, 100.0])

    if references:
        message = status_message or plagiarized_data.get("note", "External comparison completed.")
        set_feedback(message, SUCCESS)
        update_reference_status(message, SUCCESS)
    else:
        note = plagiarized_data.get("note", "")
        if status_message and status_message not in note:
            note = f"{status_message} {note}".strip()
        if not note:
            note = status_message
        if not note:
            note = "No external references loaded."
        set_feedback(
            (
                f"{note} Use topic/DOI/URL input, or leave query blank for web search mode. "
                "You can also compare two local files in EduReplica."
            ),
            WARNING,
        )
        update_reference_status(note, WARNING)


def _build_analysis_text(full_text, max_words=2400):
    """Build analysis payload for long documents using distributed word segments."""
    words = (full_text or "").split()
    if len(words) <= max_words:
        return full_text, False

    segment_words = max(500, max_words // 3)
    starts = [
        0,
        max(0, (len(words) // 2) - (segment_words // 2)),
        max(0, len(words) - segment_words),
    ]

    sampled_parts = []
    seen = set()
    for start in starts:
        segment = words[start : start + segment_words]
        if not segment:
            continue
        segment_text = " ".join(segment)
        key = re.sub(r"\s+", " ", segment_text).strip().lower()
        if key and key not in seen:
            seen.add(key)
            sampled_parts.append(segment_text)

    sampled_text = "\n\n".join(sampled_parts).strip()
    return (sampled_text if sampled_text else " ".join(words[:max_words])), True


def _chunk_token_set(text):
    tokens = re.findall(r"[A-Za-z0-9']+", (text or "").lower())
    return {
        token
        for token in tokens
        if len(token) >= 4 and not token.isdigit() and token not in EDUREPLICA_STOPWORDS
    }


def _build_word_chunks(
    text,
    chunk_words=EDUREPLICA_CHUNK_WORDS,
    stride_words=EDUREPLICA_CHUNK_STRIDE,
    max_chunks=EDUREPLICA_MAX_CHUNKS,
):
    words = (text or "").split()
    if not words:
        return [], 0

    if len(words) <= chunk_words:
        merged = " ".join(words)
        return [
            {
                "index": 0,
                "start": 0,
                "end": len(words),
                "text": merged,
                "token_set": _chunk_token_set(merged),
            }
        ], len(words)

    chunks = []
    chunk_index = 0
    for start in range(0, len(words), max(1, stride_words)):
        end = min(len(words), start + chunk_words)
        if end <= start:
            continue

        segment_text = " ".join(words[start:end]).strip()
        if not segment_text:
            continue

        chunks.append(
            {
                "index": chunk_index,
                "start": start,
                "end": end,
                "text": segment_text,
                "token_set": _chunk_token_set(segment_text),
            }
        )
        chunk_index += 1

        if end >= len(words) or len(chunks) >= max_chunks:
            break

    return chunks, len(words)


def _anchor_chunk_indexes(total_chunks):
    if total_chunks <= 0:
        return set()

    anchors = {0, total_chunks // 2, total_chunks - 1}
    if total_chunks > 4:
        anchors.add(total_chunks // 4)
        anchors.add((total_chunks * 3) // 4)

    return {index for index in anchors if 0 <= index < total_chunks}


def _join_selected_chunks(chunks, selected_indexes, max_words):
    if not chunks or not selected_indexes or max_words <= 0:
        return "", 0

    assembled = []
    used_words = 0
    for index in sorted(selected_indexes):
        if not (0 <= index < len(chunks)):
            continue

        remaining = max_words - used_words
        if remaining <= 0:
            break

        words = chunks[index]["text"].split()
        if not words:
            continue

        section = " ".join(words[:remaining]).strip()
        if not section:
            continue

        assembled.append(section)
        used_words += len(section.split())

    return "\n\n".join(assembled).strip(), used_words


def _prepare_edureplica_comparison_texts(reference_text, research_text):
    ref_chunks, ref_word_count = _build_word_chunks(reference_text)
    research_chunks, research_word_count = _build_word_chunks(research_text)
    max_word_count = max(ref_word_count, research_word_count)

    base_meta = {
        "used_chunking": False,
        "reference_word_count": ref_word_count,
        "research_word_count": research_word_count,
        "reference_chunk_count": len(ref_chunks),
        "research_chunk_count": len(research_chunks),
        "reference_chunks_used": len(ref_chunks),
        "research_chunks_used": len(research_chunks),
    }

    if max_word_count < EDUREPLICA_LONG_DOC_THRESHOLD:
        return reference_text, research_text, base_meta

    if not ref_chunks or not research_chunks:
        return reference_text, research_text, base_meta

    reference_token_index = defaultdict(set)
    for ref_index, chunk in enumerate(ref_chunks):
        for token in chunk["token_set"]:
            reference_token_index[token].add(ref_index)

    ranked_pairs = []
    for research_index, research_chunk in enumerate(research_chunks):
        token_set = research_chunk["token_set"]
        if not token_set:
            continue

        candidate_hits = Counter()
        for token in token_set:
            for ref_index in reference_token_index.get(token, set()):
                candidate_hits[ref_index] += 1

        if not candidate_hits:
            continue

        best_ref_index, overlap_hits = candidate_hits.most_common(1)[0]
        ref_token_count = max(1, len(ref_chunks[best_ref_index]["token_set"]))
        overlap_score = overlap_hits / max(1.0, (len(token_set) * ref_token_count) ** 0.5)
        if overlap_score < 0.12:
            continue

        ranked_pairs.append((overlap_score, research_index, best_ref_index))

    ranked_pairs.sort(key=lambda item: item[0], reverse=True)

    selected_reference_indexes = set(_anchor_chunk_indexes(len(ref_chunks)))
    selected_research_indexes = set(_anchor_chunk_indexes(len(research_chunks)))

    for _score, research_index, ref_index in ranked_pairs:
        if len(selected_research_indexes) < EDUREPLICA_CHUNK_SELECTION_LIMIT:
            selected_research_indexes.add(research_index)
        if len(selected_reference_indexes) < EDUREPLICA_CHUNK_SELECTION_LIMIT:
            selected_reference_indexes.add(ref_index)

        if (
            len(selected_research_indexes) >= EDUREPLICA_CHUNK_SELECTION_LIMIT
            and len(selected_reference_indexes) >= EDUREPLICA_CHUNK_SELECTION_LIMIT
        ):
            break

    reduced_reference_text, used_ref_words = _join_selected_chunks(
        ref_chunks,
        selected_reference_indexes,
        max_words=EDUREPLICA_MAX_WORDS_PER_SIDE,
    )
    reduced_research_text, used_research_words = _join_selected_chunks(
        research_chunks,
        selected_research_indexes,
        max_words=EDUREPLICA_MAX_WORDS_PER_SIDE,
    )

    if not reduced_reference_text or not reduced_research_text:
        return reference_text, research_text, base_meta

    meta = {
        "used_chunking": True,
        "reference_word_count": ref_word_count,
        "research_word_count": research_word_count,
        "reference_chunk_count": len(ref_chunks),
        "research_chunk_count": len(research_chunks),
        "reference_chunks_used": len(selected_reference_indexes),
        "research_chunks_used": len(selected_research_indexes),
        "reference_words_used": used_ref_words,
        "research_words_used": used_research_words,
    }
    return reduced_reference_text, reduced_research_text, meta


def show_report():
    global full_text

    set_loading_context("review")
    set_loading_anchor(plag_check_button)

    full_text = text_box.get("1.0", END).strip()
    selected_text, sampled_long_text = _build_analysis_text(full_text, max_words=2400)

    if not full_text or full_text == "Kindly enter a text to check for plagiarism.":
        text_box.delete("1.0", END)
        text_box.insert(END, "Kindly enter a text to check for plagiarism.\n")
        return

    if sampled_long_text:
        set_feedback(
            "Large document detected. Analyzing distributed segments across the file for better accuracy.",
            TEXT_MUTED,
        )

    text_box.config(state="disabled")
    for widget in report_frame.winfo_children():
        widget.destroy()

    start_loading("Analyzing text")
    try:
        request_review(selected_text, search_text=full_text)
    finally:
        stop_loading()

    text_box.config(state="normal")
    report_frame.pack(fill=BOTH, expand=True)

    chart_label = Label(
        report_frame,
        text="Overview",
        fg=ACCENT,
        bg=BG_SURFACE,
        font=("Helvetica", 20, "bold"),
    )
    chart_label.pack(anchor="nw", padx=40, pady=10)

    render_donut_chart(report_frame, data)

    plag_percent = max(0.0, min(100.0, float(data[0] if data else 0.0)))
    appraisal = Label(report_frame, text="", bg=BG_SURFACE, fg=DANGER, font=("Helvetica", 14))

    if plag_percent == 0:
        chart_report = "Great work. No overlap detected in the current reference set."
        appraisal.config(text=chart_report, fg=SUCCESS)
    elif 0 < plag_percent <= 30:
        chart_report = "Low overlap. A few edits and citations should make this safer."
        appraisal.config(text=chart_report, fg=SUCCESS)
    elif 30 < plag_percent <= 50:
        chart_report = "Moderate overlap. Rewrite key passages and add proper citations."
        appraisal.config(text=chart_report, fg=WARNING)
    else:
        chart_report = "High overlap detected. Heavy rewrite and attribution are required."
        appraisal.config(text=chart_report, fg=DANGER)

    appraisal.pack(anchor="nw", padx=40, pady=10)

    line_divider = Frame(report_frame, bg=DANGER, height=5)
    line_divider.pack(fill=X, pady=10, expand=True)

    plagiarized_label = Label(
        report_frame,
        text="Plagiarized Content Review (Compact Cards)",
        bg=BG_SURFACE,
        fg=DANGER,
        font=("Helvetica", 14),
    )
    plagiarized_label.pack(anchor="nw", padx=40, pady=10)

    matches_container = Frame(report_frame, bg=BG_SURFACE)
    matches_container.pack(fill=X, padx=20, pady=(0, 12), expand=True)

    if not plagiarized_contents:
        no_overlap = Label(
            matches_container,
            text=(
                "No strong overlaps detected from loaded sources. "
                "Use topic/DOI/URL input, or leave query blank for web search mode."
            ),
            fg=TEXT_MUTED,
            bg=BG_SURFACE,
            font=("Arial", 11),
            wraplength=860,
            justify="left",
        )
        no_overlap.pack(anchor="w", padx=12, pady=(4, 8))
    else:
        for index, key in enumerate(plagiarized_contents, start=1):
            item = plagiarized_contents[key]
            paragraph = item.get("plagiarized_paragraph", "")
            match_type = item.get("match_type", "Potential Match")
            source_title, source_url = item.get("source", ("Unknown Source", ""))
            score = float(item.get("score", 0.0))

            card = Frame(
                matches_container,
                bg=BG_MAIN,
                highlightthickness=1,
                highlightbackground=BG_HEADER,
            )
            card.pack(fill=X, padx=8, pady=6)

            header_row = Frame(card, bg=BG_MAIN)
            header_row.pack(fill=X, padx=12, pady=(10, 4))

            snippet = paragraph
            if len(snippet) > DETAILS_COLLAPSED_PREVIEW:
                snippet = snippet[: DETAILS_COLLAPSED_PREVIEW - 3].rstrip() + "..."

            title_label = Label(
                header_row,
                text=f"{index}. {snippet}",
                fg=TEXT_MAIN,
                bg=BG_MAIN,
                font=("Arial", 11, "bold"),
                justify="left",
                anchor="w",
                wraplength=700,
            )
            title_label.pack(side=LEFT, fill=X, expand=True)

            toggle_button = Button(
                header_row,
                text="Show Details",
                bg=BUTTON_PRIMARY,
                fg=TEXT_MAIN,
                borderwidth=0,
                relief="flat",
                font=("Arial", 9, "bold"),
                padx=8,
                pady=4,
            )
            toggle_button.pack(side=RIGHT, padx=(12, 0))

            meta = Label(
                card,
                text=f"{match_type} | Score: {score:.1f}%",
                fg=ACCENT,
                bg=BG_MAIN,
                font=("Arial", 10, "bold"),
            )
            meta.pack(anchor="w", padx=12, pady=(0, 4))

            details_frame = Frame(card, bg=BG_MAIN)

            source_label = Label(
                details_frame,
                text=f"Source: {source_title}",
                fg=ACCENT,
                bg=BG_MAIN,
                font=("Arial", 10, "underline") if source_url else ("Arial", 10),
                cursor="hand2" if source_url else "arrow",
                wraplength=820,
                justify="left",
                anchor="w",
            )
            source_label.pack(anchor="w", pady=(0, 6))
            if source_url:
                source_label.bind("<Button-1>", lambda event, link=source_url: open_link(event, link))

            full_text_label = Label(
                details_frame,
                text=f"Passage: {paragraph}",
                fg=TEXT_MUTED,
                bg=BG_MAIN,
                font=("Arial", 10),
                wraplength=820,
                justify="left",
                anchor="w",
            )
            full_text_label.pack(anchor="w", pady=(0, 6))

            # Keep feedback fast by using AI suggestions only on top matches.
            if client and index <= 2:
                suggestion = suggest_improvement(paragraph)
            else:
                suggestion = quick_suggestion(match_type)

            suggestion_label = Label(
                details_frame,
                text=f"Suggestion: {suggestion}",
                fg=SUCCESS,
                bg=BG_MAIN,
                font=("Arial", 10),
                wraplength=820,
                justify="left",
                anchor="w",
            )
            suggestion_label.pack(anchor="w")

            toggle_button.config(
                command=lambda frame=details_frame, btn=toggle_button: toggle_match_details(frame, btn)
            )

            if index <= 2:
                details_frame.pack(fill=X, padx=12, pady=(6, 10))
                toggle_button.config(text="Hide Details")

    tones = ["Professional", "Creative", "Formal", "Innovative"]
    tone_label = Label(
        report_frame,
        text="Would you like a rewrite? Select Writing Tone",
        font=("Helvetica", 18),
        fg=ACCENT,
        bg=BG_SURFACE,
    )
    tone_label.pack(anchor="nw", padx=40, pady=5, expand=True)

    report_tone_dropdown = ttk.Combobox(report_frame, values=tones, state="readonly")
    report_tone_dropdown.set("Select a Tone")
    report_tone_dropdown.pack(anchor="nw", padx=50, pady=5, expand=True)

    rewrite_btn1 = Button(
        report_frame,
        text="Rewrite",
        bg=BUTTON_PRIMARY,
        fg=TEXT_MAIN,
        borderwidth=1,
        relief="flat",
        font=("Arial", 14),
        command=lambda: redirect_to_rewrite(
            list(plagiarized_contents.keys()),
            report_tone_dropdown.get(),
        ),
    )
    rewrite_btn1.pack(anchor="nw", padx=50, pady=20, expand=True)


def redirect_to_rewrite(keys, tone):
    show_page(rewrite_page)
    text_box2.delete("1.0", END)

    selected_passages = []
    for key in keys:
        content = plagiarized_contents.get(key, {})
        passage = content.get("plagiarized_paragraph", "").strip()
        if passage:
            selected_passages.append(passage)

    if selected_passages:
        text_box2.insert(END, "\n\n".join(selected_passages))
    else:
        text_box2.insert(END, full_text)

    if tone and tone != "Select a Tone":
        rewrite_dropdown.set(tone)
        rewrite_selected_label.config(text=f"{tone} Tone Selected")


def rewrite_func(text, tone):
    global rewritten_text

    check_text = (text or "").strip()
    if not check_text:
        text_box2.delete("1.0", END)
        text_box2.insert(END, "Kindly enter text to rewrite.\n")
        return

    selected_tone = tone if tone and tone != "Select a Tone" else rewrite_dropdown.get()
    if selected_tone == "Select a Tone":
        rewrite_selected_label.config(text="No Tone Selected")
        return

    for widget in modified_text_frame.winfo_children():
        widget.destroy()

    set_loading_context("rewrite")
    set_loading_anchor(rewrite_btn)
    start_loading("Rewriting text")
    try:
        set_loading_phase("Generating rewritten draft")
        rewritten_text = rewrite_with_tone(check_text, selected_tone)
    finally:
        stop_loading()

    modified_text_frame.pack(fill=BOTH, expand=True)
    line_divider = Frame(modified_text_frame, bg=SUCCESS, height=5)
    line_divider.pack(anchor="nw", fill=X, pady=10, expand=True)

    modifier_label = Label(
        modified_text_frame,
        text=f"Rewritten Text ({selected_tone} Tone)",
        bg=BG_SURFACE,
        fg=DANGER,
        font=("Helvetica", 14),
    )
    modifier_label.pack(anchor="nw", padx=40, pady=10)

    modified_text_box = Text(
        modified_text_frame,
        fg=TEXT_MAIN,
        bg=BG_SURFACE,
        font=("Helvetica", 13),
        height=20,
        highlightthickness=2,
        highlightbackground=DANGER,
        highlightcolor=ACCENT,
        relief="flat",
    )
    modified_text_box.pack(padx=20, pady=10)

    notification = Label(
        modified_text_frame,
        text=(
            "You can rewrite again if needed. Shortcuts: Ctrl+A to select all, "
            "Ctrl+C to copy."
        ),
        font=("Helvetica", 14),
        fg=ACCENT,
        bg=BG_SURFACE,
    )
    notification.pack(padx=20, pady=10)

    modified_text_box.insert("1.0", rewritten_text)


def get_filepath(file_slot):
    global ref_file, text_file

    file_path = filedialog.askopenfilename(
        title="Select a file",
        filetypes=[("Supported Files", "*.txt *.docx *.pdf"), ("All Files", "*.*")],
    )
    if not file_path:
        return

    if file_slot == "ref_file":
        file_info1.delete(0, END)
        file_info1.insert(END, file_path)
        file_info1.config(fg=ACCENT)
        ref_file = file_path
    elif file_slot == "text_file":
        file_info2.delete(0, END)
        file_info2.insert(END, file_path)
        file_info2.config(fg=ACCENT)
        text_file = file_path


def check_research():
    set_loading_context("research")
    set_loading_anchor(cart_button)

    if not ref_file or not text_file:
        messagebox.showwarning("Missing Files", "Please add both reference and research files.")
        return

    start_loading("Extracting files")

    try:
        set_loading_phase("Extracting and cleaning reference file")
        reference_text = process_file(ref_file)

        set_loading_phase("Extracting and cleaning research file")
        research_text = process_file(text_file)
        if not reference_text or not research_text:
            messagebox.showwarning(
                "Unreadable File",
                "One or both selected files are empty or unsupported. Use .txt, .docx, or .pdf files.",
            )
            return

        set_loading_phase("Preparing comparison strategy")
        compare_reference_text, compare_research_text, compare_meta = _prepare_edureplica_comparison_texts(
            reference_text,
            research_text,
        )

        if compare_meta.get("used_chunking"):
            set_loading_phase("Long document mode: comparing selected chunks")
        else:
            set_loading_phase("Checking against sources")

        similarity = float(
            compare_texts(
                compare_reference_text,
                compare_research_text,
                progress_callback=set_loading_phase,
            )
        )
    except Exception as exc:
        messagebox.showerror("Comparison Error", str(exc))
        return
    finally:
        stop_loading()

    percent_plagiarized = max(0.0, min(100.0, similarity))
    percent_original = 100.0 - percent_plagiarized

    for widget in research_result_frame.winfo_children():
        widget.destroy()

    result_label = Label(
        research_result_frame,
        text="Results",
        fg=ACCENT,
        bg=BG_SURFACE,
        font=("Helvetica", 20, "bold"),
    )
    result_label.pack(anchor="nw", padx=40, pady=10)

    render_donut_chart(research_result_frame, [percent_plagiarized, percent_original])

    similarity_label = Label(
        research_result_frame,
        text=f"Similarity: {percent_plagiarized:.1f}%",
        fg=TEXT_MAIN,
        bg=BG_SURFACE,
        font=("Helvetica", 16, "bold"),
    )
    similarity_label.pack(pady=10, padx=20, anchor="w")

    detail_label = Label(
        research_result_frame,
        text=(
            f"Originality estimate: {percent_original:.1f}% | "
            "Based on sentence-level overlap against the selected reference file."
        ),
        fg=TEXT_MUTED,
        bg=BG_SURFACE,
        font=("Arial", 10),
        wraplength=820,
        justify="left",
    )
    detail_label.pack(pady=(0, 10), padx=20, anchor="w")

    if compare_meta.get("used_chunking"):
        chunk_note = Label(
            research_result_frame,
            text=(
                "Long document mode was used for speed: "
                f"reference chunks {compare_meta.get('reference_chunks_used', 0)}/"
                f"{compare_meta.get('reference_chunk_count', 0)}, "
                f"research chunks {compare_meta.get('research_chunks_used', 0)}/"
                f"{compare_meta.get('research_chunk_count', 0)} "
                f"(words compared: {compare_meta.get('reference_words_used', 0)} + "
                f"{compare_meta.get('research_words_used', 0)})."
            ),
            fg=ACCENT,
            bg=BG_SURFACE,
            font=("Arial", 10),
            wraplength=820,
            justify="left",
        )
        chunk_note.pack(pady=(0, 10), padx=20, anchor="w")

    research_result_frame.pack(fill=BOTH, expand=True)


def show_page(page):
    for frame in (home_page, review_page, rewrite_page, research_page):
        frame.pack_forget()
    page.pack(fill=BOTH, expand=True)


root = Tk()
root.title("PaperCritic")
root.geometry("980x760")
root.minsize(920, 700)
root.configure(bg=BG_MAIN)
try:
    root.iconbitmap(str(BASE_DIR / "favicon.ico"))
except Exception:
    pass

ttk_style = ttk.Style()
try:
    ttk_style.theme_use("clam")
except Exception:
    pass
ttk_style.configure(
    "Dark.Horizontal.TProgressbar",
    troughcolor=BG_HEADER,
    background=ACCENT,
    bordercolor=BG_HEADER,
    lightcolor=ACCENT,
    darkcolor=ACCENT,
)


def _create_loading_widgets(parent):
    frame = Frame(parent, bg=BG_MAIN)
    label = Label(frame, text="", bg=BG_MAIN, fg=ACCENT, font=("Arial", 10))
    bar = ttk.Progressbar(
        frame,
        orient="horizontal",
        length=430,
        mode="determinate",
        style="Dark.Horizontal.TProgressbar",
    )
    return frame, label, bar

canvas = Canvas(root, bg=BG_MAIN, highlightthickness=0)
scrollbar = Scrollbar(root, orient="vertical", command=canvas.yview)
scroll_frame = Frame(canvas, bg=BG_MAIN)


def _update_scrollregion(_event):
    canvas.configure(scrollregion=canvas.bbox("all"))


scroll_frame.bind("<Configure>", _update_scrollregion)
canvas_window = canvas.create_window((0, 0), window=scroll_frame, anchor="nw")
canvas.configure(yscrollcommand=scrollbar.set)


def _resize_window(event):
    canvas.itemconfigure(canvas_window, width=event.width)


def _on_mousewheel(event):
    canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")


canvas.bind("<Configure>", _resize_window)
canvas.bind_all("<MouseWheel>", _on_mousewheel)

canvas.pack(side=LEFT, fill=BOTH, expand=True)
scrollbar.pack(side=RIGHT, fill=Y)

header = Frame(scroll_frame, bg=BG_HEADER, height=62)
header.pack(side="top", fill=X)
header.pack_propagate(False)

home_page = Frame(scroll_frame, bg=BG_MAIN)
review_page = Frame(scroll_frame, bg=BG_MAIN)
rewrite_page = Frame(scroll_frame, bg=BG_MAIN)
research_page = Frame(scroll_frame, bg=BG_MAIN)

critic_photo = safe_load_image("PaperCriticLogo.jpg", (200, 50))
klarity_photo = safe_load_image("KlarityCheck.png", (240, 120))
authenti_photo = safe_load_image("AuthentiText.png", (240, 120))
edu_photo = safe_load_image("EduReplica.png", (240, 120))

if critic_photo:
    header_label = Label(header, image=critic_photo, bg=BG_HEADER)
else:
    header_label = Label(
        header,
        text="PaperCritic",
        font=("Helvetica", 18, "bold"),
        fg=TEXT_MAIN,
        bg=BG_HEADER,
    )
header_label.pack(side="left", pady=8, padx=16)

nav_buttons = [
    ("Home", home_page),
    ("KlarityCheck", review_page),
    ("AuthentiText", rewrite_page),
    ("EduReplica", research_page),
]
for label, page in nav_buttons:
    Button(
        header,
        text=label,
        command=lambda selected_page=page: show_page(selected_page),
        font=("Arial", 12, "bold"),
        fg=TEXT_MAIN,
        bg=BG_HEADER,
        activebackground=BG_SURFACE,
        activeforeground=TEXT_MAIN,
        borderwidth=0,
        relief="flat",
        cursor="hand2",
        padx=8,
    ).pack(side="right", pady=14, padx=8)

home_title = Label(
    home_page,
    text="Welcome to PaperCritic",
    font=("Helvetica", 26, "bold"),
    fg=TEXT_MAIN,
    bg=BG_MAIN,
)
home_title.pack(anchor="w", pady=(30, 8), padx=30)

home_subtitle = Label(
    home_page,
    text=(
        "Run plagiarism checks with optional reference fetching, compare local files, "
        "and rewrite flagged passages in your preferred tone."
    ),
    font=("Arial", 12),
    fg=TEXT_MUTED,
    bg=BG_MAIN,
    wraplength=860,
    justify="left",
)
home_subtitle.pack(anchor="w", padx=30, pady=(0, 18))

if klarity_photo:
    home_review_label = Label(home_page, image=klarity_photo, bg=BG_MAIN)
else:
    home_review_label = Label(
        home_page,
        text="KlarityCheck",
        font=("Helvetica", 20, "bold"),
        fg=ACCENT,
        bg=BG_MAIN,
    )
home_review_label.pack(anchor="w", pady=(8, 4), padx=30)

review_button = Button(
    home_page,
    text="Open KlarityCheck",
    bg=BUTTON_PRIMARY,
    fg=TEXT_MAIN,
    borderwidth=0,
    relief="flat",
    font=("Arial", 12, "bold"),
    padx=16,
    pady=8,
    command=lambda: show_page(review_page),
)
review_button.pack(anchor="w", padx=30, pady=(0, 20))

if authenti_photo:
    home_rewrite_label = Label(home_page, image=authenti_photo, bg=BG_MAIN)
else:
    home_rewrite_label = Label(
        home_page,
        text="AuthentiText",
        font=("Helvetica", 20, "bold"),
        fg=ACCENT,
        bg=BG_MAIN,
    )
home_rewrite_label.pack(anchor="w", pady=(8, 4), padx=30)

rewrite_button = Button(
    home_page,
    text="Open AuthentiText",
    bg=BUTTON_PRIMARY,
    fg=TEXT_MAIN,
    borderwidth=0,
    relief="flat",
    font=("Arial", 12, "bold"),
    padx=16,
    pady=8,
    command=lambda: show_page(rewrite_page),
)
rewrite_button.pack(anchor="w", padx=30, pady=(0, 20))

if klarity_photo:
    review_logo = Label(review_page, image=klarity_photo, bg=BG_MAIN)
else:
    review_logo = Label(
        review_page,
        text="KlarityCheck",
        font=("Helvetica", 22, "bold"),
        fg=ACCENT,
        bg=BG_MAIN,
    )
review_logo.pack(anchor="w", pady=(26, 10), padx=30)

review_help = Label(
    review_page,
    text=(
        "How KlarityCheck works:\n"
        "1) Paste text or upload a file.\n"
        "2) Topic/DOI: fetches reference docs + web sources.\n"
        "   URL: compares only against URL content.\n"
        "   Blank query: runs paragraph-based web search with SerpApi fallback.\n"
        "3) 'Check Plagiarism' auto-fetches references if needed.\n\n"
        "Examples: 'climate change adaptation' | '10.1038/s41586-020-2649-2' | "
        "'https://example.com/article'\n"
        "Optional SerpApi fallback: set SERPAPI_API_KEY in .env."
    ),
    font=("Arial", 11),
    fg=TEXT_MUTED,
    bg=BG_SURFACE,
    justify="left",
    wraplength=860,
    padx=14,
    pady=10,
)
review_help.pack(anchor="w", padx=30, pady=(0, 12), fill=X)

reference_label = Label(
    review_page,
    text="Reference Query (Topic / DOI / URL, optional)",
    font=("Helvetica", 14, "bold"),
    fg=TEXT_MAIN,
    bg=BG_MAIN,
)
reference_label.pack(anchor="w", padx=30, pady=(4, 4))

reference_query_entry = Entry(
    review_page,
    width=70,
    highlightthickness=1,
    highlightbackground=ACCENT,
    highlightcolor=ACCENT,
    relief="flat",
    font=("Arial", 12),
    bg=BG_SURFACE,
    fg=TEXT_MAIN,
    insertbackground=TEXT_MAIN,
)
reference_query_entry.pack(anchor="w", padx=30, pady=(0, 8))

reference_btn_frame = Frame(review_page, bg=BG_MAIN)
reference_btn_frame.pack(anchor="w", padx=30, pady=(0, 8))

Button(
    reference_btn_frame,
    text="Fetch References",
    bg=BUTTON_SUCCESS,
    fg=TEXT_MAIN,
    borderwidth=0,
    relief="flat",
    font=("Arial", 11, "bold"),
    padx=12,
    pady=7,
    command=import_references,
).pack(side=LEFT, padx=(0, 8))

Button(
    reference_btn_frame,
    text="Preview References",
    bg=BUTTON_PRIMARY,
    fg=TEXT_MAIN,
    borderwidth=0,
    relief="flat",
    font=("Arial", 11, "bold"),
    padx=12,
    pady=7,
    command=preview_loaded_references,
).pack(side=LEFT, padx=(0, 8))

Button(
    reference_btn_frame,
    text="Clear References",
    bg=BUTTON_DANGER,
    fg=TEXT_MAIN,
    borderwidth=0,
    relief="flat",
    font=("Arial", 11, "bold"),
    padx=12,
    pady=7,
    command=clear_loaded_references,
).pack(side=LEFT)

reference_status_label = Label(
    review_page,
    text="Loaded references: 0",
    font=("Arial", 10),
    fg=TEXT_MUTED,
    bg=BG_MAIN,
    justify="left",
)
reference_status_label.pack(anchor="w", padx=30, pady=(0, 8))

file_upload_button = Button(
    review_page,
    text="Upload Text File (.txt/.docx/.pdf)",
    bg=BUTTON_DANGER,
    fg=TEXT_MAIN,
    borderwidth=0,
    relief="flat",
    font=("Arial", 11, "bold"),
    padx=12,
    pady=7,
    command=file_upload,
)
file_upload_button.pack(anchor="w", padx=30, pady=(0, 4))

uploaded_file_label = Label(
    review_page,
    text="",
    font=("Arial", 10),
    bg=BG_MAIN,
    fg=ACCENT,
)
uploaded_file_label.pack(anchor="w", padx=30, pady=(0, 8))

loading_frame, progress_label, progress_bar = _create_loading_widgets(review_page)
rewrite_loading_frame, rewrite_progress_label, rewrite_progress_bar = _create_loading_widgets(rewrite_page)
research_loading_frame, research_progress_label, research_progress_bar = _create_loading_widgets(research_page)

loading_widgets.update(
    {
        "review": {
            "frame": loading_frame,
            "label": progress_label,
            "bar": progress_bar,
        },
        "rewrite": {
            "frame": rewrite_loading_frame,
            "label": rewrite_progress_label,
            "bar": rewrite_progress_bar,
        },
        "research": {
            "frame": research_loading_frame,
            "label": research_progress_label,
            "bar": research_progress_bar,
        },
    }
)
set_loading_context("review")

feedback_label = Label(
    review_page,
    text="",
    bg=BG_MAIN,
    fg=TEXT_MUTED,
    font=("Arial", 10),
    wraplength=860,
    justify="left",
)
feedback_label.pack(anchor="w", padx=30, pady=(0, 10))

text_box = Text(
    review_page,
    height=14,
    width=95,
    highlightthickness=1,
    highlightbackground=ACCENT,
    highlightcolor=ACCENT,
    relief="flat",
    bg=BG_SURFACE,
    fg=TEXT_MAIN,
    insertbackground=TEXT_MAIN,
    font=("Arial", 12),
)
text_box.pack(anchor="w", padx=30, pady=(0, 12), expand=True)

plag_check_button = Button(
    review_page,
    text="Check Plagiarism",
    bg=BUTTON_PRIMARY,
    fg=TEXT_MAIN,
    borderwidth=0,
    relief="flat",
    font=("Arial", 12, "bold"),
    padx=14,
    pady=8,
    command=show_report,
)
plag_check_button.pack(anchor="w", padx=30, pady=(0, 12))

report_frame = Frame(review_page, bg=BG_SURFACE)

if authenti_photo:
    rewrite_logo = Label(rewrite_page, image=authenti_photo, bg=BG_MAIN)
else:
    rewrite_logo = Label(
        rewrite_page,
        text="AuthentiText",
        font=("Helvetica", 22, "bold"),
        fg=ACCENT,
        bg=BG_MAIN,
    )
rewrite_logo.pack(anchor="w", pady=(24, 10), padx=30)

rewrite_hint = Label(
    rewrite_page,
    text="Paste text, choose a tone, and generate a clearer rewrite.",
    font=("Arial", 11),
    fg=TEXT_MUTED,
    bg=BG_MAIN,
)
rewrite_hint.pack(anchor="w", padx=30, pady=(0, 8))

text_box2 = Text(
    rewrite_page,
    height=14,
    width=95,
    highlightthickness=1,
    highlightbackground=ACCENT,
    highlightcolor=ACCENT,
    relief="flat",
    bg=BG_SURFACE,
    fg=TEXT_MAIN,
    insertbackground=TEXT_MAIN,
    font=("Arial", 12),
)
text_box2.pack(anchor="w", padx=30, pady=(0, 10), expand=True)

rewrite_styles = ["Professional", "Creative", "Formal", "Casual"]
rewrite_selected_label = Label(
    rewrite_page,
    text="Select Writing Tone",
    font=("Helvetica", 14, "bold"),
    fg=TEXT_MAIN,
    bg=BG_MAIN,
)
rewrite_selected_label.pack(anchor="w", padx=30, pady=(0, 4))


def update_rewrite_label(_event):
    rewrite_selected_label.config(text=f"{rewrite_dropdown.get()} Tone Selected")


rewrite_dropdown = ttk.Combobox(
    rewrite_page,
    values=rewrite_styles,
    state="readonly",
    font=("Arial", 12),
    width=22,
)
rewrite_dropdown.set("Select a Tone")
rewrite_dropdown.pack(anchor="w", padx=30, pady=(0, 8))
rewrite_dropdown.bind("<<ComboboxSelected>>", update_rewrite_label)

rewrite_btn = Button(
    rewrite_page,
    text="Rewrite",
    bg=BUTTON_PRIMARY,
    fg=TEXT_MAIN,
    borderwidth=0,
    relief="flat",
    font=("Arial", 12, "bold"),
    padx=14,
    pady=8,
    command=lambda: rewrite_func(text_box2.get("1.0", END), rewrite_dropdown.get()),
)
rewrite_btn.pack(anchor="w", padx=30, pady=(0, 10))

modified_text_frame = Frame(rewrite_page, bg=BG_SURFACE)

if edu_photo:
    research_logo = Label(research_page, image=edu_photo, bg=BG_MAIN)
else:
    research_logo = Label(
        research_page,
        text="EduReplica",
        font=("Helvetica", 22, "bold"),
        fg=ACCENT,
        bg=BG_MAIN,
    )
research_logo.pack(anchor="w", pady=(24, 10), padx=30)

research_hint = Label(
    research_page,
    text=(
        "Compare two local files directly. Reference File is the source to compare against, "
        "and Research Paper is your draft. Long documents are automatically optimized using "
        "chunk selection for faster checks."
    ),
    font=("Arial", 11),
    fg=TEXT_MUTED,
    bg=BG_MAIN,
    wraplength=860,
    justify="left",
)
research_hint.pack(anchor="w", padx=30, pady=(0, 10))

file_label1 = Label(
    research_page,
    text="Reference File:",
    font=("Helvetica", 14, "bold"),
    fg=TEXT_MAIN,
    bg=BG_MAIN,
)
file_label1.pack(anchor="w", padx=30, pady=(0, 4))

file_info1 = Entry(
    research_page,
    width=70,
    highlightthickness=1,
    highlightbackground=ACCENT,
    highlightcolor=ACCENT,
    relief="flat",
    font=("Arial", 12),
    bg=BG_SURFACE,
    fg=TEXT_MAIN,
    insertbackground=TEXT_MAIN,
)
file_info1.pack(anchor="w", padx=30, pady=(0, 6))

file_upload_button1 = Button(
    research_page,
    text="Select Reference File",
    bg=BUTTON_DANGER,
    fg=TEXT_MAIN,
    borderwidth=0,
    relief="flat",
    font=("Arial", 11, "bold"),
    padx=12,
    pady=7,
    command=lambda: get_filepath("ref_file"),
)
file_upload_button1.pack(anchor="w", padx=30, pady=(0, 12))

file_label2 = Label(
    research_page,
    text="Research Paper:",
    font=("Helvetica", 14, "bold"),
    fg=TEXT_MAIN,
    bg=BG_MAIN,
)
file_label2.pack(anchor="w", padx=30, pady=(0, 4))

file_info2 = Entry(
    research_page,
    width=70,
    highlightthickness=1,
    highlightbackground=ACCENT,
    highlightcolor=ACCENT,
    relief="flat",
    font=("Arial", 12),
    bg=BG_SURFACE,
    fg=TEXT_MAIN,
    insertbackground=TEXT_MAIN,
)
file_info2.pack(anchor="w", padx=30, pady=(0, 6))

file_upload_button2 = Button(
    research_page,
    text="Select Research Paper",
    bg=BUTTON_DANGER,
    fg=TEXT_MAIN,
    borderwidth=0,
    relief="flat",
    font=("Arial", 11, "bold"),
    padx=12,
    pady=7,
    command=lambda: get_filepath("text_file"),
)
file_upload_button2.pack(anchor="w", padx=30, pady=(0, 10))

cart_button = Button(
    research_page,
    text="Check Research",
    bg=BUTTON_SUCCESS,
    fg=TEXT_MAIN,
    borderwidth=0,
    relief="flat",
    font=("Arial", 12, "bold"),
    padx=14,
    pady=8,
    command=check_research,
)
cart_button.pack(anchor="w", padx=30, pady=(0, 12))

research_result_frame = Frame(research_page, bg=BG_SURFACE)

rewrite_providers = []
if OPENAI_API_KEY:
    rewrite_providers.append("OpenAI")
if OPENROUTER_API_KEY:
    rewrite_providers.append("OpenRouter")
if GROQ_API_KEY:
    rewrite_providers.append("Groq")
if TOGETHER_API_KEY:
    rewrite_providers.append("Together")

if rewrite_providers:
    set_feedback(
        "Rewrite providers enabled: " + ", ".join(rewrite_providers) + ".",
        SUCCESS,
    )
else:
    set_feedback(
        "No external AI key found. AuthentiText will use local rewrite fallback. "
        "Add OPENAI_API_KEY, OPENROUTER_API_KEY, GROQ_API_KEY, or TOGETHER_API_KEY in .env for AI rewrite.",
        WARNING,
    )

update_reference_status(
    "Ready. Use topic/DOI/URL or leave query blank for web search mode.",
    TEXT_MUTED,
)
show_page(home_page)
root.mainloop()

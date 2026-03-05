import os
import time
import webbrowser
from pathlib import Path
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
    check_against_reference_text as cart,
    fetch_reference_from_url,
    fetch_reference_texts,
    process_file,
)


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
client = OpenAI(api_key=OPENAI_API_KEY) if OpenAI and OPENAI_API_KEY else None


full_text = ""
rewritten_text = ""
api_references = []
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


def simulate_progress(progress_bar, label, label_text, duration=0.5):
    """Show quick progress feedback without adding large delays."""
    steps = 20
    for step in range(steps + 1):
        percent = int((step / steps) * 100)
        progress_bar["value"] = percent
        label.config(text=f"{label_text}: {percent}%")
        progress_bar.update_idletasks()
        if duration > 0:
            time.sleep(duration / steps)


def set_feedback(message, color="#8e44ad"):
    feedback_label.config(text=message, fg=color)


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

    chart_canvas = Canvas(parent, width=340, height=220, bg="#1b2631", highlightthickness=0)
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
            fill="#e74c3c",
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
            fill="#aed6f1",
            outline="",
        )

    chart_canvas.create_oval(70, 60, 150, 140, fill="#1b2631", outline="")
    chart_canvas.create_text(
        110,
        100,
        text=f"{plagiarized:.1f}%",
        fill="white",
        font=("Helvetica", 16, "bold"),
    )

    chart_canvas.create_rectangle(230, 55, 246, 70, fill="#e74c3c", outline="")
    chart_canvas.create_text(
        254,
        62,
        text="Plagiarized",
        fill="white",
        font=("Arial", 10),
        anchor="w",
    )
    chart_canvas.create_rectangle(230, 90, 246, 105, fill="#aed6f1", outline="")
    chart_canvas.create_text(
        254,
        97,
        text="Original",
        fill="white",
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


def rewrite_with_tone(text, tone):
    cleaned = (text or "").strip()
    if not cleaned:
        return "Kindly enter text to rewrite."

    if not client:
        return (
            "Rewrite assistant is unavailable. Add OPENAI_API_KEY to your .env file and restart "
            "the app to enable AI rewrite."
        )

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a professional rewriter. Keep factual meaning, improve originality, "
                        "and rewrite clearly in the requested tone."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Rewrite this text in a {tone} tone:\n\n{cleaned}",
                },
            ],
        )
        rewritten = (response.choices[0].message.content or "").strip()
        return rewritten or "No rewritten output generated."
    except Exception as exc:
        return f"Rewrite failed: {exc}"


def file_upload():
    file_path = filedialog.askopenfilename(
        title="Select a file",
        filetypes=[("Supported Files", "*.txt *.docx *.pdf"), ("All Files", "*.*")],
    )
    if not file_path:
        return ""

    progress_label.pack(padx=40)
    progress_bar.pack(padx=40, pady=5)
    simulate_progress(progress_bar, progress_label, "Uploading file", duration=0.4)

    content = process_file(file_path)

    progress_label.pack_forget()
    progress_bar.pack_forget()

    if not content:
        messagebox.showwarning("Error", "Unsupported file type or empty content.")
        return ""

    text_box.delete("1.0", END)
    text_box.insert(END, content)
    uploaded_file_label.config(text=f"Loaded file: {Path(file_path).name}", fg="#3498db")
    return file_path


def import_references():
    global api_references

    query = reference_query_entry.get().strip()
    if not query:
        messagebox.showwarning("Missing Input", "Enter a topic, DOI, title, or URL first.")
        return

    progress_label.pack(padx=40)
    progress_bar.pack(padx=40, pady=5)
    simulate_progress(progress_bar, progress_label, "Loading references", duration=0.4)

    if query.lower().startswith(("http://", "https://")):
        reference, status = fetch_reference_from_url(query, timeout=8)
        api_references = [reference] if reference else []
    else:
        api_references, status = fetch_reference_texts(query, max_results=8, timeout=8)

    progress_label.pack_forget()
    progress_bar.pack_forget()

    if api_references:
        set_feedback(status, "#229954")
    else:
        set_feedback(status, "#f1c40f")


def request_review(text):
    global plagiarized_data, plagiarized_contents, data, api_references

    references = list(api_references)
    status_message = ""

    if not references:
        query = reference_query_entry.get().strip()
        if query:
            if query.lower().startswith(("http://", "https://")):
                reference, status_message = fetch_reference_from_url(query, timeout=8)
                references = [reference] if reference else []
            else:
                references, status_message = fetch_reference_texts(
                    query,
                    max_results=8,
                    timeout=8,
                )
            if references:
                api_references = references

    plagiarized_data = analyze_text_against_references(text, references)
    plagiarized_contents = plagiarized_data.get("plagiarized_contents", {})
    data = plagiarized_data.get("data", [0.0, 100.0])

    if references:
        message = status_message or plagiarized_data.get("note", "External comparison completed.")
        set_feedback(message, "#229954")
    else:
        note = plagiarized_data.get("note", "") or status_message
        if not note:
            note = "No external references loaded."
        set_feedback(
            f"{note} Use a topic/URL import or compare two local files in EduReplica.",
            "#f1c40f",
        )


def show_report():
    global full_text

    full_text = text_box.get("1.0", END).strip()
    words = full_text.split()
    selected_text = " ".join(words[:1200]) if len(words) > 1200 else full_text

    if not full_text or full_text == "Kindly enter a text to check for plagiarism.":
        text_box.delete("1.0", END)
        text_box.insert(END, "Kindly enter a text to check for plagiarism.\n")
        return

    text_box.config(state="disabled")
    for widget in report_frame.winfo_children():
        widget.destroy()

    progress_label.pack(padx=40)
    progress_bar.pack(padx=40, pady=5)
    simulate_progress(progress_bar, progress_label, "Processing Text", duration=0.5)
    request_review(selected_text)
    progress_label.pack_forget()
    progress_bar.pack_forget()

    text_box.config(state="normal")
    report_frame.pack(fill=BOTH, expand=True)

    chart_label = Label(
        report_frame,
        text="Overview",
        fg="#8e44ad",
        bg="#1b2631",
        font=("Helvetica", 20, "bold"),
    )
    chart_label.pack(anchor="nw", padx=40, pady=10)

    render_donut_chart(report_frame, data)

    plag_percent = max(0.0, min(100.0, float(data[0] if data else 0.0)))
    appraisal = Label(report_frame, text="", bg="#1b2631", fg="red", font=("Helvetica", 14))

    if plag_percent == 0:
        chart_report = "Great work. No overlap detected in the current reference set."
        appraisal.config(text=chart_report, fg="#229954")
    elif 0 < plag_percent <= 30:
        chart_report = "Low overlap. A few edits and citations should make this safer."
        appraisal.config(text=chart_report, fg="#229954")
    elif 30 < plag_percent <= 50:
        chart_report = "Moderate overlap. Rewrite key passages and add proper citations."
        appraisal.config(text=chart_report, fg="#f1c40f")
    else:
        chart_report = "High overlap detected. Heavy rewrite and attribution are required."
        appraisal.config(text=chart_report, fg="#e74c3c")

    appraisal.pack(anchor="nw", padx=40, pady=10)

    line_divider = Frame(report_frame, bg="red", height=5)
    line_divider.pack(fill=X, pady=10, expand=True)

    plagiarized_label = Label(
        report_frame,
        text="Plagiarized Content Review",
        bg="#1b2631",
        fg="red",
        font=("Helvetica", 14),
    )
    plagiarized_label.pack(anchor="nw", padx=40, pady=10)

    plagiarized_text = Text(
        report_frame,
        fg="white",
        bg="#1b2631",
        font=("Helvetica", 13),
        height=20,
        highlightthickness=2,
        highlightbackground="red",
        highlightcolor="#8e44ad",
        relief="flat",
    )
    plagiarized_text.pack(padx=10, pady=10, expand=True)

    if not plagiarized_contents:
        plagiarized_text.insert(
            END,
            "No strong overlaps detected from loaded sources. "
            "You can import references with a topic/DOI/URL for stronger checks.\n",
        )
    else:
        for index, key in enumerate(plagiarized_contents, start=1):
            item = plagiarized_contents[key]
            paragraph = item.get("plagiarized_paragraph", "")
            match_type = item.get("match_type", "Potential Match")
            source_title, source_url = item.get("source", ("Unknown Source", ""))
            score = float(item.get("score", 0.0))

            plagiarized_text.insert(END, f"{index}. {paragraph}\n", "paragraph")
            plagiarized_text.insert(
                END,
                f"Match Type: {match_type} ({score:.1f}%)\n",
                "match_type",
            )

            start_index = plagiarized_text.index(END)
            plagiarized_text.insert(END, f"Source: {source_title}\n")
            end_index = plagiarized_text.index(END)

            source_tag = f"source_{index}"
            plagiarized_text.tag_add(source_tag, start_index, end_index)
            plagiarized_text.tag_configure(source_tag, foreground="#3498db", underline=True)
            if source_url:
                plagiarized_text.tag_bind(
                    source_tag,
                    "<Button-1>",
                    lambda event, link=source_url: open_link(event, link),
                )

            suggestion = suggest_improvement(paragraph)
            plagiarized_text.insert(END, f"Suggestion: {suggestion}\n\n", "suggestion")

    plagiarized_text.tag_configure("paragraph", foreground="#ff7675", font=("Helvetica", 13, "bold"))
    plagiarized_text.tag_configure(
        "match_type",
        foreground="white",
        background="#c0392b",
        font=("Arial", 11, "italic"),
    )
    plagiarized_text.tag_configure(
        "suggestion",
        foreground="white",
        background="#1e8449",
        font=("Arial", 11),
    )
    plagiarized_text.config(state="disabled")

    tones = ["Professional", "Creative", "Formal", "Innovative"]
    tone_label = Label(
        report_frame,
        text="Would you like a rewrite? Select Writing Tone",
        font=("Helvetica", 18),
        fg="#8e44ad",
        bg="#1b2631",
    )
    tone_label.pack(anchor="nw", padx=40, pady=5, expand=True)

    report_tone_dropdown = ttk.Combobox(report_frame, values=tones, state="readonly")
    report_tone_dropdown.set("Select a Tone")
    report_tone_dropdown.pack(anchor="nw", padx=50, pady=5, expand=True)

    rewrite_btn1 = Button(
        report_frame,
        text="Rewrite",
        bg="#0869d7",
        fg="white",
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

    rewritten_text = rewrite_with_tone(check_text, selected_tone)

    modified_text_frame.pack(fill=BOTH, expand=True)
    line_divider = Frame(modified_text_frame, bg="#2ecc71", height=5)
    line_divider.pack(anchor="nw", fill=X, pady=10, expand=True)

    modifier_label = Label(
        modified_text_frame,
        text=f"Rewritten Text ({selected_tone} Tone)",
        bg="#1b2631",
        fg="red",
        font=("Helvetica", 14),
    )
    modifier_label.pack(anchor="nw", padx=40, pady=10)

    modified_text_box = Text(
        modified_text_frame,
        fg="white",
        bg="#1b2631",
        font=("Helvetica", 13),
        height=20,
        highlightthickness=2,
        highlightbackground="red",
        highlightcolor="#8e44ad",
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
        fg="#8e44ad",
        bg="#1b2631",
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
        file_info1.config(fg="#3498db")
        ref_file = file_path
    elif file_slot == "text_file":
        file_info2.delete(0, END)
        file_info2.insert(END, file_path)
        file_info2.config(fg="#3498db")
        text_file = file_path

    cart_button.pack(anchor="nw", padx=40, pady=10)


def check_research():
    if not ref_file or not text_file:
        messagebox.showwarning("Missing Files", "Please add both reference and research files.")
        return

    progress_label.pack(padx=40)
    progress_bar.pack(padx=40, pady=5)
    simulate_progress(progress_bar, progress_label, "Comparing files", duration=0.4)

    try:
        similarity = float(cart(ref_file, text_file))
    except Exception as exc:
        progress_label.pack_forget()
        progress_bar.pack_forget()
        messagebox.showerror("Comparison Error", str(exc))
        return

    progress_label.pack_forget()
    progress_bar.pack_forget()

    percent_plagiarized = max(0.0, min(100.0, similarity))
    percent_original = 100.0 - percent_plagiarized

    for widget in research_result_frame.winfo_children():
        widget.destroy()

    result_label = Label(
        research_result_frame,
        text="Results",
        fg="#8e44ad",
        bg="#1b2631",
        font=("Helvetica", 20, "bold"),
    )
    result_label.pack(anchor="nw", padx=40, pady=10)

    render_donut_chart(research_result_frame, [percent_plagiarized, percent_original])

    text_label.config(text=f"Similarity: {percent_plagiarized:.1f}%")
    text_label.pack(pady=10, padx=20, expand=True)

    research_result_frame.pack(fill=BOTH, expand=True)


def show_page(page):
    for frame in (home_page, review_page, rewrite_page, research_page):
        frame.pack_forget()
    page.pack(fill=BOTH, expand=True)


root = Tk()
root.title("AUTHENTITEXT")
root.geometry("900x700")
try:
    root.iconbitmap(str(BASE_DIR / "favicon.ico"))
except Exception:
    pass

canvas = Canvas(root, bg="#1b2631")
scrollbar = Scrollbar(root, orient="vertical", command=canvas.yview)
scroll_frame = Frame(canvas, bg="#1b2631")
scroll_frame.bind(
    "<Configure>",
    lambda event: canvas.configure(scrollregion=canvas.bbox("all")),
)

canvas.create_window((0, 0), window=scroll_frame, anchor="nw")
canvas.configure(yscrollcommand=scrollbar.set)

canvas.pack(side=LEFT, fill=BOTH, expand=True)
scrollbar.pack(side=RIGHT, fill=Y)

header = Frame(scroll_frame, bg="white", height=50)
header.pack(side="top", fill=X)

home_page = Frame(scroll_frame, bg="#1b2631")
review_page = Frame(scroll_frame, bg="#1b2631")
rewrite_page = Frame(scroll_frame, bg="#1b2631")
research_page = Frame(scroll_frame, bg="#1b2631")

critic_photo = safe_load_image("PaperCriticLogo.jpg", (200, 50))
klarity_photo = safe_load_image("KlarityCheck.png", (200, 120))
authenti_photo = safe_load_image("AuthentiText.png", (200, 120))
edu_photo = safe_load_image("EduReplica.png", (200, 120))

if critic_photo:
    header_label = Label(header, image=critic_photo, bg="white")
else:
    header_label = Label(
        header,
        text="PaperCritic",
        font=("Helvetica", 18, "bold"),
        fg="#8e44ad",
        bg="white",
    )
header_label.pack(side="left", pady=10, padx=10)

nav_buttons = [
    ("EduReplica", research_page),
    ("AuthentiText", rewrite_page),
    ("KlarityCheck", review_page),
    ("Home", home_page),
]
for label, page in nav_buttons:
    Button(
        header,
        text=label,
        command=lambda selected_page=page: show_page(selected_page),
        font=("Arial", 14),
        fg="#8e44ad",
        bg="white",
        borderwidth=1,
        relief="flat",
    ).pack(side="right", pady=10, padx=10)

welcome_message = Label(home_page, text="", font=("Helvetica", 16), fg="white", bg="#1b2631")
welcome_message.pack(pady=40, padx=10)
welcome_message1 = Label(
    home_page,
    text="Welcome to",
    font=("Helvetica", 16),
    fg="white",
    bg="#1b2631",
)
welcome_message2 = Label(
    home_page,
    text="PaperCritic",
    font=("Helvetica", 22, "bold"),
    fg="#8e44ad",
    bg="#1b2631",
)
welcome_message1.pack(padx=8, pady=5, expand=True)
welcome_message2.pack(padx=8, pady=5, expand=True)

if klarity_photo:
    home_review_label = Label(home_page, image=klarity_photo, bg="#1b2631")
else:
    home_review_label = Label(
        home_page,
        text="KlarityCheck",
        font=("Helvetica", 18, "bold"),
        fg="#8e44ad",
        bg="#1b2631",
    )
home_review_label.pack(pady=10, padx=20, expand=True)

review_button = Button(
    home_page,
    text="Get Started",
    bg="#0869d7",
    fg="white",
    borderwidth=1,
    relief="flat",
    font=("Arial", 14),
    command=lambda: show_page(review_page),
)
review_button.pack(padx=20, pady=10, expand=True)

if authenti_photo:
    home_rewrite_label = Label(home_page, image=authenti_photo, bg="#1b2631")
else:
    home_rewrite_label = Label(
        home_page,
        text="AuthentiText",
        font=("Helvetica", 18, "bold"),
        fg="#8e44ad",
        bg="#1b2631",
    )
home_rewrite_label.pack(pady=10, padx=20, expand=True)

rewrite_button = Button(
    home_page,
    text="Get Started",
    bg="#0869d7",
    fg="white",
    borderwidth=1,
    relief="flat",
    font=("Arial", 14),
    command=lambda: show_page(rewrite_page),
)
rewrite_button.pack(padx=20, pady=10, expand=True)

if klarity_photo:
    review_logo = Label(review_page, image=klarity_photo, bg="#1b2631")
else:
    review_logo = Label(
        review_page,
        text="KlarityCheck",
        font=("Helvetica", 20, "bold"),
        fg="#8e44ad",
        bg="#1b2631",
    )
review_logo.pack(anchor="nw", pady=25, padx=5)

reference_label = Label(
    review_page,
    text="Reference topic, DOI, or URL:",
    font=("Helvetica", 16),
    fg="#8e44ad",
    bg="#1b2631",
)
reference_label.pack(anchor="nw", padx=30, pady=5)

reference_query_entry = Entry(
    review_page,
    width=45,
    highlightthickness=2,
    highlightbackground="red",
    highlightcolor="#3498db",
    relief="flat",
    font=("Arial", 14),
    bg="#1b2631",
    fg="white",
)
reference_query_entry.pack(anchor="nw", padx=30, expand=True)

import_button = Button(
    review_page,
    text="Import References",
    bg="#1e8449",
    fg="white",
    borderwidth=1,
    relief="flat",
    font=("Arial", 14),
    command=import_references,
)
import_button.pack(anchor="nw", padx=40, pady=10)

file_upload_button = Button(
    review_page,
    text="Add File",
    bg="#e74c3c",
    fg="white",
    borderwidth=1,
    relief="flat",
    font=("Arial", 14),
    command=file_upload,
)
file_upload_button.pack(anchor="nw", padx=40)

uploaded_file_label = Label(review_page, text="", font=("Arial", 11), bg="#1b2631", fg="#8e44ad")
uploaded_file_label.pack(anchor="nw", padx=40, pady=5)

progress_label = Label(review_page, text="", bg="#1b2631", fg="#8e44ad")
progress_bar = ttk.Progressbar(review_page, orient="horizontal", length=250, mode="determinate")

feedback_label = Label(
    review_page,
    text="",
    bg="#1b2631",
    fg="#8e44ad",
    font=("Arial", 11),
    wraplength=720,
    justify="left",
)
feedback_label.pack(anchor="nw", padx=30, pady=5)

text_box = Text(
    review_page,
    height=15,
    width=90,
    highlightthickness=2,
    highlightbackground="red",
    highlightcolor="#8e44ad",
    relief="flat",
    bg="#1b2631",
    fg="white",
    font=("Arial", 13),
)
text_box.pack(anchor="nw", padx=30, pady=15, expand=True)

plag_check_button = Button(
    review_page,
    text="Check For Plagiarism",
    bg="#8e44ad",
    fg="white",
    borderwidth=1,
    relief="flat",
    font=("Arial", 14),
    command=show_report,
)
plag_check_button.pack(padx=15, pady=10, expand=True)

report_frame = Frame(review_page, bg="#1b2631")

if authenti_photo:
    rewrite_logo = Label(rewrite_page, image=authenti_photo, bg="#1b2631")
else:
    rewrite_logo = Label(
        rewrite_page,
        text="AuthentiText",
        font=("Helvetica", 20, "bold"),
        fg="#8e44ad",
        bg="#1b2631",
    )
rewrite_logo.pack(anchor="nw", pady=20, padx=5)

text_box2 = Text(
    rewrite_page,
    height=15,
    width=90,
    highlightthickness=2,
    highlightbackground="red",
    highlightcolor="#8e44ad",
    relief="flat",
    bg="#1b2631",
    fg="white",
    font=("Arial", 13),
)
text_box2.pack(anchor="nw", padx=30, pady=20, expand=True)

rewrite_styles = ["Professional", "Creative", "Formal", "Casual"]
rewrite_selected_label = Label(
    rewrite_page,
    text="Select Writing Tone",
    font=("Helvetica", 18),
    fg="#8e44ad",
    bg="#1b2631",
)
rewrite_selected_label.pack(anchor="nw", padx=40, pady=5, expand=True)


def update_rewrite_label(_event):
    rewrite_selected_label.config(text=f"{rewrite_dropdown.get()} Tone Selected")


rewrite_dropdown = ttk.Combobox(
    rewrite_page,
    values=rewrite_styles,
    state="readonly",
    font=("Arial", 14),
)
rewrite_dropdown.set("Select a Tone")
rewrite_dropdown.pack(anchor="nw", padx=50, pady=5, expand=True)
rewrite_dropdown.bind("<<ComboboxSelected>>", update_rewrite_label)

rewrite_btn = Button(
    rewrite_page,
    text="Rewrite",
    bg="#0869d7",
    fg="white",
    borderwidth=1,
    relief="flat",
    font=("Arial", 14),
    command=lambda: rewrite_func(text_box2.get("1.0", END), rewrite_dropdown.get()),
)
rewrite_btn.pack(anchor="nw", padx=50, pady=10, expand=True)

modified_text_frame = Frame(rewrite_page, bg="#1b2631")

if edu_photo:
    research_logo = Label(research_page, image=edu_photo, bg="#1b2631")
else:
    research_logo = Label(
        research_page,
        text="EduReplica",
        font=("Helvetica", 20, "bold"),
        fg="#8e44ad",
        bg="#1b2631",
    )
research_logo.pack(anchor="nw", pady=20, padx=5)

file_label1 = Label(
    research_page,
    text="Add Reference File:",
    font=("Helvetica", 18),
    fg="#8e44ad",
    bg="#1b2631",
)
file_label1.pack(anchor="nw", padx=30, pady=10)

file_info1 = Entry(
    research_page,
    width=45,
    highlightthickness=2,
    highlightbackground="red",
    highlightcolor="#3498db",
    relief="flat",
    font=("Arial", 14),
    bg="#1b2631",
    fg="white",
)
file_info1.pack(anchor="nw", padx=30, expand=True)

file_upload_button1 = Button(
    research_page,
    text="Add File",
    bg="#e74c3c",
    fg="white",
    borderwidth=1,
    relief="flat",
    font=("Arial", 14),
    command=lambda: get_filepath("ref_file"),
)
file_upload_button1.pack(anchor="nw", padx=40, pady=10)

file_label2 = Label(
    research_page,
    text="Add Research Paper:",
    font=("Helvetica", 18),
    fg="#8e44ad",
    bg="#1b2631",
)
file_label2.pack(anchor="nw", padx=30, pady=10)

file_info2 = Entry(
    research_page,
    width=45,
    highlightthickness=2,
    highlightbackground="red",
    highlightcolor="#3498db",
    relief="flat",
    font=("Arial", 14),
    bg="#1b2631",
    fg="white",
)
file_info2.pack(anchor="nw", padx=30, expand=True)

file_upload_button2 = Button(
    research_page,
    text="Add File",
    bg="#e74c3c",
    fg="white",
    borderwidth=1,
    relief="flat",
    font=("Arial", 14),
    command=lambda: get_filepath("text_file"),
)
file_upload_button2.pack(anchor="nw", padx=40, pady=10)

cart_button = Button(
    research_page,
    text="Check Research",
    bg="#229954",
    fg="white",
    borderwidth=1,
    relief="flat",
    font=("Arial", 14),
    command=check_research,
)

research_result_frame = Frame(research_page, bg="#1b2631")
text_label = Label(
    research_result_frame,
    text="",
    fg="#8e44ad",
    bg="#1b2631",
    font=("Helvetica", 20, "bold"),
)

if not OPENAI_API_KEY:
    set_feedback(
        "OPENAI_API_KEY not found in .env. Rewriting and suggestions will use fallback text.",
        "#f1c40f",
    )
else:
    set_feedback("OpenAI features are enabled.", "#229954")

show_page(home_page)
root.mainloop()

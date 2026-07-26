import json
import os
import base64
from kivy.app import App
from kivy.core.window import Window
from kivy.uix.screenmanager import ScreenManager, Screen
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.textinput import TextInput
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.spinner import Spinner
from kivy.uix.filechooser import FileChooserListView
from kivy.uix.popup import Popup
from kivy.core.clipboard import Clipboard
from kivy.network.urlrequest import UrlRequest
from kivy.clock import mainthread
from kivy.metrics import dp

Window.softinput_mode = "below_target"

CONFIG_FILE = "settings.json"
HISTORY_FILE = "history.json"

PDF_COMMAND_WORDS = [
    "pdf creat", "pdf create", "pdf banao", "pdf bana", "pdf bnao",
    "save as pdf", "pdf me save", "pdf me convert", "pdf convert",
    "iska pdf", "isko pdf", "is code ka pdf",
]


# ---------- Storage helpers ----------

def get_path(name):
    app = App.get_running_app()
    return os.path.join(app.user_data_dir, name)


def load_settings():
    path = get_path(CONFIG_FILE)
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {"provider": "Groq", "api_key": "", "model": "llama-3.3-70b-versatile"}


def save_settings(data):
    with open(get_path(CONFIG_FILE), "w") as f:
        json.dump(data, f)


def load_history():
    path = get_path(HISTORY_FILE)
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return []


def save_history(messages):
    with open(get_path(HISTORY_FILE), "w") as f:
        json.dump(messages, f)


def read_pdf_text(filepath):
    try:
        from pypdf import PdfReader
        reader = PdfReader(filepath)
        text = ""
        for page in reader.pages:
            text += page.extract_text() or ""
        return text.strip() or "(PDF me text nahi mila — shayad scanned image hai)"
    except Exception as e:
        return f"(PDF read error: {e})"


def create_pdf_from_text(text, filename="output.pdf"):
    """reportlab se PDF banata hai — fpdf2/fontTools ka dependency issue avoid karne ke liye."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    from reportlab.lib.units import mm

    out_path = get_path(filename)
    c = canvas.Canvas(out_path, pagesize=A4)
    width, height = A4
    x_margin = 15 * mm
    y = height - 20 * mm
    line_height = 6 * mm
    c.setFont("Helvetica", 10)

    for raw_line in text.split("\n"):
        # simple word-wrap so long lines don't overflow the page
        words = raw_line.split(" ")
        line = ""
        for word in words:
            test_line = (line + " " + word).strip()
            if c.stringWidth(test_line, "Helvetica", 10) > (width - 2 * x_margin):
                c.drawString(x_margin, y, line)
                y -= line_height
                line = word
                if y < 20 * mm:
                    c.showPage()
                    c.setFont("Helvetica", 10)
                    y = height - 20 * mm
            else:
                line = test_line
        c.drawString(x_margin, y, line)
        y -= line_height
        if y < 20 * mm:
            c.showPage()
            c.setFont("Helvetica", 10)
            y = height - 20 * mm

    c.save()
    return out_path


def is_pdf_command(text):
    lowered = text.lower()
    return any(word in lowered for word in PDF_COMMAND_WORDS)


def image_to_base64(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def resolve_shared_path(uri_or_path):
    """
    Gallery se aayi content:// URI ko app ke private storage me copy karke
    real usable file-path deta hai (Android scoped-storage ke liye zaroori).
    """
    if not uri_or_path:
        return None
    if not str(uri_or_path).startswith("content://"):
        return uri_or_path
    try:
        from androidstorage4kivy import SharedStorage
        ss = SharedStorage()
        local_path = ss.copy_from_shared(uri_or_path)
        return local_path
    except Exception:
        return None


# ---------- UI ----------

class ChatBubble(BoxLayout):
    def __init__(self, text, is_user=False, **kwargs):
        super().__init__(orientation="vertical", size_hint_y=None,
                          padding=dp(8), spacing=dp(4), **kwargs)
        self.bind(minimum_height=self.setter("height"))

        prefix = "You: " if is_user else ""
        label = Label(text=prefix + text, size_hint_y=None, halign="left",
                       valign="top", color=(1, 1, 1, 1))
        label.bind(texture_size=lambda inst, val: setattr(label, "height", val[1]))
        label.bind(width=lambda inst, val: setattr(label, "text_size", (val, None)))
        self.add_widget(label)

        if not is_user:
            row = BoxLayout(size_hint_y=None, height=dp(36), spacing=dp(6))
            copy_btn = Button(text="Copy Code")
            copy_btn.bind(on_release=lambda inst: Clipboard.copy(text))
            row.add_widget(copy_btn)

            pdf_btn = Button(text="Save as PDF")
            pdf_btn.bind(on_release=lambda inst: self.save_pdf(text))
            row.add_widget(pdf_btn)
            self.add_widget(row)

    def save_pdf(self, text):
        try:
            path = create_pdf_from_text(text)
            popup = Popup(title="Saved",
                           content=Label(text=f"PDF saved:\n{path}"),
                           size_hint=(0.85, 0.3))
            popup.open()
        except Exception as e:
            popup = Popup(title="Error", content=Label(text=str(e)),
                           size_hint=(0.85, 0.3))
            popup.open()


class ChatScreen(Screen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.messages = load_history()
        self.last_ai_text = ""
        self.pending_image_path = None

        root = BoxLayout(orientation="vertical")

        top_bar = BoxLayout(size_hint_y=None, height=dp(48), padding=dp(4), spacing=dp(4))
        top_bar.add_widget(Label(text="Code Assistant", color=(1, 1, 1, 1)))
        clear_btn = Button(text="Clear", size_hint_x=None, width=dp(70))
        clear_btn.bind(on_release=self.clear_history)
        top_bar.add_widget(clear_btn)
        settings_btn = Button(text="Settings", size_hint_x=None, width=dp(90))
        settings_btn.bind(on_release=lambda a: setattr(self.manager, "current", "settings"))
        top_bar.add_widget(settings_btn)
        root.add_widget(top_bar)

        self.scroll = ScrollView()
        self.chat_box = BoxLayout(orientation="vertical", size_hint_y=None,
                                   spacing=dp(8), padding=dp(8))
        self.chat_box.bind(minimum_height=self.chat_box.setter("height"))
        self.scroll.add_widget(self.chat_box)
        root.add_widget(self.scroll)

        self.attach_label = Label(text="", size_hint_y=None, height=0, color=(0.7, 0.9, 1, 1))
        root.add_widget(self.attach_label)

        attach_bar = BoxLayout(size_hint_y=None, height=dp(48), padding=dp(4), spacing=dp(4))
        file_btn = Button(text="File")
        file_btn.bind(on_release=self.open_file_chooser)
        gallery_btn = Button(text="Gallery")
        gallery_btn.bind(on_release=self.open_gallery)
        attach_bar.add_widget(file_btn)
        attach_bar.add_widget(gallery_btn)
        root.add_widget(attach_bar)

        input_bar = BoxLayout(size_hint_y=None, height=dp(56), padding=dp(4), spacing=dp(4))
        self.input = TextInput(
            hint_text="App/website/game ka code maango...",
            multiline=False,
            foreground_color=(1, 1, 1, 1),
            background_color=(0.15, 0.15, 0.15, 1),
            hint_text_color=(0.6, 0.6, 0.6, 1),
            cursor_color=(1, 1, 1, 1),
        )
        self.input.bind(on_text_validate=self.send_message)
        send_btn = Button(text="Send", size_hint_x=None, width=dp(80))
        send_btn.bind(on_release=self.send_message)
        input_bar.add_widget(self.input)
        input_bar.add_widget(send_btn)
        root.add_widget(input_bar)

        self.add_widget(root)

        for m in self.messages:
            self.render_bubble(m["text"], m["is_user"])
            if not m["is_user"]:
                self.last_ai_text = m["text"]

    def render_bubble(self, text, is_user):
        bubble = ChatBubble(text=text, is_user=is_user, width=self.chat_box.width)
        self.chat_box.add_widget(bubble)
        self.scroll.scroll_y = 0

    def add_bubble(self, text, is_user=False, persist=True):
        self.render_bubble(text, is_user)
        if persist:
            self.messages.append({"text": text, "is_user": is_user})
            save_history(self.messages)
        if persist and not is_user:
            self.last_ai_text = text

    def clear_history(self, *a):
        self.messages = []
        self.last_ai_text = ""
        save_history(self.messages)
        self.chat_box.clear_widgets()

    # ---------- Attachments ----------

    def open_file_chooser(self, *a):
        chooser = FileChooserListView(path="/storage/emulated/0/", filters=["*.pdf", "*.txt"])
        popup = Popup(title="File chuno", content=chooser, size_hint=(0.9, 0.9))

        def on_selection(instance, selection):
            if selection:
                popup.dismiss()
                self.handle_uploaded_file(selection[0])

        chooser.bind(selection=on_selection)
        popup.open()

    def open_gallery(self, *a):
        try:
            from plyer import filechooser
            filechooser.open_file(
                on_selection=self.on_gallery_selected,
                filters=[["Images", "*.jpg", "*.jpeg", "*.png"]],
            )
        except Exception as e:
            self.add_bubble(f"⚠ Gallery open nahi hui: {e}", is_user=False)

    @mainthread
    def on_gallery_selected(self, selection):
        if not selection:
            return
        raw_uri = selection[0]
        real_path = resolve_shared_path(raw_uri)
        if not real_path or not os.path.exists(real_path):
            self.add_bubble("⚠ Image select hui lekin access nahi ho payi. Dobara try karo.",
                             is_user=False)
            return
        self.attach_image(real_path)

    def attach_image(self, path):
        self.pending_image_path = path
        self.attach_label.text = f"📷 Attached: {os.path.basename(path)} (Send dabao)"
        self.attach_label.height = dp(28)

    def handle_uploaded_file(self, filepath):
        self.add_bubble(f"[File uploaded: {os.path.basename(filepath)}]", is_user=True)
        if filepath.lower().endswith(".pdf"):
            text = read_pdf_text(filepath)
        else:
            try:
                with open(filepath, "r", errors="replace") as f:
                    text = f.read()
            except Exception as e:
                text = f"(File read error: {e})"

        settings = load_settings()
        if not settings.get("api_key"):
            self.add_bubble("⚠ Pehle Settings me API key daalo.", is_user=False)
            return
        prompt = f"Is file ke content ko analyze/summarize karo:\n\n{text[:6000]}"
        self.call_ai(prompt, settings)

    # ---------- Chat send ----------

    def send_message(self, *a):
        text = self.input.text.strip()
        image_path = self.pending_image_path
        self.pending_image_path = None
        self.attach_label.text = ""
        self.attach_label.height = 0

        if not text and not image_path:
            return

        settings = load_settings()

        if image_path:
            self.add_bubble((text or "[Image bheji]") + " 📷", is_user=True)
            self.input.text = ""
            if not settings.get("api_key"):
                self.add_bubble("⚠ Pehle Settings me API key daalo.", is_user=False)
                return
            if settings.get("provider") != "Gemini":
                self.add_bubble("⚠ Image samajhne ke liye Settings me provider 'Gemini' chuno "
                                 "(Groq images support nahi karta).", is_user=False)
                return
            self.call_ai_with_image(text or "Is image me kya hai, describe/analyze karo.",
                                     image_path, settings)
            return

        self.input.text = ""
        self.add_bubble(text, is_user=True)

        if is_pdf_command(text):
            if self.last_ai_text:
                try:
                    path = create_pdf_from_text(self.last_ai_text)
                    self.add_bubble(f"✅ PDF ban gayi:\n{path}", is_user=False)
                except Exception as e:
                    self.add_bubble(f"⚠ PDF banane me error: {e}", is_user=False)
            else:
                self.add_bubble("⚠ Pehle koi code/text maango, uske baad 'pdf banao' bolo.",
                                 is_user=False)
            return

        if not settings.get("api_key"):
            self.add_bubble("⚠ Pehle Settings me API key daalo.", is_user=False)
            return
        self.call_ai(text, settings)

    def call_ai(self, prompt, settings):
        provider = settings.get("provider", "Groq")
        api_key = settings.get("api_key", "")
        model = settings.get("model", "")

        system_prompt = (
            "Tum ek coding assistant ho. User jis app/website/game ka code maange, "
            "uska pura, clean, copy-paste-ready code do. Agar user format bataye "
            "(HTML, Python, Kivy, JS, etc.) to usi format me do. Agar user 'update' "
            "ya 'fix' bole to sirf modified code do, saath me chhota explanation."
        )

        if provider == "Groq":
            url = "https://api.groq.com/openai/v1/chat/completions"
            headers = {"Content-Type": "application/json",
                       "Authorization": f"Bearer {api_key}"}
            body = json.dumps({
                "model": model or "llama-3.3-70b-versatile",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ],
            })
        else:
            model_name = model or "gemini-2.0-flash"
            url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
                   f"{model_name}:generateContent?key={api_key}")
            headers = {"Content-Type": "application/json"}
            body = json.dumps({
                "contents": [{"role": "user",
                              "parts": [{"text": system_prompt + "\n\n" + prompt}]}]
            })

        self.add_bubble("...soch raha hu...", is_user=False, persist=False)

        UrlRequest(
            url, req_body=body, req_headers=headers,
            on_success=lambda req, result: self.on_ai_success(result, provider),
            on_failure=lambda req, result: self.on_ai_error(result),
            on_error=lambda req, error: self.on_ai_error(str(error)),
        )

    def call_ai_with_image(self, prompt, image_path, settings):
        api_key = settings.get("api_key", "")
        model = settings.get("model") or "gemini-2.0-flash"
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{model}:generateContent?key={api_key}")
        headers = {"Content-Type": "application/json"}
        img_b64 = image_to_base64(image_path)
        mime = "image/png" if image_path.lower().endswith(".png") else "image/jpeg"
        body = json.dumps({
            "contents": [{
                "role": "user",
                "parts": [
                    {"text": prompt},
                    {"inline_data": {"mime_type": mime, "data": img_b64}},
                ],
            }]
        })

        self.add_bubble("...image dekh raha hu...", is_user=False, persist=False)

        UrlRequest(
            url, req_body=body, req_headers=headers,
            on_success=lambda req, result: self.on_ai_success(result, "Gemini"),
            on_failure=lambda req, result: self.on_ai_error(result),
            on_error=lambda req, error: self.on_ai_error(str(error)),
        )

    @mainthread
    def on_ai_success(self, result, provider):
        if self.chat_box.children:
            self.chat_box.remove_widget(self.chat_box.children[0])
        try:
            if provider == "Groq":
                text = result["choices"][0]["message"]["content"]
            else:
                text = result["candidates"][0]["content"]["parts"][0]["text"]
        except Exception:
            text = f"Response parse nahi hua: {result}"
        self.add_bubble(text, is_user=False)

    @mainthread
    def on_ai_error(self, error):
        if self.chat_box.children:
            self.chat_box.remove_widget(self.chat_box.children[0])
        self.add_bubble(f"⚠ Error: {error}", is_user=False)


class SettingsScreen(Screen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        layout = BoxLayout(orientation="vertical", padding=dp(16), spacing=dp(12))

        layout.add_widget(Label(text="Provider chuno:", size_hint_y=None, height=dp(30), color=(1,1,1,1)))
        self.provider_spinner = Spinner(text="Groq", values=("Groq", "Gemini"),
                                         size_hint_y=None, height=dp(44))
        layout.add_widget(self.provider_spinner)

        layout.add_widget(Label(text="API Key:", size_hint_y=None, height=dp(30), color=(1,1,1,1)))
        self.key_input = TextInput(multiline=False, password=True, size_hint_y=None, height=dp(44),
                                    foreground_color=(1,1,1,1))
        layout.add_widget(self.key_input)

        layout.add_widget(Label(text="Model naam (optional):", size_hint_y=None, height=dp(30), color=(1,1,1,1)))
        self.model_input = TextInput(multiline=False, size_hint_y=None, height=dp(44),
                                      foreground_color=(1,1,1,1))
        layout.add_widget(self.model_input)

        save_btn = Button(text="Save", size_hint_y=None, height=dp(48))
        save_btn.bind(on_release=self.save)
        layout.add_widget(save_btn)

        back_btn = Button(text="Back to Chat", size_hint_y=None, height=dp(48))
        back_btn.bind(on_release=lambda a: setattr(self.manager, "current", "chat"))
        layout.add_widget(back_btn)

        self.status = Label(text="", size_hint_y=None, height=dp(30), color=(1,1,1,1))
        layout.add_widget(self.status)
        self.add_widget(layout)

    def on_pre_enter(self, *a):
        s = load_settings()
        self.provider_spinner.text = s.get("provider", "Groq")
        self.key_input.text = s.get("api_key", "")
        self.model_input.text = s.get("model", "")

    def save(self, *a):
        save_settings({
            "provider": self.provider_spinner.text,
            "api_key": self.key_input.text.strip(),
            "model": self.model_input.text.strip(),
        })
        self.status.text = "Saved!"


class CodeAssistantApp(App):
    def build(self):
        sm = ScreenManager()
        sm.add_widget(ChatScreen(name="chat"))
        sm.add_widget(SettingsScreen(name="settings"))
        return sm


if __name__ == "__main__":
    CodeAssistantApp().run()

import json
import os
from kivy.app import App
from kivy.uix.screenmanager import ScreenManager, Screen
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.textinput import TextInput
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.spinner import Spinner
from kivy.core.clipboard import Clipboard
from kivy.network.urlrequest import UrlRequest
from kivy.clock import mainthread
from kivy.metrics import dp

CONFIG_FILE = "settings.json"


def get_config_path():
    app = App.get_running_app()
    return os.path.join(app.user_data_dir, CONFIG_FILE)


def load_settings():
    path = get_config_path()
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {"provider": "Groq", "api_key": "", "model": "llama-3.3-70b-versatile"}


def save_settings(data):
    with open(get_config_path(), "w") as f:
        json.dump(data, f)


class ChatBubble(BoxLayout):
    def __init__(self, text, is_user=False, **kwargs):
        super().__init__(orientation="vertical", size_hint_y=None,
                          padding=dp(8), spacing=dp(4), **kwargs)
        self.bind(minimum_height=self.setter("height"))

        label = Label(text=text, size_hint_y=None, halign="left", valign="top")
        label.bind(texture_size=lambda inst, val: setattr(label, "height", val[1]))
        label.bind(width=lambda inst, val: setattr(label, "text_size", (val, None)))
        self.add_widget(label)

        if not is_user:
            copy_btn = Button(text="Copy Code", size_hint_y=None, height=dp(36))
            copy_btn.bind(on_release=lambda inst: Clipboard.copy(text))
            self.add_widget(copy_btn)


class ChatScreen(Screen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        root = BoxLayout(orientation="vertical")

        top_bar = BoxLayout(size_hint_y=None, height=dp(48), padding=dp(4), spacing=dp(4))
        top_bar.add_widget(Label(text="Code Assistant"))
        settings_btn = Button(text="Settings", size_hint_x=None, width=dp(100))
        settings_btn.bind(on_release=lambda a: setattr(self.manager, "current", "settings"))
        top_bar.add_widget(settings_btn)
        root.add_widget(top_bar)

        self.scroll = ScrollView()
        self.chat_box = BoxLayout(orientation="vertical", size_hint_y=None,
                                   spacing=dp(8), padding=dp(8))
        self.chat_box.bind(minimum_height=self.chat_box.setter("height"))
        self.scroll.add_widget(self.chat_box)
        root.add_widget(self.scroll)

        input_bar = BoxLayout(size_hint_y=None, height=dp(56), padding=dp(4), spacing=dp(4))
        self.input = TextInput(hint_text="App/website/game ka code maango...", multiline=False)
        self.input.bind(on_text_validate=self.send_message)
        send_btn = Button(text="Send", size_hint_x=None, width=dp(80))
        send_btn.bind(on_release=self.send_message)
        input_bar.add_widget(self.input)
        input_bar.add_widget(send_btn)
        root.add_widget(input_bar)

        self.add_widget(root)

    def add_bubble(self, text, is_user=False):
        bubble = ChatBubble(text=text, is_user=is_user, width=self.chat_box.width)
        self.chat_box.add_widget(bubble)
        self.scroll.scroll_y = 0

    def send_message(self, *a):
        text = self.input.text.strip()
        if not text:
            return
        self.input.text = ""
        self.add_bubble(f"You: {text}", is_user=True)
        settings = load_settings()
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
        else:  # Gemini
            model_name = model or "gemini-2.0-flash"
            url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
                   f"{model_name}:generateContent?key={api_key}")
            headers = {"Content-Type": "application/json"}
            body = json.dumps({
                "contents": [{"role": "user",
                              "parts": [{"text": system_prompt + "\n\n" + prompt}]}]
            })

        self.add_bubble("...soch raha hu...", is_user=False)

        UrlRequest(
            url, req_body=body, req_headers=headers,
            on_success=lambda req, result: self.on_ai_success(result, provider),
            on_failure=lambda req, result: self.on_ai_error(result),
            on_error=lambda req, error: self.on_ai_error(str(error)),
        )

    @mainthread
    def on_ai_success(self, result, provider):
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
        self.add_bubble(f"⚠ Error: {error}", is_user=False)


class SettingsScreen(Screen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        layout = BoxLayout(orientation="vertical", padding=dp(16), spacing=dp(12))

        layout.add_widget(Label(text="Provider chuno:", size_hint_y=None, height=dp(30)))
        self.provider_spinner = Spinner(text="Groq", values=("Groq", "Gemini"),
                                         size_hint_y=None, height=dp(44))
        layout.add_widget(self.provider_spinner)

        layout.add_widget(Label(text="API Key:", size_hint_y=None, height=dp(30)))
        self.key_input = TextInput(multiline=False, password=True, size_hint_y=None, height=dp(44))
        layout.add_widget(self.key_input)

        layout.add_widget(Label(text="Model naam (optional):", size_hint_y=None, height=dp(30)))
        self.model_input = TextInput(multiline=False, size_hint_y=None, height=dp(44))
        layout.add_widget(self.model_input)

        save_btn = Button(text="Save", size_hint_y=None, height=dp(48))
        save_btn.bind(on_release=self.save)
        layout.add_widget(save_btn)

        back_btn = Button(text="Back to Chat", size_hint_y=None, height=dp(48))
        back_btn.bind(on_release=lambda a: setattr(self.manager, "current", "chat"))
        layout.add_widget(back_btn)

        self.status = Label(text="", size_hint_y=None, height=dp(30))
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

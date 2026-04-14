# TeleSiphon (Cloud-Native UserBot)

**TeleSiphon** is an interactive Telegram UserBot designed to mirror media from restricted groups to a destination chat.

## 🚀 Features

- **Siphon-Mirror Workflow**: Fetches restricted media, caches it temporarily, re-uploads it, and cleans up immediately.
- **Bypass Restrictions**: Directly interfaces with MTProto to bypass "Restrict Saving Content" settings.
- **Rich Text Preservation**: Maintains bold, italics, and links in captions.
- **Interactive Buttons**: Manage everything via Telegram Inline Keyboards.
- **Cloud-Ready**: 
    - **String Session**: No local `.session` file needed.
    - **Keep-Alive**: Built-in FastAPI server for Render health checks.
    - **Ephemeral Storage**: Uses `/tmp/` for processing.
- **Real-time Progress**: Displays a live progress bar by editing its own status message.

## 🛠 Setup

1.  **Dependencies**:
    ```bash
    pip install -r requirements.txt
    ```
2.  **Environment Variables (`.env`)**:
    ```env
    API_ID=your_id
    API_HASH=your_hash
    TELEGRAM_STRING_SESSION=your_string_session
    DEFAULT_SOURCE=source_chat_id
    DEFAULT_DESTINATION=destination_chat_id
    PORT=8000
    ```
3. **Start Command**:
    ```bash
    python app.py
    ```

## 🕹 Usage

1.  Send `.siphon` in any chat where the UserBot is active.
2.  Use the **Control Panel** to:
    - Confirm or change your Source/Destination.
    - Select Media Types (Voices, Videos, etc.).
    - Select the search limit.
3.  Watch the progress bar as TeleSiphon mirrors the content.

## ⚠️ Disclaimer
This tool is for educational purposes only. Always respect Telegram's Terms of Service and user privacy.

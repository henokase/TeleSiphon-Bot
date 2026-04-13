# TeleSiphon

**TeleSiphon** is a Python-based utility to download and forward media from restricted Telegram groups using the MTProto API.

## Features

- **Bypass Restrictions**: Directly interfaces with MTProto to download media from groups where "Restrict Saving Content" is enabled.
- **Forwarding (Re-upload)**: Bypasses forwarding restrictions by downloading the file and re-uploading it as a brand-new message.
- **Rich Text Preservation**: Maintains bold, italics, and links in captions when forwarding.
- **Interactive Menu**: Choose between downloading, forwarding, and specific media types (Voices, Audios, Videos, Images).
- **Progress Tracking**: Real-time console progress bars using `tqdm`.
- **Integrity Verification**: Verifies downloaded file sizes against Telegram metadata.

## Setup

1.  **Clone the Repository**:
    ```bash
    git clone https://github.com/yourusername/TeleSiphon.git
    cd TeleSiphon
    ```

2.  **Install Dependencies**:
    ```bash
    python -m venv venv
    .\venv\Scripts\activate  # Windows
    pip install -r requirements.txt
    ```

3.  **Configure `.env`**:
    Create a `.env` file in the root directory:
    ```env
    API_ID=your_api_id
    API_HASH=your_api_hash
    TARGET_CHAT_ID=source_group_id
    FORWARD_CHAT_ID=destination_group_id
    DOWNLOAD_LIMIT=0  # 0 for no limit
    ```

## Usage

Simply run:
```bash
python main.py
```

Follow the interactive prompts to select your source chat, action, and media types.

## Disclaimer

This tool is for educational purposes only. Always respect Telegram's Terms of Service and the privacy of group members.

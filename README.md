# TeleSiphon

A powerful Telegram UserBot that mirrors media between chats with advanced filtering and album support.

## Features

- **Multiple Source Modes**: Siphon media from chats, specific message links, or date ranges
- **Forum Support**: Compatible with Telegram forums - select specific topics to mirror
- **Media Types**: Voices, audios, videos, photos, and documents
- **Album Handling**: Intelligently groups and sends photo albums
- **Parallel Uploads**: High-performance chunked MTProto uploads
- **Date Filtering**: Mirror media within specific date ranges
- **Interactive Menu**: User-friendly command-driven interface

## Requirements

- Python 3.9+
- Telegram API credentials (API_ID, API_HASH)
- A Telegram string session

## Installation

```bash
# Clone the repository
git clone https://github.com/yourusername/TeleSiphon.git
cd TeleSiphon

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Linux/Mac
# venv\Scripts\activate  # Windows

# Install dependencies
pip install -r requirements.txt
```

## Configuration

Create a `.env` file:

```env
API_ID=your_api_id
API_HASH=your_api_hash
TELEGRAM_STRING_SESSION=your_string_session

# Optional defaults
DEFAULT_SOURCE=-1001234567890
DEFAULT_DESTINATION=-1001234567890
DOWNLOAD_LIMIT=5
```

### Getting Credentials

1. Get API_ID and API_HASH from [my.telegram.org](https://my.telegram.org)
2. Generate a string session using `python string_session_generator.py`

## Usage

```bash
# Run the bot
python app.py
```

### Commands

Once running, open a chat with your bot and use:

| Command | Description |
|---------|-------------|
| `.siphon` | Launch the interactive menu |
| `X` | Exit current session |

### Interactive Workflow

1. Send `.siphon` to open the control panel
2. Choose an action:
   - **Siphon Media** - Fetch from a source chat
   - **Siphon by Message Link** - Specific messages
   - **Siphon by Date Range** - Date-filtered media
3. Select media type and limit
4. Confirm to start mirroring

### Date Range Formats

- **Single date**: `2024-01-15` (only that day)
- **Range**: `2024-01-01,2024-01-15`
- **To now**: `2024-01-01,`
- **Short**: `MM-DD` or `DD`

## Architecture

```
TeleSiphon/
├── app.py                    # FastAPI entry point & lifecycle
├── bot_client.py             # Telethon client initialization
├── main.py                   # Core mirroring logic & state machine
├── downloader.py             # Media download manager
├── telethon_utils.py          # Parallel upload utilities
└── string_session_generator.py  # Session generation tool
```

## License

MIT License - See [LICENSE](LICENSE)
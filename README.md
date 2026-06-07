# Auto-Playlist

Create a Spotify playlist from a simple `playlist.json` file.

This project is designed for a workflow where you ask an LLM for a structured playlist, save the result as JSON, and run a local Python script that creates the playlist in your Spotify account.

## What it does

* Reads `playlist.json`
* Authenticates with Spotify
* Creates a new playlist
* Searches Spotify for each requested track only when no cached ID/URI is available
* Adds matched tracks to the playlist
* Prints unresolved tracks so you can fix them manually
* Exports Spotify playlists into an agent-friendly local database
* Reuses unchanged playlist exports by comparing Spotify `snapshot_id` values

## Important limitation

Spotify's Web API can create playlists and add tracks, but it cannot place playlists into Spotify folders.

Do not include fields like:

```json
"folder_path": "Some Folder"
```

If you want the playlist inside a folder, create it with this script and then move it manually in the Spotify desktop app.

## Project files

Recommended folder layout:

```text
Auto-Playlist/
├── create_playlist.py
├── export_spotify.py
├── playlist_agent.py
├── playlist.json
├── requirements.txt
├── .env
├── .env.example
└── README.md
```

## Requirements

Use Python 3.10 or newer.

Install dependencies:

```bash
python -m pip install -r requirements.txt
```

Your `requirements.txt` should contain:

```txt
python-dotenv
requests
spotipy
```

## Spotify setup

1. Open the Spotify Developer Dashboard.
2. Create a new app.
3. Copy the app's Client ID and Client Secret.
4. Add this Redirect URI to the app:

```text
http://127.0.0.1:8888/callback
```

5. Save the app settings.
6. If your app is in development mode, add your Spotify account in the app's Users Management section.

## Environment variables

Create a `.env` file:

```env
SPOTIPY_CLIENT_ID=your_client_id_here
SPOTIPY_CLIENT_SECRET=your_client_secret_here
SPOTIPY_REDIRECT_URI=http://127.0.0.1:8888/callback
```

Do not commit `.env` to GitHub.

Recommended `.gitignore`:

```gitignore
.env
.venv/
__pycache__/
.cache-autoplaylist
```

## Playlist JSON format

Create `playlist.json` in the same folder as `create_playlist.py`.

Example:

```json
{
  "playlist_name": "Auto-Playlist-001",
  "public": false,
  "description": "Dark melodic bass, djent architecture, orchestral pressure, math-rock glasswork, and black-lit doors into stranger rooms.",
  "tracks": [
    {
      "title": "Obsidian Vortex",
      "artists": ["ATLiens"]
    },
    {
      "title": "Bleed",
      "artists": ["Meshuggah"]
    },
    {
      "title": "Language I: Intuition",
      "artists": ["The Contortionist"]
    }
  ]
}
```

Required fields:

```json
"playlist_name"
"tracks"
```

Optional fields:

```json
"public"
"description"
```

Each track should have:

```json
"title"
"artists"
```

`artists` can be a list or a single string, but a list is preferred.

## How to ask an LLM for a playlist JSON

Use a prompt like this:

```text
Create a Spotify playlist JSON for a playlist with the following vibe:

[describe the vibe]

Return only valid JSON. Do not include markdown. Do not include comments.

Use this exact schema:

{
  "playlist_name": "string",
  "public": false,
  "description": "short description",
  "tracks": [
    {
      "title": "song title",
      "artists": ["artist name"]
    }
  ]
}

Rules:
- Include 25 tracks.
- Use real released songs.
- Prefer songs likely to exist on Spotify.
- Use exact artist names when possible.
- Do not include album names.
- Do not include folder_path or playlist folder fields.
```

## Run the script

Activate your virtual environment:

```bash
source .venv/bin/activate
```

Run:

```bash
python create_playlist.py
```

You can also pass a specific generated file:

```bash
python create_playlist.py path/to/playlist.json
```

The first run will open a browser window asking you to authorize the Spotify app.

After authorization, the script will create the playlist, search each track, add matched tracks, and print any unresolved tracks.

Resolved search results are cached in `exports/track_resolution_cache.json`. If a future `playlist.json` includes `spotify_uri` or `spotify_id`, the script uses that directly and does not search Spotify for that track.

## Reset Spotify authorization

If you change scopes, credentials, or Spotify app settings, delete the local token cache:

```bash
rm -f .cache-autoplaylist
```

Then run again:

```bash
python create_playlist.py
```

## Troubleshooting

### `Missing SPOTIPY_CLIENT_ID`

Your `.env` file is missing or not in the same folder as `create_playlist.py`.

### `ModuleNotFoundError`

Install dependencies inside your project virtual environment:

```bash
python -m pip install -r requirements.txt
```

### `403 Forbidden`

Check Spotify Developer Dashboard:

* Your Spotify account is added under Users Management.
* Your Redirect URI exactly matches `.env`.
* You deleted `.cache-autoplaylist` after changing app settings.

### `400 Bad Request`

Check your `playlist.json`.

Common cause:

```json
"folder_path": "Some Folder"
```

Remove it. Spotify folders are not supported by the playlist creation API.

### Some songs are unresolved

The script uses title and artist matching. If a song fails:

* Check spelling.
* Use the official Spotify artist name.
* Remove featured artists from the title.
* Use the main artist in the `artists` field.



## Export database

Export one playlist:

```bash
python export_spotify.py playlist "https://open.spotify.com/playlist/YOUR_PLAYLIST_ID"
```

Export every playlist visible to your account:

```bash
python export_spotify.py all-playlists
```

Export liked songs:

```bash
python export_spotify.py liked
```

Export everything:

```bash
python export_spotify.py all
```

It creates and updates:

```text
exports/
  spotify_database.json
  liked_songs.json
  playlists_index.json
  playlists/
    Playlist_Name_abc123.json
```

The first export fetches playlist tracks and builds `exports/spotify_database.json`. Later exports fetch the playlist index, compare each playlist's `snapshot_id`, and reuse cached tracks for unchanged playlists. If only metadata changed, the database row is updated without re-fetching the playlist's track pages.

## Matrix agent workflow

The repository now has a minimal CLI surface for a Matrix bot or LLM agent. The Matrix process only needs to convert text messages into these local commands.

Refresh the database manually:

```bash
python playlist_agent.py refresh
```

Ask for tracks from an inspiration playlist by name, URL, URI, or ID:

```bash
python playlist_agent.py inspiration "Chrome Cathedral" --limit 50
```

If the playlist is not in `exports/spotify_database.json`, this command refreshes the playlist database once, then tries the lookup again.

Create an agent request file from a Matrix message:

```bash
python playlist_agent.py request "make me a heavy glassy bass playlist" --inspired-by "Chrome Cathedral"
```

This writes `exports/agent_playlist_request.json`, containing the user prompt, inspiration playlist metadata, and compact track rows. The agent should use that file to generate `playlist.json` in the schema documented above.

Create the playlist after the agent writes `playlist.json`:

```bash
python playlist_agent.py create playlist.json
```

Recommended Matrix command contract:

```text
!playlist refresh
  -> python playlist_agent.py refresh

!playlist inspire <playlist name or URL>
  -> python playlist_agent.py inspiration "<playlist name or URL>"

!playlist create inspired-by "<playlist name or URL>" <request text>
  -> python playlist_agent.py request "<request text>" --inspired-by "<playlist name or URL>"
  -> agent writes playlist.json
  -> python playlist_agent.py create playlist.json
```

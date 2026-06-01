import os
import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
import spotipy
from spotipy.oauth2 import SpotifyOAuth


load_dotenv()

SCOPE = "playlist-read-private playlist-read-collaborative user-library-read"
EXPORT_DIR = Path("exports")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_filename(text: str) -> str:
    text = re.sub(r"[^\w\s.-]", "", text, flags=re.UNICODE)
    text = re.sub(r"\s+", "_", text.strip())
    return text[:80] or "spotify_export"


def playlist_id_from_input(value: str) -> str:
    value = value.strip()

    if value.startswith("spotify:playlist:"):
        return value.split(":")[-1]

    if "open.spotify.com" in value:
        parsed = urlparse(value)
        parts = [part for part in parsed.path.split("/") if part]
        if "playlist" in parts:
            index = parts.index("playlist")
            return parts[index + 1]

    return value


def get_spotify_client():
    client_id = os.getenv("SPOTIPY_CLIENT_ID")
    client_secret = os.getenv("SPOTIPY_CLIENT_SECRET")
    redirect_uri = os.getenv("SPOTIPY_REDIRECT_URI")

    missing = [
        name for name, value in {
            "SPOTIPY_CLIENT_ID": client_id,
            "SPOTIPY_CLIENT_SECRET": client_secret,
            "SPOTIPY_REDIRECT_URI": redirect_uri,
        }.items()
        if not value
    ]

    if missing:
        raise RuntimeError(f"Missing required .env values: {', '.join(missing)}")

    return spotipy.Spotify(
        auth_manager=SpotifyOAuth(
            client_id=client_id.strip(),
            client_secret=client_secret.strip(),
            redirect_uri=redirect_uri.strip(),
            scope=SCOPE,
            cache_path=".cache-spotify-export",
            open_browser=True,
        )
    )


def write_json(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )
    print(f"Wrote: {path}")


def simplify_track(track: dict, added_at=None, added_by=None, is_local=False) -> dict | None:
    if not track:
        return None

    if track.get("type") != "track":
        return {
            "item_type": track.get("type", "unknown"),
            "title": track.get("name"),
            "spotify_uri": track.get("uri"),
            "spotify_id": track.get("id"),
            "added_at": added_at,
            "added_by": added_by,
            "is_local": is_local,
            "note": "Non-track item. This may be a podcast episode or another Spotify item type."
        }

    album = track.get("album") or {}

    return {
        "item_type": "track",
        "title": track.get("name"),
        "artists": [artist.get("name") for artist in track.get("artists", [])],
        "spotify_uri": track.get("uri"),
        "spotify_id": track.get("id"),
        "spotify_url": (track.get("external_urls") or {}).get("spotify"),
        "album": album.get("name"),
        "album_type": album.get("album_type"),
        "album_release_date": album.get("release_date"),
        "duration_ms": track.get("duration_ms"),
        "explicit": track.get("explicit"),
        "popularity": track.get("popularity"),
        "added_at": added_at,
        "added_by": added_by,
        "is_local": is_local
    }


def fetch_playlist_tracks(sp, playlist_id: str) -> list[dict]:
    tracks = []
    offset = 0
    limit = 50

    while True:
        page = sp.playlist_items(
            playlist_id,
            limit=limit,
            offset=offset,
            additional_types=("track",)
        )

        for item in page.get("items", []):
            added_by = item.get("added_by") or {}
            simple = simplify_track(
                item.get("track"),
                added_at=item.get("added_at"),
                added_by=added_by.get("id"),
                is_local=item.get("is_local", False)
            )

            if simple:
                tracks.append(simple)

        if not page.get("next"):
            break

        offset += limit

    return tracks


def export_playlist(sp, playlist_input: str) -> dict:
    playlist_id = playlist_id_from_input(playlist_input)
    playlist = sp.playlist(playlist_id)
    tracks = fetch_playlist_tracks(sp, playlist_id)

    output = {
        "export_type": "spotify_playlist",
        "exported_at": now_iso(),
        "playlist_name": playlist.get("name"),
        "description": playlist.get("description"),
        "public": playlist.get("public"),
        "collaborative": playlist.get("collaborative"),
        "spotify_playlist_id": playlist.get("id"),
        "spotify_uri": playlist.get("uri"),
        "spotify_url": (playlist.get("external_urls") or {}).get("spotify"),
        "owner": {
            "id": (playlist.get("owner") or {}).get("id"),
            "display_name": (playlist.get("owner") or {}).get("display_name")
        },
        "track_count": len(tracks),
        "tracks": tracks
    }

    filename = f"{safe_filename(output['playlist_name'])}_{playlist_id}.json"
    write_json(EXPORT_DIR / "playlists" / filename, output)
    return output


def fetch_all_user_playlists(sp) -> list[dict]:
    playlists = []
    offset = 0
    limit = 50

    while True:
        page = sp.current_user_playlists(limit=limit, offset=offset)

        for playlist in page.get("items", []):
            playlists.append({
                "name": playlist.get("name"),
                "spotify_playlist_id": playlist.get("id"),
                "spotify_uri": playlist.get("uri"),
                "spotify_url": (playlist.get("external_urls") or {}).get("spotify"),
                "owner": {
                    "id": (playlist.get("owner") or {}).get("id"),
                    "display_name": (playlist.get("owner") or {}).get("display_name")
                },
                "public": playlist.get("public"),
                "collaborative": playlist.get("collaborative"),
                "track_total": (playlist.get("tracks") or {}).get("total")
            })

        if not page.get("next"):
            break

        offset += limit

    return playlists


def export_all_playlists(sp):
    current_user = sp.current_user()
    current_user_id = current_user["id"]

    playlists = fetch_all_user_playlists(sp)

    index = {
        "export_type": "spotify_playlist_index",
        "exported_at": now_iso(),
        "current_user_id": current_user_id,
        "playlist_count": len(playlists),
        "playlists": playlists
    }

    write_json(EXPORT_DIR / "playlists_index.json", index)

    skipped = []
    failures = []

    for playlist in playlists:
        playlist_id = playlist["spotify_playlist_id"]
        playlist_name = playlist["name"]
        owner_id = (playlist.get("owner") or {}).get("id")
        collaborative = playlist.get("collaborative", False)

        can_read_items = owner_id == current_user_id or collaborative

        if not can_read_items:
            skipped.append({
                "playlist_name": playlist_name,
                "spotify_playlist_id": playlist_id,
                "owner_id": owner_id,
                "reason": "Spotify API only exposes playlist items for playlists owned by the current user or collaborative playlists."
            })
            print(f"SKIPPED: {playlist_name} — not owned/collaborative")
            continue

        try:
            print(f"Exporting playlist: {playlist_name}")
            export_playlist(sp, playlist_id)
        except Exception as error:
            failures.append({
                "playlist_name": playlist_name,
                "spotify_playlist_id": playlist_id,
                "error": str(error)
            })
            print(f"FAILED: {playlist_name} — {error}")

    if skipped:
        write_json(EXPORT_DIR / "playlist_export_skipped.json", {
            "exported_at": now_iso(),
            "skipped": skipped
        })

    if failures:
        write_json(EXPORT_DIR / "playlist_export_failures.json", {
            "exported_at": now_iso(),
            "failures": failures
        })


def export_liked_songs(sp):
    tracks = []
    offset = 0
    limit = 50

    while True:
        page = sp.current_user_saved_tracks(limit=limit, offset=offset)

        for item in page.get("items", []):
            simple = simplify_track(
                item.get("track"),
                added_at=item.get("added_at"),
                added_by="current_user",
                is_local=False
            )

            if simple:
                tracks.append(simple)

        if not page.get("next"):
            break

        offset += limit

    output = {
        "export_type": "spotify_liked_songs",
        "exported_at": now_iso(),
        "playlist_name": "Liked Songs",
        "description": "Spotify saved tracks exported from the current user's library.",
        "public": False,
        "track_count": len(tracks),
        "tracks": tracks
    }

    write_json(EXPORT_DIR / "liked_songs.json", output)
    return output


def main():
    parser = argparse.ArgumentParser(
        description="Export Spotify playlists and liked songs to agent-friendly JSON."
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    playlist_parser = subparsers.add_parser("playlist")
    playlist_parser.add_argument(
        "playlist",
        help="Spotify playlist URL, URI, or raw playlist ID."
    )

    subparsers.add_parser("all-playlists")
    subparsers.add_parser("liked")
    subparsers.add_parser("all")

    args = parser.parse_args()
    sp = get_spotify_client()

    if args.command == "playlist":
        export_playlist(sp, args.playlist)

    elif args.command == "all-playlists":
        export_all_playlists(sp)

    elif args.command == "liked":
        export_liked_songs(sp)

    elif args.command == "all":
        export_all_playlists(sp)
        export_liked_songs(sp)


if __name__ == "__main__":
    main()
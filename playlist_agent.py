import argparse
import json
import re
from pathlib import Path

import create_playlist
import export_spotify


DEFAULT_REQUEST_PATH = Path("exports") / "agent_playlist_request.json"


def normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def read_database() -> dict:
    return export_spotify.read_json(export_spotify.DATABASE_PATH, {
        "export_type": "spotify_database",
        "playlists": []
    })


def compact_track(track: dict) -> dict:
    return {
        "title": track.get("title"),
        "artists": track.get("artists") or [],
        "spotify_uri": track.get("spotify_uri"),
        "spotify_id": track.get("spotify_id"),
        "album": track.get("album"),
        "duration_ms": track.get("duration_ms"),
        "explicit": track.get("explicit"),
    }


def playlist_matches(playlist: dict, reference: str) -> bool:
    reference_id = export_spotify.playlist_id_from_input(reference)
    normalized_reference = normalize(reference)

    return any([
        playlist.get("spotify_playlist_id") == reference_id,
        playlist.get("spotify_uri") == reference,
        playlist.get("spotify_url") == reference,
        normalize(playlist.get("playlist_name") or "") == normalized_reference,
    ])


def find_playlist(database: dict, reference: str) -> dict | None:
    playlists = database.get("playlists", [])
    exact = [playlist for playlist in playlists if playlist_matches(playlist, reference)]

    if exact:
        return max(exact, key=lambda item: item.get("track_count") or 0)

    normalized_reference = normalize(reference)
    partial = [
        playlist for playlist in playlists
        if normalized_reference and normalized_reference in normalize(playlist.get("playlist_name") or "")
    ]

    if partial:
        return max(partial, key=lambda item: item.get("track_count") or 0)

    return None


def refresh_database(include_liked: bool = False) -> dict:
    sp = export_spotify.get_spotify_client()
    export_spotify.export_all_playlists(sp)

    if include_liked:
        export_spotify.export_liked_songs(sp)

    return read_database()


def get_inspiration(reference: str, *, limit: int, refresh_missing: bool = True) -> dict:
    database = read_database()
    playlist = find_playlist(database, reference)

    needs_refresh = not playlist or (
        playlist.get("spotify_track_total", 0) and not playlist.get("tracks")
    )
    if needs_refresh and refresh_missing:
        database = refresh_database()
        playlist = find_playlist(database, reference)

    if not playlist:
        raise RuntimeError(
            f"Playlist not found in {export_spotify.DATABASE_PATH}. Run: python export_spotify.py all-playlists"
        )

    tracks = [compact_track(track) for track in playlist.get("tracks", [])[:limit]]
    return {
        "playlist_name": playlist.get("playlist_name"),
        "spotify_playlist_id": playlist.get("spotify_playlist_id"),
        "spotify_url": playlist.get("spotify_url"),
        "snapshot_id": playlist.get("snapshot_id"),
        "track_count": playlist.get("track_count"),
        "tracks_returned": len(tracks),
        "tracks": tracks,
    }


def write_generation_request(prompt: str, reference: str, output: Path, limit: int) -> dict:
    inspiration = get_inspiration(reference, limit=limit)
    request = {
        "task": "create_spotify_playlist_json",
        "user_prompt": prompt,
        "inspired_by": inspiration,
        "output_file": "playlist.json",
        "required_schema": {
            "playlist_name": "string",
            "public": False,
            "description": "short description",
            "tracks": [
                {
                    "title": "song title",
                    "artists": ["artist name"],
                    "spotify_uri": "preferred when selecting a track already present in the database"
                }
            ]
        },
        "rules": [
            "Return only valid JSON for playlist.json.",
            "Use real released songs likely to exist on Spotify.",
            "Prefer spotify_uri values from the database when reusing inspiration tracks.",
            "Do not include folder_path or Spotify folder fields."
        ]
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(request, indent=2, ensure_ascii=False), encoding="utf-8")
    return request


def print_json(data) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="CLI tool for Matrix agents to refresh, inspect, and create Spotify playlists."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    refresh_parser = subparsers.add_parser("refresh")
    refresh_parser.add_argument("--liked", action="store_true", help="Also refresh liked songs.")

    inspiration_parser = subparsers.add_parser("inspiration")
    inspiration_parser.add_argument("playlist", help="Playlist name, URL, URI, or ID.")
    inspiration_parser.add_argument("--limit", type=int, default=50)
    inspiration_parser.add_argument("--no-refresh", action="store_true")

    request_parser = subparsers.add_parser("request")
    request_parser.add_argument("prompt", help="User playlist request from Matrix.")
    request_parser.add_argument("--inspired-by", required=True, help="Playlist name, URL, URI, or ID.")
    request_parser.add_argument("--limit", type=int, default=50)
    request_parser.add_argument("--output", type=Path, default=DEFAULT_REQUEST_PATH)

    create_parser = subparsers.add_parser("create")
    create_parser.add_argument("playlist_json", nargs="?", default="playlist.json")

    args = parser.parse_args()

    if args.command == "refresh":
        database = refresh_database(include_liked=args.liked)
        print_json({
            "database": str(export_spotify.DATABASE_PATH),
            "playlist_count": database.get("playlist_count"),
            "updated_at": database.get("updated_at"),
        })

    elif args.command == "inspiration":
        print_json(get_inspiration(
            args.playlist,
            limit=args.limit,
            refresh_missing=not args.no_refresh,
        ))

    elif args.command == "request":
        request = write_generation_request(
            args.prompt,
            args.inspired_by,
            args.output,
            args.limit,
        )
        print_json({
            "request_file": str(args.output),
            "playlist_name": request["inspired_by"].get("playlist_name"),
            "tracks_returned": request["inspired_by"].get("tracks_returned"),
        })

    elif args.command == "create":
        create_playlist.run(args.playlist_json)


if __name__ == "__main__":
    main()

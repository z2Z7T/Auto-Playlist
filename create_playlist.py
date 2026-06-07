import json
import os
import re
import argparse
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv
from spotipy.oauth2 import SpotifyOAuth


load_dotenv()

SPOTIFY_API = "https://api.spotify.com/v1"
TOKEN_CACHE = ".cache-autoplaylist"
SCOPE = "playlist-modify-private playlist-modify-public user-read-private"
TRACK_CACHE_PATH = Path("exports") / "track_resolution_cache.json"


class SpotifyApiError(RuntimeError):
    pass


def required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing {name}. Check your .env file.")
    return value


def normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def normalize_key(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "1", "public"}
    return bool(value)


def load_playlist(path: str = "playlist.json") -> dict:
    playlist_path = Path(path)

    if not playlist_path.exists():
        raise FileNotFoundError(f"No {path} found in this folder.")

    data = json.loads(playlist_path.read_text(encoding="utf-8"))

    if "playlist_name" not in data:
        raise ValueError('playlist.json must contain "playlist_name".')

    if "tracks" not in data:
        raise ValueError('playlist.json must contain "tracks".')

    if not isinstance(data["tracks"], list):
        raise ValueError('"tracks" must be a list.')

    forbidden_fields = {"folder", "folder_path", "playlist_folder"}
    found_forbidden = forbidden_fields.intersection(data)

    if found_forbidden:
        fields = ", ".join(sorted(found_forbidden))
        raise ValueError(
            f"Remove unsupported field(s) from playlist.json: {fields}. "
            "Spotify's Web API can create playlists, but it cannot place them in folders."
        )

    return data


def load_track_cache() -> dict:
    if not TRACK_CACHE_PATH.exists():
        return {}

    return json.loads(TRACK_CACHE_PATH.read_text(encoding="utf-8"))


def write_track_cache(cache: dict) -> None:
    TRACK_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    TRACK_CACHE_PATH.write_text(
        json.dumps(cache, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )


def track_cache_key(track: dict) -> str:
    if track.get("spotify_uri"):
        return f"uri::{track['spotify_uri']}"

    if track.get("spotify_id"):
        return f"id::{track['spotify_id']}"

    title = normalize_key(str(track.get("title", "")))
    artists = track.get("artists", [])

    if isinstance(artists, str):
        artists = [artists]

    artist_key = "|".join(normalize_key(str(artist)) for artist in artists if str(artist).strip())
    return f"{title}::{artist_key}"


def cached_track_to_match(cached: dict) -> dict | None:
    if not cached or not cached.get("spotify_uri"):
        return None

    artists = cached.get("artists") or []
    return {
        "id": cached.get("spotify_id"),
        "uri": cached["spotify_uri"],
        "name": cached.get("title"),
        "artists": [{"name": artist} for artist in artists],
    }


def track_to_cache(match: dict) -> dict:
    return {
        "title": match.get("name"),
        "artists": [artist.get("name") for artist in match.get("artists", [])],
        "spotify_id": match.get("id"),
        "spotify_uri": match.get("uri"),
        "spotify_url": (match.get("external_urls") or {}).get("spotify"),
    }


def get_access_token() -> str:
    auth = SpotifyOAuth(
        client_id=required_env("SPOTIPY_CLIENT_ID"),
        client_secret=required_env("SPOTIPY_CLIENT_SECRET"),
        redirect_uri=required_env("SPOTIPY_REDIRECT_URI"),
        scope=SCOPE,
        open_browser=True,
        show_dialog=False,
        cache_path=TOKEN_CACHE,
    )

    token = auth.get_access_token(check_cache=True, as_dict=False)

    if not isinstance(token, str):
        raise RuntimeError(f"Expected token string, got {type(token)}.")

    return token


def spotify_url(path: str) -> str:
    if path.startswith("http://") or path.startswith("https://"):
        return path

    if not path.startswith("/"):
        path = "/" + path

    return SPOTIFY_API + path


def spotify_request(
    method: str,
    path: str,
    *,
    headers: dict,
    params: dict | None = None,
    payload: dict | None = None,
    action: str = "Spotify request",
    max_retries: int = 5,
) -> dict:
    import time

    for attempt in range(max_retries + 1):
        response = requests.request(
            method=method,
            url=spotify_url(path),
            headers=headers,
            params=params,
            json=payload,
            timeout=30,
        )

        if response.status_code == 429:
            retry_after = int(response.headers.get("Retry-After", "30"))
            wait_time = retry_after + 2

            print()
            print(f"Spotify rate limit hit during: {action}")
            print(f"Waiting {wait_time} seconds before retrying...")
            time.sleep(wait_time)
            continue

        if response.ok:
            if response.text.strip():
                return response.json()
            return {}

        message = [
            "",
            f"Spotify error during: {action}",
            f"Status: {response.status_code}",
            f"URL: {response.url}",
            f"Response body: {response.text}",
        ]

        if payload is not None:
            message.append(f"Request payload: {json.dumps(payload, indent=2)}")

        if response.status_code == 403:
            message.extend(
                [
                    "",
                    "Likely Spotify dashboard issue:",
                    "- Add your Spotify account under the app's Users Management page.",
                    "- Confirm the redirect URI in .env exactly matches the dashboard.",
                    "- Delete the local token cache and reauthorize:",
                    f"  rm -f {TOKEN_CACHE}",
                ]
            )

        raise SpotifyApiError("\n".join(message))

    raise SpotifyApiError(
        f"Spotify kept rate-limiting after {max_retries} retries during: {action}"
    )


def spotify_get(headers: dict, path: str, *, params: dict | None = None, action: str) -> dict:
    return spotify_request(
        "GET",
        path,
        headers=headers,
        params=params,
        action=action,
    )


def spotify_post(headers: dict, path: str, *, payload: dict | None = None, action: str) -> dict:
    return spotify_request(
        "POST",
        path,
        headers=headers,
        payload=payload,
        action=action,
    )


def score_match(candidate: dict, wanted: dict) -> int:
    score = 0

    candidate_title = normalize(candidate["name"])
    wanted_title = normalize(wanted["title"])

    if candidate_title == wanted_title:
        score += 4
    elif wanted_title in candidate_title or candidate_title in wanted_title:
        score += 2

    candidate_artists = [normalize(artist["name"]) for artist in candidate["artists"]]

    wanted_artists_raw = wanted.get("artists", [])
    if isinstance(wanted_artists_raw, str):
        wanted_artists_raw = [wanted_artists_raw]

    wanted_artists = [normalize(artist) for artist in wanted_artists_raw]

    for wanted_artist in wanted_artists:
        if any(wanted_artist == candidate_artist for candidate_artist in candidate_artists):
            score += 3
        elif any(
            wanted_artist in candidate_artist or candidate_artist in wanted_artist
            for candidate_artist in candidate_artists
        ):
            score += 1

    return score


def resolve_track(headers: dict, track: dict, cache: dict) -> dict | None:
    if track.get("spotify_uri"):
        artists = track.get("artists", [])
        if isinstance(artists, str):
            artists = [artists]
        return {
            "id": track.get("spotify_id"),
            "uri": track["spotify_uri"],
            "name": track.get("title"),
            "artists": [{"name": artist} for artist in artists],
        }

    if track.get("spotify_id"):
        track_id = str(track["spotify_id"])
        result = spotify_get(
            headers,
            f"/tracks/{track_id}",
            action=f"get track by id: {track_id}",
        )
        return result if result.get("uri") else None

    key = track_cache_key(track)
    cached = cache.get(key)
    if cached is not None:
        return cached_track_to_match(cached)

    title = str(track.get("title", "")).strip()
    artists = track.get("artists", [])

    if isinstance(artists, str):
        artists = [artists]

    artists = [str(artist).strip() for artist in artists if str(artist).strip()]

    if not title:
        return None

    queries = [f"{title} {' '.join(artists)}".strip()]
    if artists:
        queries.append(f'track:"{title}" artist:"{artists[0]}"')
    queries.append(title)

    best_match = None
    best_score = -1

    for query in dict.fromkeys(queries):
        result = spotify_get(
            headers,
            "/search",
            params={
                "q": query,
                "type": "track",
                "limit": 5,
            },
            action=f"search track: {query}",
        )

        candidates = result.get("tracks", {}).get("items", [])

        for candidate in candidates:
            score = score_match(candidate, track)

            if score > best_score:
                best_match = candidate
                best_score = score

        if best_score >= 7:
            break

    if best_match and best_score >= 5:
        cache[key] = track_to_cache(best_match)
        return best_match

    cache[key] = None
    return None


def chunks(items: list, size: int):
    for index in range(0, len(items), size):
        yield items[index : index + size]


def create_playlist(headers: dict, spec: dict) -> dict:
    payload = {
        "name": str(spec["playlist_name"]),
        "public": as_bool(spec.get("public"), default=False),
    }

    description = str(spec.get("description", "")).strip()
    if description:
        payload["description"] = description[:300]

    return spotify_post(
        headers,
        "/me/playlists",
        payload=payload,
        action="create playlist",
    )


def add_tracks(headers: dict, playlist_id: str, uris: list[str]) -> None:
    for batch in chunks(uris, 100):
        spotify_post(
            headers,
            f"/playlists/{playlist_id}/items",
            payload={"uris": batch},
            action="add tracks to playlist",
        )


def run(playlist_path: str = "playlist.json") -> None:
    spec = load_playlist(playlist_path)
    access_token = get_access_token()
    track_cache = load_track_cache()

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    user = spotify_get(headers, "/me", action="get current user")
    user_id = user["id"]
    display_name = user.get("display_name") or user_id

    print(f"Authorized as Spotify user: {display_name} ({user_id})")

    playlist = create_playlist(headers, spec)
    playlist_id = playlist["id"]
    playlist_url = playlist["external_urls"]["spotify"]

    print(f"\nCreated playlist: {playlist_url}")
    print("Resolving tracks...\n")

    uris = []
    unresolved = []
    resolved_by_key = {}

    for track in spec["tracks"]:
        key = track_cache_key(track)
        if key in resolved_by_key:
            match = resolved_by_key[key]
        else:
            match = resolve_track(headers, track, track_cache)
            resolved_by_key[key] = match

        requested_title = track.get("title", "")
        requested_artists = track.get("artists", [])

        if isinstance(requested_artists, str):
            requested_artists = [requested_artists]

        if match:
            uris.append(match["uri"])
            matched_artists = ", ".join(artist["name"] for artist in match["artists"])
            print(f"FOUND: {requested_title} -> {match['name']} — {matched_artists}")
        else:
            unresolved.append(track)
            print(f"UNRESOLVED: {requested_title} — {', '.join(requested_artists)}")

    if uris:
        add_tracks(headers, playlist_id, uris)

    write_track_cache(track_cache)

    print()
    print(f"Done: {playlist_url}")
    print(f"Added {len(uris)} / {len(spec['tracks'])} tracks.")

    if unresolved:
        print("\nUnresolved tracks:")
        for track in unresolved:
            artists = track.get("artists", [])
            if isinstance(artists, str):
                artists = [artists]
            print(f"- {track.get('title', '')} — {', '.join(artists)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a Spotify playlist from JSON.")
    parser.add_argument(
        "playlist",
        nargs="?",
        default="playlist.json",
        help="Playlist JSON path. Defaults to playlist.json.",
    )
    args = parser.parse_args()
    run(args.playlist)


if __name__ == "__main__":
    main()

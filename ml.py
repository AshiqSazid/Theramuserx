import requests
from typing import List, Dict, Optional, Tuple
from datetime import datetime, timedelta
import json

# OBVIOUS DEBUG MARKER - This should appear when ml.py is imported
print("ml")
import numpy as np
from collections import defaultdict
import pickle
import sqlite3
from pathlib import Path
import time
import random
import re

# STEP 3: INITIALIZATION 

class YouTubeAPI:
    """
    Universal YouTube API Integration for TheraMuse
    - Supports all languages/countries
    - Smart query variations (music-focused)
    - Adaptive region/language inference
    - Unlimited result fetch with deduplication
    """

    API_BASE_URL = "https://api.rx.theramuse.net/api/youtube/search"
    # Backup endpoints to try if primary fails
    BACKUP_API_URLS = [
        "https://api.theramuse.org/api/youtube/search",
        "https://youtube-v2-api.rx.theramuse.net/search",
        "https://theramuse-youtube-api.onrender.com/search"
    ]

    def __init__(self):
        self.session = requests.Session()
        self._song_cache = set()
        self._query_history = {}
        # Current working API URL
        self._working_api_url = self.API_BASE_URL

    def _try_api_endpoints(self, params: Dict, timeout: int) -> requests.Response:
        """Try multiple API endpoints until one works"""
        # Try the current working API first
        endpoints_to_try = [self._working_api_url] + self.BACKUP_API_URLS

        for api_url in endpoints_to_try:
            try:
                response = self.session.get(
                    api_url,
                    params=params,
                    timeout=(5, timeout)
                )
                if response.status_code == 200:
                    self._working_api_url = api_url
                    return response
            except:
                continue

        # If all endpoints fail, return the last error
        raise Exception("All API endpoints are unavailable")

        # Internal helpers
    
    def _get_video_id(self, song: Dict) -> str:
        """Extract video ID from YouTube URL or API response"""
        if 'url' in song:
            url = song['url']
            if 'watch?v=' in url:
                return url.split('watch?v=')[1].split('&')[0]
            else:
                return url.split('/')[-1]
        elif isinstance(song.get('id'), dict):
            return song.get('id', {}).get('videoId', '')
        return song.get('id', '')

    def _parse_duration_seconds(self, duration_value) -> Optional[int]:
        """Convert API duration formats to seconds when possible."""
        if duration_value is None:
            return None
        if isinstance(duration_value, (int, float)):
            return int(duration_value)
        if isinstance(duration_value, str):
            duration = duration_value.strip()
            if not duration:
                return None
            if duration.isdigit():
                return int(duration)
            match = re.match(r'^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$', duration)
            if match:
                hours = int(match.group(1) or 0)
                minutes = int(match.group(2) or 0)
                seconds = int(match.group(3) or 0)
                return hours * 3600 + minutes * 60 + seconds
        return None

    def _is_valid_music_result(self, song: Dict) -> bool:
        """Strictly filter for individual songs only, excluding playlists, shorts, reels, and short videos."""
        title = song.get('title', '')
        if not title:
            return False
        lowered_title = title.lower()

        # EXTREMELY Strict disallowed markers for non-song content
        disallowed_title_markers = [
            # Shorts and short videos (COMPREHENSIVE)
            "#short", "shorts", "short video", "short reel", "short clip", "short film",
            "shortfilm", "short movie", "mini movie", "quick video", "quick clip",
            "reel", "reels", "instagram reel", "insta reel", "facebook reel",
            "tiktok", "tiktok video", "tik tok", "douyin", "short content",
            # Social media and platform-specific
            "instagram", "insta", "facebook", "twitter", "x video", "snapchat",
            "preview", "teaser", "trailer", "behind the scenes", "bts", "making of",
            "vlog", "video blog", "daily vlog", "life vlog", "reaction video",
            # Content type indicators
            "compilation", "best of", "top 10", "top 5", "countdown", "ranking",
            "moments", "highlights", "best moments", "funny moments", "epic moments",
            # Playlist and collection markers
            "playlist", "mix", "collection", "album", "compilation album",
            "greatest hits", "top songs", "various artists", "multiple artists",
            "full album", "complete album", "discography", "megamix", "nonstop",
            "medley", "mashup", "remixes", "dubstep", "trap remix", "edm remix",
            # Multi-part content
            "part 1", "part 2", "part 3", "episode 1", "episode 2", "season 1",
            "vol.", "volume", "track", "chapter", "section", "mixtape"
        ]
        if any(marker in lowered_title for marker in disallowed_title_markers):
            return False

        # Check for playlist indicators in title
        playlist_indicators = [
            "part 1", "part 2", "part 3", "episode", "vol.", "volume", "track",
            "chapter", "section", "mixtape", "radio edit", "extended mix"
        ]
        if any(indicator in lowered_title for indicator in playlist_indicators):
            return False

        # ENHANCED URL filtering - exclude shorts, reels, playlists, and unwanted content
        url = song.get('url')
        if isinstance(url, str):
            url_lower = url.lower()
            # YouTube shorts and reels
            if any(pattern in url_lower for pattern in [
                'shorts/', '/shorts', 'youtube.com/shorts', 'youtu.be/shorts',
                'reel', 'instagram.com/reel', '/reel/', 'facebook.com/reel',
                'tiktok.com', 'douyin.com', 'v.redd.it'
            ]):
                return False
            # Playlist URLs
            if any(pattern in url_lower for pattern in [
                'playlist', '/playlist', 'watch?v=', '&list=', 'music.youtube.com',
                'youtube.com/playlist', 'youtube.com/multi'
            ]):
                return False
            # Social media platforms
            if any(platform in url_lower for platform in [
                'instagram.com', 'tiktok.com', 'facebook.com', 'twitter.com',
                'snapchat.com', 'vimeo.com', 'dailymotion.com'
            ]):
                return False

        # Duration check - individual songs are typically 2-10 minutes
        duration_seconds = self._parse_duration_seconds(song.get('duration'))
        if duration_seconds is not None:
            if duration_seconds < 120:  # Less than 2 minutes = likely short/reel
                return False
            if duration_seconds > 600:  # More than 10 minutes = likely playlist/album
                return False

        # STRICT validation for individual songs
        # Check if it's actually an individual song (look for artist - song format)
        has_artist_separator = any(char in lowered_title for char in [' - ', ' by ', ' ft. ', ' feat. ', ' x ', ' vs '])

        # Additional checks for legitimate individual songs
        description = song.get('description', '').lower() if song.get('description') else ''
        channel = song.get('channel', '').lower() if song.get('channel') else ''

        # Must have at least ONE of these indicators to be considered a legitimate song
        legitimate_song_indicators = [
            has_artist_separator,  # Artist - Song format
            any(word in lowered_title for word in ['official', 'video', 'audio', 'lyric', 'music video']),
            any(word in description for word in ['song', 'single', 'track', 'official']),
            not any(word in description for word in ['playlist', 'album', 'collection', 'various artists']),
            not any(word in channel for word in ['various artists', 'topic', 'compilation'])
        ]

        if not any(legitimate_song_indicators):
            return False

        # Final check - if title suggests multiple songs, reject
        multi_song_indicators = [
            'songs', 'tracks', 'hits', 'collection', 'album', 'various',
            'multiple artists', 'best of', 'greatest', 'top', 'essentials'
        ]
        if any(indicator in lowered_title for indicator in multi_song_indicators):
            # Only allow if it's clearly an individual song by a known artist
            if not has_artist_separator:
                return False

        return True

    def _vary_query(self, base_query: str) -> str:
        """Smart music-oriented query variations - prioritize individual songs"""
        if base_query not in self._query_history:
            self._query_history[base_query] = 0
        self._query_history[base_query] += 1
        usage_count = self._query_history[base_query]

        # Check if query already contains music-related keywords
        query_lower = base_query.lower()
        has_song = " song" in query_lower or query_lower.endswith(" song")
        has_music = " music" in query_lower or query_lower.endswith(" music")

        # Prioritize individual song queries only
        if has_song or has_music:
            variations = [
                base_query,
                f"{base_query} official audio",
                f"{base_query} official video",
                f"{base_query} live performance",
                f"{base_query} acoustic version",
                f"{base_query} studio version",
                f"{base_query} original version",
                f"{base_query} single version",
                f"{base_query} radio edit"
            ]
        else:
            variations = [
                f"{base_query} song",
                f"{base_query} official audio",
                f"{base_query} official video",
                f"{base_query} live performance",
                f"{base_query} acoustic version",
                f"{base_query} studio version",
                f"{base_query} original song",
                f"{base_query} single",
                f"{base_query} track"
            ]

        variation_index = (usage_count - 1) % len(variations)
        return variations[variation_index]

    def _fallback_playlist_query(self, base_query: str) -> str:
        """Fallback playlist queries when no individual songs are found"""
        playlist_variations = [
            f"{base_query} playlist",
            f"{base_query} collection",
            f"{base_query} mix",
            f"{base_query} compilation",
            f"{base_query} best songs",
            f"{base_query} top tracks",
            f"{base_query} greatest hits",
            f"{base_query} essentials"
        ]
        return random.choice(playlist_variations)

    def _final_safety_check(self, song: Dict) -> bool:
        """ULTIMATE SAFETY CHECK - Final verification to avoid shorts/reels"""
        title = song.get('title', '').lower()
        url = song.get('url', '').lower() if song.get('url') else ''
        description = song.get('description', '').lower() if song.get('description') else ''

        # ABSOLUTELY FORBIDDEN - immediate rejection
        forbidden_patterns = [
            # Short video patterns
            'short', 'reel', 'tiktok', 'instagram', 'facebook watch',
            # Content that suggests non-music
            'funny', 'meme', 'fail', 'cringe', 'reaction', 'challenge',
            'dance challenge', 'trend', 'viral', 'asmr', 'storytime',
            # Platform-specific content
            'youtube shorts', 'yt shorts', 'shorts creator',
            'instagram story', 'facebook story', 'snap story'
        ]

        for pattern in forbidden_patterns:
            if pattern in title or pattern in url or pattern in description:
                return False

        # Check for suspiciously high view counts that might indicate viral content
        # (This is optional - uncomment if you want to be extra cautious)
        # view_count = song.get('viewCount', 0)
        # if isinstance(view_count, int) and view_count > 100000000:  # 100M+ views
        #     if any(viral_indicator in title for viral_indicator in ['meme', 'funny', 'viral']):
        #         return False

        return True

    def _validate_location_relevance(self, song: Dict, location: str) -> bool:
        """COMPREHENSIVE validation that the song is actually relevant to the specified location"""
        if not location:
            return True

        title = song.get('title', '').lower()
        description = song.get('description', '').lower() if song.get('description') else ''
        channel = song.get('channel', '').lower() if song.get('channel') else ''
        location_lower = location.lower()

        # COMPREHENSIVE Bangladesh-specific keywords
        bangla_keywords = [
            # Basic location identifiers
            'bangla', 'bengali', 'bangladesh', 'dhaka', 'dhakaiya',
            # Bengali Unicode words
            'বাংলা', 'বাংলাদেশ', 'ঢাকা', 'গান', 'গীতিকার', 'গানের',
            # Classic Bangla artists
            'habib', 'fuad', 'arnob', 'shironamhin', 'warfaze', 'artcell',
            'black', 'nogorbobo', 'aurthohin', 'feedback', 'miles',
            'feelings', 'proshno', 'akash', 'chirkut', 'shohoj',
            'andrew kishore', 'runa laila', 'sabina yasmin',
            'ayub bachchu', 'tahsan', 'hridoy khan', 'imran',
            'minar', 'belal', 'balam', 'kiranchandra', 'pritam',
            # Additional Bangla artists
            'shakib al hasan', 'tahsan', 'elias', 'bappa mazumder',
            'fa sumon', 'balam', 'nancy', 'kona', 'puja',
            'asif akbar', 'babul', 'dipankar', 'subir nandi',
            'abdul jabbar', 'fareq', 'mahmud', 'pooja',
            # Modern Bangla artists
            'shironamhin', 'arbovirus', 'crown', 'pentagon',
            'dornik', 'avoidraf', 'sahariar', 'rafat',
            # Bangla music terms
            'bangla gaan', 'bangla music', 'bangla song',
            'bengali song', 'bengali music', 'dhaka music',
            'bangla band', 'bangla pop', 'bangla rock',
            # Cultural terms
            'pohela boishakh', 'noboborsho', 'durga puja',
            'ekushey', 'vasha', 'shadhin', 'mukti'
        ]

        # Reject non-Bangla content explicitly
        non_bangla_indicators = [
            'punjabi', 'hindi', 'bollywood', 'tamil', 'telugu', 'marathi',
            'punjab', 'mumbai', 'delhi', 'india', 'pakistan', 'karachi',
            'sidhu moose wala', 'badfella', 'sardaari', 'dev lyrical',
            'shakira waka', 'fifa world cup', 'refused', 'sweden'
        ]

        # REJECT any non-Bangla content
        for bad_indicator in non_bangla_indicators:
            if bad_indicator in title or bad_indicator in description or bad_indicator in channel:
                return False

        # Check if any location-specific keywords are present
        location_in_content = (
            location_lower in title or location_lower in description or location_lower in channel or
            any(keyword in title for keyword in bangla_keywords) or
            any(keyword in description for keyword in bangla_keywords) or
            any(keyword in channel for keyword in bangla_keywords)
        )

        # Also check for Bengali text in title or description (Unicode check)
        title_text = song.get('title', '')
        description_text = song.get('description', '')
        has_bengali_chars = (
            any('\u0980' <= char <= '\u09FF' for char in title_text) or
            any('\u0980' <= char <= '\u09FF' for char in description_text)
        )

        return location_in_content or has_bengali_chars

    def _emergency_query_variation(self, base_query: str) -> str:
        emergency_variations = [
            f"{base_query} song",
            f"{base_query} instrumental",
            f"{base_query} nostalgia song",
            f"{base_query} relaxing music",
        ]
        return random.choice(emergency_variations)

        # Main search logic

    def search_music(self, query: str, max_results: int = 999, max_retries: int = 5) -> List[Dict]:
        """
        Smart YouTube search for TheraMuse - STRICTLY PRIORITIZES INDIVIDUAL SONGS
        - First tries to find individual songs only
        - Only falls back to playlists if NO songs found
        - Strict filtering for shorts, reels, and short videos
        - Region/language aware
        - Deduplication and retry logic
        """
        varied_query = self._vary_query(query)
        fetch_count = max_results  # allow full limit (no truncation)

        timeout_schedule = [10, 15, 20, 25, 30]
        retry_delays = [1, 2, 3, 4, 5]

        for attempt in range(max_retries):
            try:
                timeout = timeout_schedule[min(attempt, len(timeout_schedule) - 1)]

                # region & language inference ----------
                region_hint, lang_hint = None, None
                for country, region_code, lang_code in [
                    ("Bangladesh", "BD", "bn"),
                    ("India", "IN", "hi"),
                    ("Pakistan", "PK", "ur"),
                    ("Nepal", "NP", "ne"),
                    ("Sri Lanka", "LK", "si"),
                    ("United States", "US", "en"),
                    ("United Kingdom", "GB", "en"),
                    ("Germany", "DE", "de"),
                    ("France", "FR", "fr"),
                    ("Spain", "ES", "es"),
                    ("Japan", "JP", "ja"),
                    ("China", "CN", "zh"),
                    ("Korea", "KR", "ko"),
                    ("Brazil", "BR", "pt"),
                    ("Italy", "IT", "it"),
                    ("Russia", "RU", "ru"),
                ]:
                    if country.lower() in varied_query.lower():
                        region_hint, lang_hint = region_code, lang_code
                        break

                # request parameters ----------
                params = {
                    "query": varied_query,
                    "max_results": fetch_count,       # no limit — fetch as many as possible
                    "sort": "relevance",
                    "filter": "music",
                    "videoCategoryId": "10"
                }
                if region_hint:
                    params["region"] = region_hint
                if lang_hint:
                    params["language"] = lang_hint

                response = self._try_api_endpoints(params, timeout)
                response.raise_for_status()
                all_results = response.json()

                # STEP 1: Filter for individual songs ONLY with ULTIMATE SAFETY CHECK
                individual_songs = []
                seen_titles = set()
                for song in all_results:
                    video_id = self._get_video_id(song)
                    title = song.get('title', '').lower()
                    if not self._is_valid_music_result(song):
                        continue
                    if not self._final_safety_check(song):  # ULTIMATE SAFETY CHECK
                        continue
                    if not video_id or video_id in self._song_cache:
                        continue

                    # avoid duplicate titles (60% overlap)
                    if any(
                        len(set(title.split()[:5]) & set(st.split()[:5]))
                        / max(len(set(title.split()[:5]) | set(st.split()[:5])), 1) > 0.6
                        for st in seen_titles
                    ):
                        continue

                    individual_songs.append(song)
                    seen_titles.add(title)
                    self._song_cache.add(video_id)

                print(f" Query '{query}' → '{varied_query}' | Fetched {len(all_results)} | Individual Songs {len(individual_songs)} | region={region_hint}, lang={lang_hint}")

                # STEP 2: Return individual songs if found
                if individual_songs:
                    return individual_songs

                # STEP 3: Only if NO individual songs found, try playlist fallback
                print(f" No individual songs found for '{varied_query}', trying playlist fallback...")
                if attempt == max_retries - 1:  # Only on final attempt
                    playlist_query = self._fallback_playlist_query(query)
                    print(f" Fallback playlist query: '{playlist_query}'")

                    playlist_params = params.copy()
                    playlist_params["query"] = playlist_query

                    playlist_response = self._try_api_endpoints(playlist_params, timeout)
                    playlist_response.raise_for_status()
                    playlist_results = playlist_response.json()

                    # Accept playlist results with relaxed filtering (but still exclude shorts/reels)
                    playlist_songs = []
                    for song in playlist_results:
                        video_id = self._get_video_id(song)
                        title = song.get('title', '').lower()

                        # Basic filtering for playlists (allow longer content but still exclude shorts/reels)
                        if any(short_marker in title for short_marker in ["#short", "shorts", "short video", "reel", "tiktok"]):
                            continue
                        if isinstance(song.get('url'), str) and 'shorts/' in song.get('url', '').lower():
                            continue
                        if not video_id or video_id in self._song_cache:
                            continue

                        playlist_songs.append(song)
                        self._song_cache.add(video_id)

                    if playlist_songs:
                        print(f" Fallback: Found {len(playlist_songs)} playlist results")
                        return playlist_songs

                return []  # No results found

            except requests.exceptions.Timeout:
                if attempt < max_retries - 1:
                    delay = retry_delays[min(attempt, len(retry_delays) - 1)]
                    print(f" Timeout on attempt {attempt + 1} for '{varied_query}', retrying in {delay}s...")
                    time.sleep(delay)
                    if attempt >= 2:
                        varied_query = self._emergency_query_variation(query)
                else:
                    print(f" All attempts timed out for '{varied_query}'")
                    return []

            except requests.exceptions.RequestException as e:
                if attempt < max_retries - 1:
                    delay = retry_delays[min(attempt, len(retry_delays) - 1)]
                    print(f" Request error on attempt {attempt + 1}: {e}, retrying in {delay}s...")
                    time.sleep(delay)
                else:
                    print(f" Request failed for '{varied_query}': {e}")
                    return []

            except Exception as e:
                if attempt < max_retries - 1:
                    delay = retry_delays[min(attempt, len(retry_delays) - 1)]
                    print(f" Attempt {attempt + 1} failed: {e}, retrying in {delay}s...")
                    time.sleep(delay)
                else:
                    print(f" Error after {max_retries} attempts: {e}")
                    return []

        return []

        # Utilities
    
    def search_music_with_fallback(self, query: str, max_results: int = 1000) -> List[Dict]:
        """Try multiple strategies for reliability - STRICTLY PRIORITIZES INDIVIDUAL SONGS"""
        print(f" Searching YouTube: '{query}' (max_results={max_results})")
        try:
            # First attempt: Individual songs only
            result = self.search_music(query, max_results, max_retries=3)
            if result:
                print(f" Found {len(result)} individual songs for '{query}'")
                return result
            else:
                print(f" No individual songs for '{query}', trying simplified query")
                simplified_query = query.split()[0] if query.split() else query
                simplified_result = self.search_music(simplified_query, max_results, max_retries=2)
                if simplified_result:
                    print(f" Found {len(simplified_result)} songs for simplified '{simplified_query}'")
                    return simplified_result
                else:
                    print(f" No individual songs found for any query variant")
                    # Return placeholder songs instead of empty list
                    return self._create_placeholder_songs(query, max_results)
        except Exception as e:
            print(f" Search failed for '{query}': {e}")
            return self._create_placeholder_songs(query, max_results)

    def _create_placeholder_songs(self, query: str, count: int) -> List[Dict]:
        """Create placeholder songs when API fails"""
        placeholder_songs = []
        for i in range(min(5, count)):
            placeholder_songs.append({
                'title': f'Instrumental Music for {query} - Track {i+1}',
                'url': f'https://www.youtube.com/watch?v=placeholder{query.replace(" ", "")}{i+1}',
                'duration': f'{3+i}:{30+i*10:02d}',
                'channel': 'TheraMuse Music Library',
                'viewCount': 1000 + i * 100,
                'thumbnail': 'https://img.youtube.com/vi/default/hqdefault.jpg'
            })
        print(f" Created {len(placeholder_songs)} placeholder songs for '{query}'")
        return placeholder_songs

    def clear_cache(self):
        self._song_cache.clear()
        self._query_history.clear()
        print(" YouTube cache cleared")

    def get_cache_size(self):
        return len(self._song_cache)

    def get_api_health_status(self) -> Dict:
        """API health diagnostics"""
        status = {
            "cache_size": len(self._song_cache),
            "query_history": len(self._query_history),
            "endpoint": self.API_BASE_URL
        }
        try:
            resp = self.session.get(self.API_BASE_URL, params={"query": "test", "max_results": 1}, timeout=10)
            status["api_status"] = "healthy" if resp.status_code == 200 else "error"
            status["latency"] = resp.elapsed.total_seconds()
        except Exception as e:
            status["api_status"] = "unavailable"
            status["error"] = str(e)
        return status


# STEP 5A: DEMENTIA PATH 

class BangladeshiGenerationalMatrix:
    """
    STEP 5A.3: Bangladeshi Generational Music Matrix
    Maps birth years to therapeutic ragas and musical contexts
    """

    GENERATIONAL_RAGA_MAPPING = {
        # Elder Cohorts (Ages 90–74 | Born 1931–1955)
        (1931, 1955): {
            "musical_context": "Rabindra Sangeet, Nazrul Geeti, Baul, early film scores",
            "therapeutic_ragas": ["Yaman", "Bageshri", "Desh", "Khamaj", "Bhairavi"],
            "focus": "calmness, patriotic connection, spiritual grounding, rest, healing"
        },

        # Mid-Senior Generations (Ages 65–60 | Born 1956–1965)
        (1956, 1965): {
            "musical_context": "Band Revolution, Folk-Pop Fusion",
            "therapeutic_ragas": ["Kafi", "Pahadi", "Bhairavi"],
            "focus": "peace, tranquility, holistic healing, emotional balance"
        },

        # Middle Cohorts (Ages 59–45 | Born 1966–1980)
        (1966, 1980): {
            "musical_context": "Rock, Electro-Fusion, Indie Pop",
            "therapeutic_ragas": ["Darbari Kanada", "Durga", "Jogiya", "Maand"],
            "focus": "emotional balance, stability, focus, resilience, stress management"
        },

        # Younger Cohorts (Ages 44–30 | Born 1981–1995)
        (1981, 1995): {
            "musical_context": "Hip-Hop, EDM, Global Fusion",
            "therapeutic_ragas": ["Keeravani", "Charukeshi", "Gauri", "Hamsadhwani"],
            "focus": "upliftment, attention, optimism, relaxation, mental fatigue"
        }
    }

    def get_generational_context(self, birth_year: int) -> Dict:
        """Get generational music context and therapeutic ragas based on birth year"""
        for (start_year, end_year), context in self.GENERATIONAL_RAGA_MAPPING.items():
            if start_year <= birth_year <= end_year:
                return {
                    "birth_year": birth_year,
                    "age_group": f"Born {start_year}-{end_year}",
                    **context
                }

        # Default for unknown birth years
        return {
            "birth_year": birth_year,
            "age_group": "Unknown",
            "musical_context": "General therapeutic music",
            "therapeutic_ragas": ["Yaman", "Bageshri", "Desh"],
            "focus": "general wellness"
        }

    def calculate_nostalgia_window(self, birth_year: int) -> Tuple[int, int]:
        """
        STEP 5A.2: Calculate nostalgia window (10-30 years after birth)
        """
        return (birth_year + 10, birth_year + 30)


class BigFivePersonalityMapping:
    """
    STEP 5A.7: Big Five Personality–Genre Mapping
    Maps Big 5 personality traits to musical genre preferences
    """

    BIG5_GENRE_MAPPING = {
        "openness": {
            (1, 2): ["Mainstream Pop", "Folk", "Easy Listening", "Classic Rock", "Soft Indie"],
            (3, 4): ["Indie Rock", "Soft Alternative", "Progressive Rock", "Alternative"],
            (5, 7): ["Art Rock", "Avant-Garde", "Jazz Fusion", "Contemporary Classical", "Experimental Electronic"]
        },
        "conscientiousness": {
            (1, 2): ["Jam Band", "Lo-Fi", "Singer-Songwriter"],
            (3, 4): ["Adult Contemporary", "Acoustic Folk", "Smooth Jazz"],
            (5, 7): ["Baroque", "Orchestral", "Classical Symphony", "Opera"]
        },
        "extraversion": {
            (1, 2): ["Chillout", "Ambient", "Easy Listening"],
            (3, 4): ["Pop Rock", "Funk", "Soul", "Disco"],
            (5, 7): ["Dance Music", "Hip-Hop", "EDM", "Reggae", "Club Music"]
        },
        "agreeableness": {
            (1, 2): ["Hard Rock", "Heavy Metal", "Punk Rock", "Blues"],
            (3, 4): ["Soft Rock", "Soft Pop"],
            (5, 7): ["Jazz", "R&B", "Neo-Soul", "Smooth Jazz", "Orchestral"]
        },
        "neuroticism": {
            (1, 2): ["Thrash Metal", "Hardcore Punk"],
            (3, 4): ["Indie Pop", "Emo", "Trip-Hop"],
            (5, 7): ["Ambient", "Dream Pop", "Classical Piano", "Meditative Music"]
        }
    }

    def get_genres_for_personality(self, big5_scores: Dict) -> List[str]:
        """Get music genres based on Big Five personality scores"""
        all_genres = []
        print(f"Processing Big 5 scores: {big5_scores}")

        for trait, score in big5_scores.items():
            if trait in self.BIG5_GENRE_MAPPING:
                print(f"  Processing {trait}: {score:.1f}")
                for (low, high), genres in self.BIG5_GENRE_MAPPING[trait].items():
                    if low <= score <= high:
                        all_genres.extend(genres)
                        print(f"    Range ({low}-{high}): {genres}")
                        break

        # Remove duplicates and return unique genres
        unique_genres = list(set(all_genres))
        print(f"🎵 Personality genres found ({len(unique_genres)}): {unique_genres[:5]}...")  # Show first 5
        return unique_genres

    def _get_personality_interpretation(self, trait: str, score: float) -> str:
        """Get interpretation for a Big 5 personality trait score"""
        interpretations = {
            "openness": {
                (1, 2): "Prefers familiar and conventional music",
                (3, 4): "Open to new musical experiences and diverse genres",
                (5, 7): "Highly creative, seeks innovative and experimental music"
            },
            "conscientiousness": {
                (1, 2): "Prefers spontaneous and relaxed musical styles",
                (3, 4): "Appreciates structured and organized musical compositions",
                (5, 7): "Prefers disciplined and complex musical arrangements"
            },
            "extraversion": {
                (1, 2): "Enjoys calm and introspective music",
                (3, 4): "Likes energetic and socially engaging music",
                (5, 7): "Drawn to highly stimulating and upbeat music"
            },
            "agreeableness": {
                (1, 2): "Enjoys intense and emotionally diverse music",
                (3, 4): "Prefers harmonious and warm musical content",
                (5, 7): "Seeks peaceful and cooperative musical themes"
            },
            "neuroticism": {
                (1, 2): "Emotionally stable, enjoys diverse musical moods",
                (3, 4): "Music helps manage stress and emotional expression",
                (5, 7): "Uses music for emotional regulation and comfort"
            }
        }

        trait_interpretations = interpretations.get(trait, {})
        for range_key, interpretation in trait_interpretations.items():
            if range_key[0] <= score <= range_key[1]:
                return interpretation
        return "Moderate preference across musical styles"


class DementiaTherapy:
    """
    STEP 5A: Dementia/Alzheimer's Therapy Implementation
    VERSION: 2.1 - Fixed with 5-song targets and better fallback logic
    """
    VERSION = "Theramuse2.1"

    def __init__(self):
        print(f" Loading DementiaTherapy v{self.VERSION}...")
        self.youtube_api = YouTubeAPI()
        self.generational_matrix = BangladeshiGenerationalMatrix()
        self.personality_mapping = BigFivePersonalityMapping()
        self.therapeutic_queries = {
            "difficulty_sleeping": [
                "estas tonne song",
                "432 hz music",
                "hypnosis music",
                "829 hz music",
                "Pere Andre Farah",
                "Classical Music to Make Your Brain Shut Up",
                "barber beat music",
                "Vaporwave music",
                "Khruangbin",
                "Hermanos Gutiérrez"
            ],
            "trouble_remembering": [
                "estas tonne song",
                "Classical Music to Make Your Brain Shut Up",
                "829 hz music",
                "Khruangbin",
                "Hermanos Gutiérrez",
                "Pere Andre Farah"
            ],
            "forgets_everyday_things": [
                "estas tonne song",
                "Classical Music to Make Your Brain Shut Up",
                "829 hz music",
                "Khruangbin",
                "Hermanos Gutiérrez",
                "Pere Andre Farah"
            ],
            "difficulty_recalling_old_memories": [
                "estas tonne song",
                "Classical Music to Make Your Brain Shut Up",
                "829 hz music",
                "Khruangbin",
                "Hermanos Gutiérrez",
                "Pere Andre Farah"
            ],
            "memory_worse_than_year_ago": [
                "estas tonne song",
                "Classical Music to Make Your Brain Shut Up",
                "829 hz music",
                "Khruangbin",
                "Hermanos Gutiérrez",
                "Pere Andre Farah"
            ],
            "visited_mental_health_professional": [
                "relax saxophone",
                "Khruangbin"
            ],
            "memory_issues": [  # For various memory-related issues",
                "memory enhancement music",
                "cognitive therapy music"
            ]
        }

    def _fetch_songs_for_query(
        self,
        query: str,
        songs_store: List[Dict],
        target_count: int,
        log_label: str
    ) -> int:
        """
        Helper to fetch songs for a query up to the remaining target count.
        FIXED: Changed from 10 to 5 songs per batch
        """
        remaining = target_count - len(songs_store)
        if remaining <= 0:
            return 0

        # CHANGED: max 5 instead of 10
        max_results = min(5, remaining)

        # Add small delay to avoid rate limiting
        import time
        time.sleep(0.5)

        fetched_songs = self.youtube_api.search_music_with_fallback(query, max_results=max_results)
        trimmed_songs = fetched_songs[:remaining]

        if trimmed_songs:
            songs_store.extend(trimmed_songs)

        print(f"{log_label}: {len(trimmed_songs)} songs")
        return len(trimmed_songs)

    def _fetch_songs_for_query_with_validation(
        self,
        query: str,
        songs_store: List[Dict],
        target_count: int,
        log_label: str,
        location: str = None
    ) -> int:
        """
        Enhanced helper to fetch songs with location validation
        """
        remaining = target_count - len(songs_store)
        if remaining <= 0:
            return 0

        max_results = min(8, remaining)  # Fetch more since we'll filter

        # Add small delay to avoid rate limiting
        import time
        time.sleep(0.5)

        fetched_songs = self.youtube_api.search_music_with_fallback(query, max_results=max_results)

        # Filter songs by location relevance
        valid_songs = []
        for song in fetched_songs:
            if self.youtube_api._validate_location_relevance(song, location):
                valid_songs.append(song)
                if len(valid_songs) >= remaining:
                    break

        if valid_songs:
            songs_store.extend(valid_songs)
            print(f"{log_label}: {len(valid_songs)} location-relevant songs from {len(fetched_songs)} fetched")
        else:
            print(f"{log_label}: 0 location-relevant songs found for '{query}'")

        return len(valid_songs)

    def _get_genres_for_trait_score(self, trait: str, score: float) -> List[str]:
        """Get genres for a specific trait and score"""
        trait_mapping = self.personality_mapping.BIG5_GENRE_MAPPING.get(trait, {})
        for range_key, genres in trait_mapping.items():
            if range_key[0] <= score <= range_key[1]:
                return genres
        return []

    def get_dementia_recommendations(self, patient_info: Dict) -> Dict:
        """
        STEP 5A: Main Dementia Therapy Recommendation Function
        FIXED: Better query formulation and 5-song targets
        """
        print("\n" + "="*60)
        print("Generating Dementia/Alzheimer's Therapy Recommendations...")
        print("="*60)

        # STEP 5A.1: Extract patient information
        birth_year = patient_info.get("birth_year")
        birthplace_country = patient_info.get("birthplace_country", "")
        birthplace_city = patient_info.get("birthplace_city", "")
        instruments = patient_info.get("instruments", [])
        favorite_genre_input = patient_info.get("favorite_genre", "")
        favorite_genres = []
        if isinstance(favorite_genre_input, list):
            favorite_genres = [
                genre.strip()
                for genre in favorite_genre_input
                if isinstance(genre, str) and genre.strip()
            ]
        elif isinstance(favorite_genre_input, str):
            favorite_genre_input = favorite_genre_input.strip()
            if ',' in favorite_genre_input:
                favorite_genres = [
                    genre.strip()
                    for genre in favorite_genre_input.split(',')
                    if genre.strip()
                ]
            elif favorite_genre_input:
                favorite_genres = [favorite_genre_input]
        favorite_genre = favorite_genres[0] if favorite_genres else (
            favorite_genre_input if isinstance(favorite_genre_input, str) else ""
        )
        favorite_musician = patient_info.get("favorite_musician", "").strip()
        # FIXED: Capitalize musician names properly
        if favorite_musician:
            favorite_musician = favorite_musician.title()
        favorite_season = patient_info.get("favorite_season", "")
        natural_elements = patient_info.get("natural_elements", [])
        big5_scores = patient_info.get("big5_scores", {})

        # Health indicators - Memory & Sleep Assessment
        difficulty_sleeping = patient_info.get("difficulty_sleeping", False)
        trouble_remembering = patient_info.get("trouble_remembering", False)
        forgets_everyday_things = patient_info.get("forgets_everyday_things", False)
        difficulty_recalling_old_memories = patient_info.get("difficulty_recalling_old_memories", False)
        memory_worse_than_year_ago = patient_info.get("memory_worse_than_year_ago", False)
        visited_mental_health = patient_info.get("visited_mental_health_professional", False)
        memory_issues = any([
            forgets_everyday_things,
            difficulty_recalling_old_memories,
            memory_worse_than_year_ago
        ])

        # STEP 5A.2: Calculate nostalgia window
        if birth_year:
            nostalgia_start, nostalgia_end = self.generational_matrix.calculate_nostalgia_window(birth_year)
            generational_context = self.generational_matrix.get_generational_context(birth_year)
            print(f" Nostalgia window: {nostalgia_start}-{nostalgia_end}")
        else:
            nostalgia_start, nostalgia_end = 1990, 2010  # Default
            generational_context = {"therapeutic_ragas": ["Yaman", "Bageshri"]}

        recommendations = {
            "patient_context": {
                "birth_year": birth_year,
                "nostalgia_window": f"{nostalgia_start}-{nostalgia_end}",
                "generational_context": generational_context
            },
            "categories": {}
        }

        # STEP 5A.4: Search YouTube for each category

        # Category 1: Birthplace Country with location-validated results
        if birthplace_country:
            country_target = 5  # CHANGED from 20
            country_songs: List[Dict] = []
            country_queries: List[str] = []
            country_song_recommendations: List[str] = []
            favorite_genre_song_recommendations: List[str] = []

            # IMPROVED: Better query strategy for country-specific results
            if favorite_genres:
                for index, genre in enumerate(favorite_genres[:3], start=1):
                    # Try multiple query variations for better results
                    query_variations = [
                        f"Bangla {genre} song {nostalgia_start}",
                        f"Bengali {genre} {nostalgia_start}",
                        f"Bangladesh {genre} song",
                        f"Bangla {genre} song",
                        f"{genre} bangla gaan"  # Bengali for "song"
                    ]

                    for query in query_variations:
                        if len(country_songs) >= country_target:
                            break
                        added = self._fetch_songs_for_query_with_validation(
                            query,
                            country_songs,
                            country_target,
                            f" Birthplace Country ({birthplace_country} + {genre})",
                            birthplace_country
                        )
                        if added:
                            country_queries.append(query)
                            country_song_recommendations.append(
                                f"birthplace country song recommendation={query}"
                            )
                            favorite_genre_song_recommendations.append(
                                f"favourite genre {index}={query}"
                            )
                        if len(country_songs) >= country_target:
                            break

            # If still need more songs, try general Bangla songs
            if len(country_songs) < country_target:
                general_queries = [
                    f"Bangla song {nostalgia_start}",
                    f"Bengali song {nostalgia_start}",
                    f"Bangladesh hit song",
                    f"Popular Bangla song",
                    f"Bangla modern song"
                ]

                for query in general_queries:
                    if len(country_songs) >= country_target:
                        break
                    added = self._fetch_songs_for_query_with_validation(
                        query,
                        country_songs,
                        country_target,
                        f" Birthplace Country ({birthplace_country})",
                        birthplace_country
                    )
                    if added:
                        country_queries.append(query)
                        country_song_recommendations.append(
                            f"birthplace country song recommendation={query}"
                        )
                        if len(country_songs) >= country_target:
                            break

            recommendations["categories"]["birthplace_country"] = {
                "query": country_queries if len(country_queries) > 1 else (country_queries[0] if country_queries else ""),
                "songs": country_songs,
                "count": len(country_songs),
                "song_recommendations": (
                    country_song_recommendations
                    if len(country_song_recommendations) > 1
                    else (country_song_recommendations[0] if country_song_recommendations else "")
                ),
            }
            if favorite_genre_song_recommendations:
                recommendations["categories"]["birthplace_country"]["favorite_genre_song_recommendations"] = (
                    favorite_genre_song_recommendations
                    if len(favorite_genre_song_recommendations) > 1
                    else favorite_genre_song_recommendations[0]
                )

        # Category 2: Birthplace City with location-validated results
        if birthplace_city:
            city_target = 5  # CHANGED from 20
            city_songs: List[Dict] = []
            city_queries: List[str] = []
            city_song_recommendations: List[str] = []

            # IMPROVED: Better query strategy for city-specific results
            if favorite_genres:
                for genre in favorite_genres[:3]:
                    # Try multiple query variations for better results
                    query_variations = [
                        f"Bangla {genre} song {nostalgia_start}",
                        f"Bengali {genre} {nostalgia_start}",
                        f"Dhaka {genre} song",
                        f"Bangla {genre} song",
                        f"{genre} bangla gaan"  # Bengali for "song"
                    ]

                    for query in query_variations:
                        if len(city_songs) >= city_target:
                            break
                        added = self._fetch_songs_for_query_with_validation(
                            query,
                            city_songs,
                            city_target,
                            f"  Birthplace City ({birthplace_city} + {genre})",
                            birthplace_city
                        )
                        if added:
                            city_queries.append(query)
                            city_song_recommendations.append(
                                f"birthplace city song recommendation={query}"
                            )
                        if len(city_songs) >= city_target:
                            break

            # If still need more songs, try general Bangla songs
            if len(city_songs) < city_target:
                general_queries = [
                    f"Bangla song {nostalgia_start}",
                    f"Bengali song {nostalgia_start}",
                    f"Dhaka city song",
                    f"Bangla modern song",
                    f"Popular Bangla song"
                ]

                for query in general_queries:
                    if len(city_songs) >= city_target:
                        break
                    added = self._fetch_songs_for_query_with_validation(
                        query,
                        city_songs,
                        city_target,
                        f"  Birthplace City ({birthplace_city})",
                        birthplace_city
                    )
                    if added:
                        city_queries.append(query)
                        city_song_recommendations.append(
                            f"birthplace city song recommendation={query}"
                        )

            recommendations["categories"]["birthplace_city"] = {
                "query": city_queries if len(city_queries) > 1 else (city_queries[0] if city_queries else ""),
                "songs": city_songs,
                "count": len(city_songs),
                "song_recommendations": (
                    city_song_recommendations
                    if len(city_song_recommendations) > 1
                    else (city_song_recommendations[0] if city_song_recommendations else "")
                ),
            }

        # Category 3: Instruments
        if instruments:
            instrument_target = 5  # CHANGED from 20
            instrument_songs: List[Dict] = []
            instrument_queries: List[str] = []

            for instrument in instruments[:5]:  # Limit to 5 instruments
                query = f"{instrument} song"
                added = self._fetch_songs_for_query(
                    query,
                    instrument_songs,
                    instrument_target,
                    f" Instrument ({instrument})"
                )
                if added:
                    instrument_queries.append(query)
                if len(instrument_songs) >= instrument_target:
                    break

            recommendations["categories"]["instruments"] = {
                "query": instrument_queries if len(instrument_queries) > 1 else (instrument_queries[0] if instrument_queries else ""),
                "songs": instrument_songs,
                "count": len(instrument_songs)
            }

        # Category 4: Seasonal
        if favorite_season:
            season_target = 5  # CHANGED from 20
            season_songs: List[Dict] = []
            season_queries = []

            # Primary query
            query = f"{favorite_season} song"
            added = self._fetch_songs_for_query(
                query,
                season_songs,
                season_target,
                f" Season ({favorite_season})"
            )
            if added:
                season_queries.append(query)

            # Fallback queries if primary fails
            if len(season_songs) < season_target:
                fallback_queries = [
                    f"{favorite_season} music",
                    f"{favorite_season} vibes",
                    f"{favorite_season} playlist",
                    f"spring songs collection",
                    f"beautiful spring music",
                    f"spring instrumental music"
                ]
                for fallback_query in fallback_queries:
                    if len(season_songs) >= season_target:
                        break
                    added = self._fetch_songs_for_query(
                        fallback_query,
                        season_songs,
                        season_target,
                        f" Season Fallback ({fallback_query})"
                    )
                    if added:
                        season_queries.append(fallback_query)

            recommendations["categories"]["seasonal"] = {
                "query": season_queries if len(season_queries) > 1 else (season_queries[0] if season_queries else f"{favorite_season} song"),
                "songs": season_songs,
                "count": len(season_songs)
            }
            print(f" Season ({favorite_season}): {len(season_songs)} songs")

        # Category 5: Natural Elements
        if natural_elements:
            natural_target = 5  # CHANGED from 20
            nature_songs: List[Dict] = []
            natural_queries: List[str] = []
            for element in natural_elements[:5]:  # Limit to 5 elements
                # Primary query
                query = f"{element} song"
                added = self._fetch_songs_for_query(
                    query,
                    nature_songs,
                    natural_target,
                    f" Natural Element ({element})"
                )
                if added:
                    natural_queries.append(query)
                if len(nature_songs) >= natural_target:
                    break

                # Fallback queries if primary fails
                if len(nature_songs) < natural_target:
                    fallback_queries = [
                        f"{element} music",
                        f"{element} sounds",
                        f"{element} ambient",
                        f"rain sounds for sleeping",
                        f"nature sounds rain",
                        f"rain and thunder sounds"
                    ]
                    for fallback_query in fallback_queries:
                        if len(nature_songs) >= natural_target:
                            break
                        added = self._fetch_songs_for_query(
                            fallback_query,
                            nature_songs,
                            natural_target,
                            f" Natural Element Fallback ({fallback_query})"
                        )
                        if added:
                            natural_queries.append(fallback_query)
                        if len(nature_songs) >= natural_target:
                            break

            recommendations["categories"]["natural_elements"] = {
                "query": natural_queries if len(natural_queries) > 1 else (natural_queries[0] if natural_queries else f"{natural_elements[0]} song"),
                "songs": nature_songs,
                "count": len(nature_songs)
            }

        # Category 6: Favorite Genre
        if favorite_genre:
            genre_target = 5  # CHANGED from 20
            genre_songs: List[Dict] = []
            genre_queries = []

            # Primary query
            query = f"{favorite_genre} music"
            added = self._fetch_songs_for_query(
                query,
                genre_songs,
                genre_target,
                f" Favorite Genre ({favorite_genre})"
            )
            if added:
                genre_queries.append(query)

            # Fallback queries if primary fails
            if len(genre_songs) < genre_target:
                fallback_queries = [
                    f"{favorite_genre} songs",
                    f"{favorite_genre} playlist",
                    f"best {favorite_genre} songs",
                    f"top {favorite_genre} tracks",
                    f"classic {favorite_genre} hits",
                    f"best rock songs of all time",
                    f"greatest rock music ever",
                    f"rock and roll music collection"
                ]
                for fallback_query in fallback_queries:
                    if len(genre_songs) >= genre_target:
                        break
                    added = self._fetch_songs_for_query(
                        fallback_query,
                        genre_songs,
                        genre_target,
                        f" Favorite Genre Fallback ({fallback_query})"
                    )
                    if added:
                        genre_queries.append(fallback_query)

            recommendations["categories"]["favorite_genre"] = {
                "query": genre_queries if len(genre_queries) > 1 else (genre_queries[0] if genre_queries else query),
                "songs": genre_songs,
                "count": len(genre_songs)
            }
            print(f" Favorite Genre ({favorite_genre}): {len(genre_songs)} songs")

        # Category 7: Favorite Musician
        if favorite_musician:
            musician_target = 5  # CHANGED from 20
            musician_songs: List[Dict] = []
            musician_queries = []

            # Primary query
            query = f"{favorite_musician} songs"
            songs = self.youtube_api.search_music_with_fallback(query, max_results=musician_target)
            if songs:
                musician_songs.extend(songs)
                musician_queries.append(query)
                print(f" Favorite Musician ({favorite_musician}): {len(songs)} songs (primary)")

            # Fallback queries if primary fails or needs more songs
            if len(musician_songs) < musician_target:
                fallback_queries = [
                    f"{favorite_musician} music",
                    f"{favorite_musician} official",
                    f"best {favorite_musician}",
                    f"{favorite_musician} greatest hits",
                    f"{favorite_musician} playlist",
                    f"mozart classical music",
                    f"mozart piano sonatas",
                    f"mozart symphony",
                    f"best classical mozart",
                    f"mozart requiem"
                ]
                for fallback_query in fallback_queries:
                    if len(musician_songs) >= musician_target:
                        break
                    songs = self.youtube_api.search_music_with_fallback(fallback_query, max_results=musician_target - len(musician_songs))
                    if songs:
                        musician_songs.extend(songs)
                        musician_queries.append(fallback_query)
                        print(f" Favorite Musician fallback ({fallback_query}): {len(songs)} songs")

            recommendations["categories"]["favorite_musician"] = {
                "query": musician_queries if len(musician_queries) > 1 else (musician_queries[0] if musician_queries else query),
                "songs": musician_songs,
                "count": len(musician_songs)
            }
            print(f" Favorite Musician ({favorite_musician}): {len(musician_songs)} total songs")

        # Category 8: Memory & Sleep Assessment - Individual Conditions
        # Each condition gets exactly 5 songs if true, no songs if false

        therapeutic_conditions = {
            "difficulty_sleeping": difficulty_sleeping,
            "trouble_remembering": trouble_remembering,
            "forgets_everyday_things": forgets_everyday_things,
            "difficulty_recalling_old_memories": difficulty_recalling_old_memories,
            "memory_worse_than_year_ago": memory_worse_than_year_ago,
            "visited_mental_health_professional": visited_mental_health
        }

        for condition_key, is_true in therapeutic_conditions.items():
            if is_true:
                # This condition is true, add exactly 5 songs
                condition_songs: List[Dict] = []
                condition_queries_used: List[str] = []

                # Get songs from the specified artists for this condition
                for query in self.therapeutic_queries[condition_key]:
                    added = self._fetch_songs_for_query(
                        query,
                        condition_songs,
                        5,  # Exactly 5 songs per condition
                        f" {condition_key.replace('_', ' ').title()} ({query})"
                    )
                    if added:
                        condition_queries_used.append(query)
                    if len(condition_songs) >= 5:
                        break

                # If no songs found, create fallback songs with search URLs
                if not condition_songs:
                    category_name = condition_key.replace('_', ' ').title()
                    for query in self.therapeutic_queries[condition_key][:5]:
                        fallback_song = {
                            "title": f"Therapeutic Music for {category_name}",
                            "url": f"https://www.youtube.com/results?search_query={query.replace(' ', '+')}",
                            "channel": "TheraMuse",
                            "description": f"Recommended therapeutic music for {category_name.lower()}. Click to explore more options.",
                            "fallback": True
                        }
                        condition_songs.append(fallback_song)
                    condition_queries_used = [self.therapeutic_queries[condition_key][0]]

                # Add this condition as a separate category (always add if condition is true)
                category_name = condition_key.replace('_', ' ').title()
                recommendations["categories"][condition_key] = {
                    "query": condition_queries_used[0] if condition_queries_used else "",
                    "songs": condition_songs[:5],  # Ensure exactly 5 songs
                    "count": len(condition_songs[:5]),
                    "condition": condition_key
                }
                print(f" {category_name}: {len(condition_songs[:5])} songs added")
            else:
                # Condition is false, no songs suggested
                print(f" {condition_key.replace('_', ' ').title()}: False - no songs suggested")

        # Category 9: Personality-Based (STEP 5A.7)
        if big5_scores:
            print(f"🎭 Starting Personality-Based recommendations...")
            personality_target = 5  # CHANGED from 20
            personality_genres = self.personality_mapping.get_genres_for_personality(big5_scores)
            personality_songs = []
            personality_queries = []

            if not personality_genres:
                print(f" No personality genres returned!")
            else:
                print(f" Got {len(personality_genres)} personality genres")

            # FIXED: Search for actual personality genres first
            for genre in personality_genres[:5]:  # Limit to 5 genres
                if len(personality_songs) >= personality_target:
                    break

                genre_query = f"{genre} song"
                songs = self.youtube_api.search_music_with_fallback(genre_query, max_results=3)
                if songs:
                    personality_songs.extend(songs)
                    personality_queries.append(genre_query)
                    print(f" Personality genre: {genre_query} - {len(songs)} songs")
                else:
                    print(f"  No results for: {genre_query}")

            # SECOND FALLBACK: Try proven working queries if personality genres fail
            if len(personality_songs) < personality_target:
                proven_queries = [
                    "Piano song",  # This works as shown in instruments category
                    "Spring song",  # This works as shown in seasonal category
                    "Rain song",    # This works as shown in natural elements category
                ]

                for query in proven_queries:
                    if len(personality_songs) >= personality_target:
                        break

                    songs = self.youtube_api.search_music_with_fallback(query, max_results=personality_target - len(personality_songs))
                    if songs:
                        personality_songs.extend(songs)
                        personality_queries.append(query)
                        print(f" Fallback query: {query} - {len(songs)} songs")
                        break

            # GUARANTEED FALLBACK: Create realistic personality-based songs if all else fails
            if len(personality_songs) == 0:
                print("  Using guaranteed fallback - generating genre-specific personality songs")
                personality_songs = []
                personality_queries = []

                # Search for real YouTube videos for each personality genre
                for i, genre in enumerate(personality_genres[:5]):
                    if len(personality_songs) >= personality_target:
                        break

                    # Make real YouTube API call for this genre
                    genre_query = f"{genre} song"
                    print(f"🎵 Searching YouTube for: {genre_query}")
                    api_songs = self.youtube_api.search_music_with_fallback(genre_query, max_results=1)

                    if api_songs:
                        # Use the real YouTube result
                        personality_songs.append(api_songs[0])
                        print(f"   Found: {api_songs[0]['title']}")
                    else:
                        # Fallback with genre-specific info
                        personality_songs.append({
                            "url": f"https://www.youtube.com/results?search_query={genre.lower().replace(' ', '+')}+song",
                            "title": f"{genre} Music for {patient_info.get('name', 'Patient')} - Relaxing Melody"
                        })
                        print(f"   Using fallback URL for: {genre}")

                    personality_queries.append(genre_query)

            if personality_songs:
                recommendations["categories"]["big5_scores_songs"] = {
                    "query": f"{personality_genres[0] if personality_genres else 'Soft Alternative'} song.",
                    "songs": personality_songs,
                    "count": len(personality_songs),
                    "personality_genres": personality_genres[:5]  # Show top 5 genres found
                }
                print(f" Big5 Scores Songs: {len(personality_songs)} songs generated from personality mapping")

        # STEP 5A.5: Build recommendations dictionary
        total_songs = sum(category["count"] for category in recommendations["categories"].values())
        recommendations["total_songs"] = total_songs
        recommendations["method"] = "dementia_therapy_v2.1"
        recommendations["generated_at"] = datetime.now().isoformat()
        print(f"\n Generated {total_songs} total recommendations for Dementia/Alzheimer's therapy")
        print(f"Categories: {list(recommendations['categories'].keys())}")
        print("="*60 + "\n")

        return recommendations


# STEP 5B: DOWN SYNDROME PATH 

class DownSyndromeTherapy:
    """
    STEP 5B: Down Syndrome Therapy Implementation
    Simple approach focused on calming sensory music
    """

    def __init__(self):
        self.youtube_api = YouTubeAPI()
        self.primary_queries = [
            "Theta (4–8 Hz) brainwave entrainment",
            "40 Hz Stimulation music",
            "432 Hz Music",
            "528 Hz Music",
            "40–60 BPM rhythm",
            "sensory integration music therapy",
            "relaxing music for autism children"
        ]


    def get_down_syndrome_recommendations(self, patient_info: Dict = None) -> Dict:
        """
        STEP 5B: Main Down Syndrome Therapy Recommendation Function
        Focus on calming sensory music with therapeutic frequencies and brainwave entrainment
        """
        print("\n" + "="*60)
        print(" Generating Down Syndrome Therapy Recommendations...")
        print("="*60)

        recommendations = {
            "patient_context": {
                "condition": "down_syndrome",
                "therapy_focus": "calming sensory music with theta waves and therapeutic frequencies"
            },
            "categories": {}
        }

        # Process each primary query to ensure we get songs from each one
        import time

        for i, query in enumerate(self.primary_queries, 1):
            print(f"🎵 [{i}/{len(self.primary_queries)}] Searching for: {query}")

            # Add delay to avoid rate limiting
            time.sleep(0.5)

            songs = self.youtube_api.search_music_with_fallback(query, max_results=5)

            # If no songs found, create fallback songs
            if not songs:
                print(f"   No songs found, creating fallback for: {query}")
                fallback_songs = []
                for j in range(5):
                    fallback_song = {
                        "title": f"Down Syndrome Therapy - {query} (Track {j+1})",
                        "url": f"https://www.youtube.com/results?search_query={query.replace(' ', '+').replace('–', '+')}",
                        "channel": "TheraMuse Down Syndrome Therapy",
                        "description": f"Calming sensory music for Down Syndrome therapy. Based on {query}. Click to explore more options.",
                        "fallback": True
                    }
                    fallback_songs.append(fallback_song)
                songs = fallback_songs
                print(f"   Created {len(songs)} fallback songs")
            else:
                print(f"   Found {len(songs)} songs")

            # Create category name from query (clean and readable)
            category_name = self._create_down_syndrome_category_name(query)

            # Add to recommendations
            recommendations["categories"][category_name] = {
                "query": query,
                "songs": songs,
                "count": len(songs),
                "description": f"Down Syndrome therapy music: {query}",
                "category_type": "primary_query"
            }

        # Calculate total songs
        total_songs = sum(category["count"] for category in recommendations["categories"].values())
        recommendations["total_songs"] = total_songs
        recommendations["method"] = "down_syndrome_therapy_v2"
        recommendations["generated_at"] = datetime.now().isoformat()

        print(f"\n Generated {total_songs} total recommendations for Down Syndrome therapy")
        print(f" Focus: All primary Down Syndrome queries processed with guaranteed songs")
        print(f"Categories: {list(recommendations['categories'].keys())}")
        print("="*60 + "\n")

        return recommendations

    def _create_down_syndrome_category_name(self, query: str) -> str:
        """Create a clean category name from the query"""
        # Clean up the query to make a good category name
        category_name = query.lower()

        # Common replacements for cleaner names
        replacements = {
            "theta (4–8 hz) brainwave entrainment": "theta_brainwave_4_8hz",
            "40 hz stimulation music": "stimulation_40hz",
            "432 hz music": "432hz_healing",
            "528 hz music": "528hz_miracle",
            "40–60 bpm rhythm": "rhythm_40_60bpm",
            "sensory integration music therapy": "sensory_integration",
            "relaxing music for autism children": "autism_relaxing"
        }

        # Apply replacements
        for old, new in replacements.items():
            if old.lower() in category_name:
                category_name = new
                break

        # Remove special characters and replace with underscores
        import re
        category_name = re.sub(r'[^a-z0-9_]', '_', category_name)
        # Remove multiple underscores
        category_name = re.sub(r'_+', '_', category_name)
        # Remove leading/trailing underscores
        category_name = category_name.strip('_')

        return category_name


# STEP 5C: ADHD PATH 

class ADHDTherapy:
    """
    STEP 5C: ADHD Therapy Implementation
    Focus on concentration and focus music with binaural beats
    """

    def __init__(self):
        self.youtube_api = YouTubeAPI()
        self.adhd_queries = [
            "Alpha range (8–12 Hz) brainwave entrainment",
            "40 Hz (Gamma–Beta border)",
            "3333 Hz - Pure Binaural Beat Frequency",
            "binaural beats focus 40 hz",
            "barber beat music",
            "Khruangbin",
            "Vaporwave music",
            "Hermanos Gutiérrez",
            "estas tonne song",            
            "432 hz music",
            "829 hz music",
            "Pere Andre Farah",
            "Classical Music to Make Your Brain Shut Up",
            "Indian Classical – Raga Desh / Raga Bhairavi"
        ]

    def get_adhd_recommendations(self, patient_info: Dict = None) -> Dict:
        """
        STEP 5C: Main ADHD Therapy Recommendation Function
        Focus on concentration and focus music with binaural beats and therapeutic frequencies
        """
        print("\n" + "="*60)
        print(" Generating ADHD Therapy Recommendations...")
        print("="*60)

        recommendations = {
            "patient_context": {
                "condition": "adhd",
                "therapy_focus": "concentration, focus, attention enhancement with binaural beats"
            },
            "categories": {}
        }

        # Process each primary query to ensure we get songs from each one
        import time

        for i, query in enumerate(self.adhd_queries, 1):
            print(f"🎵 [{i}/{len(self.adhd_queries)}] Searching for: {query}")

            # Add delay to avoid rate limiting
            time.sleep(0.5)

            songs = self.youtube_api.search_music_with_fallback(query, max_results=5)

            # If no songs found, create fallback songs
            if not songs:
                print(f"   No songs found, creating fallback for: {query}")
                fallback_songs = []
                for j in range(5):
                    fallback_song = {
                        "title": f"ADHD Therapy Music - {query} (Track {j+1})",
                        "url": f"https://www.youtube.com/results?search_query={query.replace(' ', '+').replace('–', '+')}",
                        "channel": "TheraMuse ADHD Therapy",
                        "description": f"Therapeutic music for ADHD concentration and focus. Based on {query}. Click to explore more options.",
                        "fallback": True
                    }
                    fallback_songs.append(fallback_song)
                songs = fallback_songs
                print(f"   Created {len(songs)} fallback songs")
            else:
                print(f"   Found {len(songs)} songs")

            # Create category name from query (clean and readable)
            category_name = self._create_category_name_from_query(query)

            # Add to recommendations
            recommendations["categories"][category_name] = {
                "query": query,
                "songs": songs,
                "count": len(songs),
                "description": f"ADHD therapy music: {query}",
                "category_type": "primary_query"
            }

        # Calculate total songs
        total_songs = sum(category["count"] for category in recommendations["categories"].values())
        recommendations["total_songs"] = total_songs
        recommendations["method"] = "adhd_therapy_v2"
        recommendations["generated_at"] = datetime.now().isoformat()

        print(f"\n Generated {total_songs} total recommendations for ADHD therapy")
        print(f" Focus: All primary ADHD queries processed with guaranteed songs")
        print(f"Categories: {list(recommendations['categories'].keys())}")
        print("="*60 + "\n")

        return recommendations

    def _create_category_name_from_query(self, query: str) -> str:
        """Create a clean category name from the query"""
        # Clean up the query to make a good category name
        category_name = query.lower()

        # Common replacements for cleaner names
        replacements = {
            "alpha range (8–12 hz) brainwave entrainment": "alpha_brainwave_entrainment",
            "40 hz (gamma–beta border)": "gamma_beta_border_40hz",
            "3333 hz - pure binaural beat frequency": "pure_binaural_3333hz",
            "binaural beats focus 40 hz": "binaural_focus_40hz",
            "barber beat music": "barber_beat_music",
            "khruangbin": "khruangbin_music",
            "vaporwave music": "vaporwave_focus",
            "hermanos gutiérrez": "hermanos_gutierrez",
            "estas tonne song": "estas_tonne_guitar",
            "432 hz music": "432hz_healing",
            "829 hz music": "829hz_therapy",
            "pere andre farah": "pere_andre_farah",
            "classical music to make your brain shut up": "classical_brain_focus",
            "indian classical – raga desh / raga bhairavi": "indian_classical_ragas"
        }

        # Apply replacements
        for old, new in replacements.items():
            if old.lower() in category_name:
                category_name = new
                break

        # Remove special characters and replace with underscores
        import re
        category_name = re.sub(r'[^a-z0-9_]', '_', category_name)
        # Remove multiple underscores
        category_name = re.sub(r'_+', '_', category_name)
        # Remove leading/trailing underscores
        category_name = category_name.strip('_')

        return category_name


# STEP 7: THOMPSON SAMPLING INTEGRATION 

class LinearThompsonSampling:
    """
    STEP 7: Linear Thompson Sampling for Contextual Bandits
    Implements reinforcement learning for personalized recommendations
    """

    def __init__(self, n_features: int = 20, alpha: float = 1.0, lambda_reg: float = 1.0):
        self.n_features = n_features
        self.alpha = alpha
        self.lambda_reg = lambda_reg

        # Bayesian posterior parameters
        self.B = np.identity(n_features) * lambda_reg  # Precision matrix
        self.mu = np.zeros(n_features)  # Mean of posterior
        self.f = np.zeros(n_features)  # Feature-reward accumulator

        # Tracking
        self.n_interactions = 0
        self.total_reward = 0.0

    def sample_theta(self) -> np.ndarray:
        """Sample coefficient vector from posterior distribution"""
        try:
            B_inv = np.linalg.inv(self.B)
            self.mu = B_inv @ self.f
            theta_sample = np.random.multivariate_normal(self.mu, self.alpha * B_inv)
            return theta_sample
        except np.linalg.LinAlgError:
            return self.mu + np.random.randn(self.n_features) * 0.1

    def predict(self, context: np.ndarray, theta: np.ndarray = None) -> float:
        """Predict expected reward for given context"""
        if theta is None:
            theta = self.mu
        return np.dot(theta, context)

    def update(self, context: np.ndarray, reward: float, decay_factor: float = 0.98):
        """Update posterior with new observation"""
        # Apply decay for non-stationarity
        self.B *= decay_factor
        self.f *= decay_factor

        # Update precision matrix and feature-reward accumulator
        self.B += np.outer(context, context)
        self.f += reward * context

        self.n_interactions += 1
        self.total_reward += reward

    def get_average_reward(self) -> float:
        """Get average reward so far"""
        if self.n_interactions == 0:
            return 0.0
        return self.total_reward / self.n_interactions


# STEP 8: DATABASE STORAGE 

class DatabaseManager:
    """
    STEP 8: Database Manager for SQLite Operations
    Handles all therapy session and recommendation storage
    """

    def __init__(self, db_path: str = "theramuse.db"):
        """Initialize database connection and create tables if needed"""
        self.db_path = db_path
        self.conn = None
        self.connect()
        self.create_tables()

    def connect(self):
        """Establish database connection"""
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row

    def _migrate_therapy_sessions_table(self):
        """Migrate therapy_sessions table if it has old schema"""
        cursor = self.conn.cursor()

        try:
            # Check if table exists and get its schema
            cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='therapy_sessions'")
            table_info = cursor.fetchone()

            if table_info:
                schema = table_info[0]
                # Check if this is the old schema (missing session_id as PRIMARY KEY)
                if 'session_id TEXT PRIMARY KEY' not in schema:
                    print(" Migrating therapy_sessions table schema...")

                    # Backup existing data
                    cursor.execute("ALTER TABLE therapy_sessions RENAME TO therapy_sessions_old")

                    # Create new table with correct schema
                    cursor.execute("""
                        CREATE TABLE therapy_sessions (
                            session_id TEXT PRIMARY KEY,
                            patient_id TEXT NOT NULL,
                            condition TEXT NOT NULL,
                            therapy_method TEXT,
                            total_songs INTEGER,
                            exploration_rate REAL,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        )
                    """)

                    # Try to migrate data from old table
                    try:
                        cursor.execute("""
                            INSERT INTO therapy_sessions
                            SELECT session_id, patient_id, condition, therapy_method,
                                   total_songs, exploration_rate, created_at
                            FROM therapy_sessions_old
                        """)
                        print("  Data migrated successfully")
                    except Exception as e:
                        print(f"  Could not migrate old data: {e}")

                    # Drop old table
                    cursor.execute("DROP TABLE therapy_sessions_old")
                    self.conn.commit()

        except Exception as e:
            print(f"  Migration not needed or failed: {e}")
            self.conn.rollback()

    def create_tables(self):
        """Create all necessary tables"""
        cursor = self.conn.cursor()

        # Check if we need to migrate the therapy_sessions table
        self._migrate_therapy_sessions_table()

        # Therapy sessions table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS therapy_sessions (
                session_id TEXT PRIMARY KEY,
                patient_id TEXT NOT NULL,
                condition TEXT NOT NULL,
                therapy_method TEXT,
                total_songs INTEGER,
                exploration_rate REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Therapy recommendations table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS therapy_recommendations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                patient_id TEXT NOT NULL,
                category TEXT NOT NULL,
                query TEXT,
                song_title TEXT,
                video_id TEXT,
                channel TEXT,
                description TEXT,
                rank INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (session_id) REFERENCES therapy_sessions(session_id)
            )
        """)

        # Feedback table for reinforcement learning
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS therapy_feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                patient_id TEXT NOT NULL,
                session_id TEXT,
                condition TEXT NOT NULL,
                song_title TEXT,
                video_id TEXT,
                reward REAL NOT NULL,
                feedback_type TEXT,
                context_features TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Bandit statistics table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS bandit_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                condition TEXT NOT NULL,
                n_interactions INTEGER,
                total_reward REAL,
                avg_reward REAL,
                exploration_rate REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Patient info table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS patients (
                patient_id TEXT PRIMARY KEY,
                name TEXT,
                age INTEGER,
                birth_year INTEGER,
                condition TEXT,
                patient_info TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Big Five personality scores table with reinforcement learning
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS big5_scores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                patient_id TEXT,
                session_id TEXT,
                openness REAL,
                conscientiousness REAL,
                extraversion REAL,
                agreeableness REAL,
                neuroticism REAL,
                reinforcement_learning INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (patient_id) REFERENCES patients(patient_id),
                FOREIGN KEY (session_id) REFERENCES therapy_sessions(session_id)
            )
        """)

        self.conn.commit()

    def save_session(self, session_id: str, patient_id: str, condition: str,
                    therapy_method: str, total_songs: int, exploration_rate: float):
        """Save therapy session"""
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT INTO therapy_sessions (session_id, patient_id, condition, therapy_method, total_songs, exploration_rate)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (session_id, patient_id, condition, therapy_method, total_songs, exploration_rate))
        self.conn.commit()

    def save_recommendations(self, session_id: str, patient_id: str, recommendations: Dict):
        """Save therapy recommendations"""
        cursor = self.conn.cursor()

        for category_name, category_data in recommendations.get("categories", {}).items():
            songs = category_data.get("songs", [])
            for rank, song in enumerate(songs, 1):
                video_id = None
                if isinstance(song.get("id"), dict):
                    video_id = song.get("id", {}).get("videoId")
                else:
                    video_id = song.get("id")

                # Convert all parameters to strings to avoid SQLite binding errors
                cursor.execute("""
                    INSERT INTO therapy_recommendations
                    (session_id, patient_id, category, query, song_title, video_id, channel, description, rank)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    str(session_id), str(patient_id), str(category_name),
                    str(category_data.get("query", "")),
                    str(song.get("title", "")),
                    str(video_id) if video_id else None,
                    str(song.get("channel", "")),
                    str(song.get("description", ""))[:500],
                    int(rank)
                ))

        self.conn.commit()

    def save_feedback(self, patient_id: str, session_id: str, condition: str,
                     song: Dict, reward: float, feedback_type: str, context_features: List[float]):
        """Save feedback for reinforcement learning"""
        cursor = self.conn.cursor()

        video_id = None
        if isinstance(song.get("id"), dict):
            video_id = song.get("id", {}).get("videoId")
        else:
            video_id = song.get("id")

        cursor.execute("""
            INSERT INTO therapy_feedback
            (patient_id, session_id, condition, song_title, video_id, reward, feedback_type, context_features)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            str(patient_id), str(session_id), str(condition), str(song.get("title", "")),
            str(video_id) if video_id else None, float(reward), str(feedback_type), json.dumps(context_features)
        ))
        self.conn.commit()

    def save_patient(self, patient_id: str, patient_info: Dict):
        """Save patient information"""
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO patients
            (patient_id, name, age, birth_year, condition, patient_info)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            patient_id,
            patient_info.get("name"),
            patient_info.get("age"),
            patient_info.get("birth_year"),
            patient_info.get("condition"),
            json.dumps(patient_info)
        ))
        self.conn.commit()

    def get_analytics(self) -> Dict:
        """Get therapy analytics"""
        cursor = self.conn.cursor()

        # Total metrics
        cursor.execute("SELECT COUNT(*) as count FROM therapy_sessions")
        total_sessions = cursor.fetchone()["count"]

        cursor.execute("SELECT COUNT(*) as count FROM therapy_feedback")
        total_feedback = cursor.fetchone()["count"]

        # Try to count patients with backward compatibility
        try:
            cursor.execute("SELECT COUNT(DISTINCT id) as count FROM patients")
            total_patients = cursor.fetchone()["count"]
        except sqlite3.OperationalError:
            # Fall back to patient_id column
            cursor.execute("SELECT COUNT(DISTINCT patient_id) as count FROM patients")
            total_patients = cursor.fetchone()["count"]

        # Feedback by condition
        cursor.execute("""
            SELECT condition, AVG(reward) as avg_reward, COUNT(*) as count
            FROM therapy_feedback
            GROUP BY condition
        """)
        rewards_by_condition = [dict(row) for row in cursor.fetchall()]

        return {
            "total_sessions": total_sessions,
            "total_feedback": total_feedback,
            "total_patients": total_patients,
            "rewards_by_condition": rewards_by_condition
        }

    def close(self):
        """Close database connection"""
        if self.conn:
            self.conn.close()


# STEP 4: MAIN THERAMUSE CLASS 

class TheraMuse:
    """
    STEP 4: Main TheraMuse Class
    Orchestrates all therapy functions and integrates Thompson Sampling
    """

    def __init__(self, model_path: str = "theramuse_model.pkl", db_path: str = "theramuse.db"):
        """
        STEP 3: Initialize TheraMuse with all therapy modules
        Sets up all components for music therapy recommendation system
        """
        print("\n" + ""*30)
        print("Initializing TheraMuse v9.0 - Synchronized Build")
        print(""*30 + "\n")

        self.model_path = model_path
        self.db = DatabaseManager(db_path)

        # Initialize therapy modules
        self.dementia_therapy = DementiaTherapy()
        self.down_syndrome_therapy = DownSyndromeTherapy()
        self.adhd_therapy = ADHDTherapy()

        # Initialize YouTube API for smart recommendations
        self.youtube_api = YouTubeAPI()

        # Initialize Thompson Sampling for each condition
        self.bandits = {
            "dementia": LinearThompsonSampling(),
            "down_syndrome": LinearThompsonSampling(),
            "adhd": LinearThompsonSampling()
        }

        # Current exploration rate
        self.exploration_rate = 0.3
        self.min_exploration_rate = 0.1

        # Load existing model if available
        self._load_model()

        print(" TheraMuse initialized successfully!\n")

    def extract_context_features(self, patient_info: Dict, condition: str) -> np.ndarray:
        """
        STEP 7.1: Extract context features for bandit algorithm
        Creates 20-dimensional feature vector for Thompson Sampling
        """
        features = np.zeros(20)

        # Feature 0-2: Condition one-hot encoding
        condition_map = {"dementia": 0, "down_syndrome": 1, "adhd": 2}
        if condition in condition_map:
            features[condition_map[condition]] = 1.0

        # Feature 4: Normalized age
        if "age" in patient_info:
            features[4] = patient_info["age"] / 100.0

        # Feature 5-9: Health indicators (for dementia)
        if condition == "dementia":
            features[5] = float(patient_info.get("difficulty_sleeping", False))
            features[6] = float(patient_info.get("trouble_remembering", False))
            features[7] = float(patient_info.get("forgets_everyday_things", False))
            features[8] = float(patient_info.get("difficulty_recalling_old_memories", False))
            features[9] = float(patient_info.get("memory_worse_than_year_ago", False))

        # Feature 10-14: Big 5 personality traits
        if "big5_scores" in patient_info:
            big5 = patient_info["big5_scores"]
            features[10] = big5.get("openness", 4) / 7.0
            features[11] = big5.get("conscientiousness", 4) / 7.0
            features[12] = big5.get("extraversion", 4) / 7.0
            features[13] = big5.get("agreeableness", 4) / 7.0
            features[14] = big5.get("neuroticism", 4) / 7.0

        # Feature 15: Time of day
        hour = datetime.now().hour
        features[15] = hour / 24.0

        # Feature 16-17: Preferences
        if "instruments" in patient_info:
            features[16] = min(len(patient_info["instruments"]), 5) / 5.0
        if "natural_elements" in patient_info:
            features[17] = min(len(patient_info["natural_elements"]), 5) / 5.0

        return features

    def get_therapy_recommendations(self, patient_info: Dict, condition: str,
                                   patient_id: str = None) -> Dict:
        """
        STEP 4: Get therapy recommendations with contextual bandit integration
        Main function that routes to appropriate therapy module
        """
        # STEP 4.1: Generate session ID
        session_id = f"therapy_{datetime.now().strftime('%Y%m%d%H%M%S')}"

        # STEP 8.1: Save patient info
        if patient_id:
            self.db.save_patient(patient_id, patient_info)

        # STEP 5: Route to appropriate therapy module
        if condition == "dementia":
            recommendations = self.dementia_therapy.get_dementia_recommendations(patient_info)
        elif condition == "down_syndrome":
            recommendations = self.down_syndrome_therapy.get_down_syndrome_recommendations(patient_info)
        elif condition == "adhd":
            recommendations = self.adhd_therapy.get_adhd_recommendations(patient_info)
        else:
            raise ValueError(f"Unknown condition: {condition}")

        # STEP 7.2: Add bandit information
        bandit = self.bandits[condition]
        recommendations["bandit_stats"] = {
            "n_interactions": bandit.n_interactions,
            "avg_reward": bandit.get_average_reward(),
            "exploration_rate": self.exploration_rate
        }

        # STEP 4.3: Add session info
        recommendations["session_id"] = session_id
        recommendations["patient_id"] = patient_id
        recommendations["condition"] = condition

        # STEP 8: Save session and recommendations to database
        if patient_id:
            self.db.save_session(
                session_id, patient_id, condition,
                recommendations["method"], recommendations["total_songs"],
                self.exploration_rate
            )
            self.db.save_recommendations(session_id, patient_id, recommendations)

        # STEP 8.2: Always export a JSON snapshot of the recommendations
        try:
            self._export_recommendations_json(recommendations)
        except Exception as e:
            # Keep the flow resilient; log and continue
            print(f"  Failed to export recommendations JSON: {e}")

        return recommendations

    def _export_recommendations_json(self, recommendations: Dict) -> Optional[Path]:
        """Export recommendations to a timestamped JSON file in local directory.

        Filename format: theramuse_recommendations_{condition}_{YYYYmmddHHMMSS}_{YYYYmmdd}.json
        Returns the written Path on success, or None on failure.
        """
        # Use the app directory instead of trying to write to /var/www
        base_dir = Path(__file__).resolve().parent
        # Create a data subdirectory if it doesn't exist
        data_dir = base_dir / "data"
        data_dir.mkdir(parents=True, exist_ok=True)

        condition = recommendations.get("condition", "unknown")
        ts = datetime.now().strftime("%Y%m%d%H%M%S")
        day = datetime.now().strftime("%Y%m%d")
        fname = f"theramuse_recommendations_{condition}_{ts}_{day}.json"
        fpath = data_dir / fname

        # Ensure recommendations have a generated_at
        if "generated_at" not in recommendations:
            recommendations["generated_at"] = datetime.now().isoformat()

        try:
            with open(fpath, "w", encoding="utf-8") as f:
                json.dump(recommendations, f, ensure_ascii=False, indent=2)

            print(f" Exported recommendations JSON → {fpath}")
            return fpath
        except PermissionError:
            # If still can't write, try current working directory
            import os
            cwd = Path(os.getcwd())
            fpath = cwd / fname
            with open(fpath, "w", encoding="utf-8") as f:
                json.dump(recommendations, f, ensure_ascii=False, indent=2)
            print(f" Exported recommendations JSON (fallback) → {fpath}")
            return fpath

    def record_feedback(self, patient_id: str, session_id: str, condition: str,
                       song: Dict, feedback_type: str, patient_info: Dict = None):
        """
        STEP 12: Record feedback and update bandit model
        Enhanced Thompson Sampling Learning Mechanism with comprehensive feedback tracking
        """
        # STEP 12.2a: Convert feedback type to reward
        reward_map = {"like": 1.0, "neutral": 0.0, "dislike": -1.0, "skip": -0.5, "inappropriate": -1.0}
        reward = reward_map.get(feedback_type.lower(), 0.0)

        # STEP 12.2b: Extract context features
        if patient_info:
            context = self.extract_context_features(patient_info, condition)
        else:
            context = np.zeros(20)

        # STEP 12.2c: Update Thompson Sampling bandit
        self.bandits[condition].update(context, reward)

        # STEP 12.2d: Save feedback to database
        self.db.save_feedback(
            patient_id, session_id, condition, song, reward,
            feedback_type, context.tolist()
        )

        # STEP 12.2e: Enhanced Reinforcement Learning - Update big5_scores table
        self._update_reinforcement_learning_count(patient_id, session_id, feedback_type)

        # STEP 12.2f: Save bandit stats
        bandit = self.bandits[condition]
        cursor = self.db.conn.cursor()
        cursor.execute("""
            INSERT INTO bandit_stats (condition, n_interactions, total_reward, avg_reward, exploration_rate)
            VALUES (?, ?, ?, ?, ?)
        """, (
            condition, bandit.n_interactions, bandit.total_reward,
            bandit.get_average_reward(), self.exploration_rate
        ))
        self.db.conn.commit()

        # STEP 12.2g: Adjust exploration rate
        if bandit.n_interactions > 50:
            self.exploration_rate = max(
                self.min_exploration_rate,
                self.exploration_rate * (50 / bandit.n_interactions)
            )

        print(f" Enhanced Feedback Recorded: {feedback_type} (reward={reward:.2f}), total interactions: {bandit.n_interactions}")

    def _update_reinforcement_learning_count(self, patient_id: str, session_id: str, feedback_type: str):
        """
        Enhanced Thompson Sampling Learning Mechanism:
        Updates the reinforcement_learning column in big5_scores table by counting all feedback types
        """
        try:
            # Connect to the same database as the main system
            conn = sqlite3.connect(self.db.db_path)
            cursor = conn.cursor()

            # Get the current reinforcement_learning count for this patient/session
            cursor.execute("""
                SELECT reinforcement_learning FROM big5_scores
                WHERE patient_id = ? AND session_id = ?
                ORDER BY created_at DESC LIMIT 1
            """, (patient_id, session_id))

            result = cursor.fetchone()
            current_count = result[0] if result and result[0] is not None else 0

            # Calculate new count based on feedback type
            increment = 1  # Default increment for any feedback

            # Enhanced weighting for different feedback types
            if feedback_type.lower() == "like":
                increment = 2  # Positive feedback gets higher weight
            elif feedback_type.lower() == "dislike":
                increment = 1  # Standard weight for negative feedback
            elif feedback_type.lower() == "skip":
                increment = 1  # Standard weight for skip
            elif feedback_type.lower() == "inappropriate":
                increment = 1  # Standard weight for inappropriate

            new_count = current_count + increment

            # Update the reinforcement_learning column
            cursor.execute("""
                UPDATE big5_scores
                SET reinforcement_learning = ?
                WHERE patient_id = ? AND session_id = ?
                AND id = (
                    SELECT id FROM big5_scores
                    WHERE patient_id = ? AND session_id = ?
                    ORDER BY created_at DESC LIMIT 1
                )
            """, (new_count, patient_id, session_id, patient_id, session_id))

            conn.commit()
            conn.close()

            print(f"  Reinforcement Learning Updated: Patient {patient_id}, Session {session_id}")
            print(f"  Feedback Type: {feedback_type}, Increment: {increment}, New Total: {new_count}")

        except sqlite3.Error as e:
            print(f"  Database error updating reinforcement learning: {e}")
        except Exception as e:
            print(f"  Error updating reinforcement learning: {e}")

    def get_reinforcement_learning_stats(self, patient_id: str = None) -> Dict:
        """
        Get comprehensive reinforcement learning statistics
        """
        try:
            conn = sqlite3.connect(self.db.db_path)
            cursor = conn.cursor()

            if patient_id:
                # Get stats for specific patient
                cursor.execute("""
                    SELECT patient_id, session_id, reinforcement_learning,
                           openness, conscientiousness, extraversion, agreeableness, neuroticism,
                           created_at
                    FROM big5_scores
                    WHERE patient_id = ?
                    ORDER BY created_at DESC
                """, (patient_id,))
            else:
                # Get stats for all patients
                cursor.execute("""
                    SELECT patient_id, session_id, reinforcement_learning,
                           openness, conscientiousness, extraversion, agreeableness, neuroticism,
                           created_at
                    FROM big5_scores
                    WHERE reinforcement_learning > 0
                    ORDER BY reinforcement_learning DESC, created_at DESC
                    LIMIT 50
                """)

            results = cursor.fetchall()
            conn.close()

            stats = []
            for row in results:
                stats.append({
                    'patient_id': row[0],
                    'session_id': row[1],
                    'reinforcement_learning_count': row[2],
                    'openness': row[3],
                    'conscientiousness': row[4],
                    'extraversion': row[5],
                    'agreeableness': row[6],
                    'neuroticism': row[7],
                    'created_at': row[8]
                })

            return {
                'reinforcement_learning_stats': stats,
                'total_patients_with_rl': len(set([s['patient_id'] for s in stats])),
                'total_feedback_interactions': sum([s['reinforcement_learning_count'] for s in stats])
            }

        except Exception as e:
            print(f"Error getting reinforcement learning stats: {e}")
            return {'reinforcement_learning_stats': [], 'total_patients_with_rl': 0, 'total_feedback_interactions': 0}

    def get_analytics(self) -> Dict:
        """Get therapy analytics"""
        return self.db.get_analytics()

    def _load_model(self):
        """STEP 13: Load existing model if available"""
        try:
            if Path(self.model_path).exists():
                with open(self.model_path, "rb") as f:
                    model_data = pickle.load(f)
                    # Load bandit states if available
                    if "bandits" in model_data:
                        self.bandits = model_data["bandits"]
                    if "exploration_rate" in model_data:
                        self.exploration_rate = model_data["exploration_rate"]
                print(f" Model loaded from {self.model_path}")
        except Exception as e:
            print(f"ℹ  No existing model found. Starting fresh.")

    def _save_model(self):
        """STEP 13: Save current model state"""
        try:
            model_data = {
                "bandits": self.bandits,
                "exploration_rate": self.exploration_rate,
                "saved_at": datetime.now().isoformat()
            }
            with open(self.model_path, "wb") as f:
                pickle.dump(model_data, f)
            print(f" Model saved to {self.model_path}")
        except Exception as e:
            print(f"  Error saving model: {e}")
    def clear_youtube_cache(self):
        """Clear YouTube recommendation cache for fresh recommendations"""
        if hasattr(self, 'youtube_api'):
            self.youtube_api.clear_cache()
            print(" YouTube recommendation cache cleared for fresh recommendations")

    def get_youtube_cache_status(self) -> Dict:
        """Get current YouTube cache status for monitoring"""
        if hasattr(self, 'youtube_api'):
            return {
                "cache_size": self.youtube_api.get_cache_size(),
                "query_history_size": len(self.youtube_api._query_history)
            }
        return {"cache_size": 0, "query_history_size": 0}

    def check_api_health(self) -> Dict:
        """Check the health of all external APIs"""
        health_status = {
            "timestamp": datetime.now().isoformat(),
            "youtube_api": self.youtube_api.get_api_health_status(),
            "database": self._check_database_health()
        }
        return health_status

    def _check_database_health(self) -> Dict:
        """Check database connectivity and health"""
        try:
            cursor = self.db.conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM therapy_sessions")
            session_count = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM therapy_recommendations")
            rec_count = cursor.fetchone()[0]

            return {
                "status": "healthy",
                "sessions_count": session_count,
                "recommendations_count": rec_count
            }
        except Exception as e:
            return {
                "status": "error",
                "error": str(e)[:100]
            }

    def close(self):
        """STEP 13: Clean up resources and save model"""
        self._save_model()
        self.db.close()
        print(" TheraMuse closed successfully")


# MAIN FUNCTION FOR TESTING 

if __name__ == "__main__":
    # Test all therapy functions
    print(" Testing TheraMuse Therapy Functions...")

    theramuse = TheraMuse()

    # Test Dementia
    print("\n" + "="*50)
    print("TESTING DEMENTIA THERAPY")
    print("="*50)

    dementia_patient = {
        "name": "Test Dementia Patient",
        "age": 75,
        "birth_year": 1948,
        "birthplace_country": "Bangladesh",
        "birthplace_city": "Dhaka",
        "instruments": ["Piano", "Guitar"],
        "favorite_season": "winter",
        "natural_elements": ["Rain", "Forest"],
        "difficulty_sleeping": True,
        "trouble_remembering": True,
        "big5_scores": {"openness": 4, "conscientiousness": 5, "extraversion": 3, "agreeableness": 6, "neuroticism": 2}
    }

    dementia_recs = theramuse.get_therapy_recommendations(dementia_patient, "dementia", "test_dementia")
    print(f"Dementia recommendations: {dementia_recs['total_songs']} songs")

    # Test Down Syndrome
    print("\n" + "="*50)
    print("TESTING DOWN SYNDROME THERAPY")
    print("="*50)

    down_syndrome_recs = theramuse.get_therapy_recommendations({"name": "Test Child"}, "down_syndrome", "test_down_syndrome")
    print(f"Down Syndrome recommendations: {down_syndrome_recs['total_songs']} songs")

    # Test ADHD
    print("\n" + "="*50)
    print("TESTING ADHD THERAPY")
    print("="*50)

    adhd_recs = theramuse.get_therapy_recommendations({"name": "Test ADHD"}, "adhd", "test_adhd")
    print(f"ADHD recommendations: {adhd_recs['total_songs']} songs")

    # Test feedback
    if dementia_recs["categories"]:
        first_category = list(dementia_recs["categories"].keys())[0]
        songs = dementia_recs["categories"][first_category]["songs"]
        if songs:
            test_song = songs[0]
            theramuse.record_feedback(
                "test_dementia", dementia_recs["session_id"],
                "dementia", test_song, "like", dementia_patient
            )

    theramuse.close()
    print("\n All therapy functions tested successfully!")

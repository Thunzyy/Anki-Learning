import base64

import pytest

from app.models import Card, Settings


@pytest.fixture(autouse=True)
def isolated_image_cache(tmp_path, monkeypatch):
    from app import cache
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(cache, "CACHE_MEDIA", tmp_path / "cache" / "media")
    cache.reset()
from app.services.images import (
    fetch_image,
    illustration_prompt,
    image_generation_status,
    image_url_is_unrelated,
    image_url_is_weak,
    page_title_matches,
    score_sense,
)


def _settings() -> Settings:
    return Settings()


def test_image_url_is_unrelated_detects_compound_filenames():
    assert image_url_is_unrelated(
        "half",
        "moitié",
        "https://upload.wikimedia.org/wikipedia/commons/thumb/5/5d/Half_raised_flag.jpg/960px-Half_raised_flag.jpg",
    )
    assert image_url_is_unrelated("half", "moitié", "https://example.com/Half-mast.jpg")
    assert image_url_is_unrelated("more", "plus", "https://example.com/Thomas_More.jpg")
    assert not image_url_is_unrelated(
        "half",
        "moitié",
        "https://commons.wikimedia.org/wiki/Special:FilePath/2-4.svg?width=640",
    )
    assert not image_url_is_unrelated(
        "house",
        "maison",
        "https://commons.wikimedia.org/wiki/Special:FilePath/Ranch%20style%20home%20in%20Salinas%2C%20California.JPG?width=640",
    )


def test_svg_diagram_is_too_weak_for_a_vocab_card():
    svg = "https://commons.wikimedia.org/wiki/Special:FilePath/2-4.svg?width=640"
    photo = "https://commons.wikimedia.org/wiki/Special:FilePath/Ranch%20style%20home%20in%20Salinas%2C%20California.JPG?width=640"
    assert image_url_is_weak("half", "moitié", svg)
    assert not image_url_is_weak("house", "maison", photo)


def test_generated_illustrations_are_never_weak():
    """Concept files are named gen_<sense>.jpg, which the compound heuristic misreads."""
    assert not image_url_is_weak("exam", "examen", "/api/cache-media/concepts/gen_examen.jpg")
    assert not image_url_is_weak("march", "mars / marcher", "/api/cache-media/concepts/gen_mars_marcher.jpg")
    assert not image_url_is_weak("exam", "examen", "/api/media/legacy-en-ngsl/exam.jpg")
    assert image_url_is_weak("exam", "examen", "")


def test_image_generation_status_requires_grok_session(monkeypatch):
    monkeypatch.setattr(
        "app.services.images.resolve_for_provider",
        lambda *_a, **_k: type("S", (), {"value": "", "extra_headers": {}})(),
    )
    ok, reason = image_generation_status("claude", Settings())
    assert ok is False
    assert "claude" in reason.lower() or "Grok" in reason
    ok, reason = image_generation_status("local", Settings())
    assert ok is False
    assert "Grok" in reason or "local" in reason.lower()
    ok, reason = image_generation_status("grok", Settings())
    assert ok is False
    assert "connect" in reason.lower() or "clé" in reason.lower() or "OAuth" in reason


def test_image_generation_status_ok_when_grok_key_present():
    ok, reason = image_generation_status("grok", Settings(xai_api_key="xai-test"))
    assert ok is True
    assert reason == ""
    ok, _ = image_generation_status("xai", Settings(xai_api_key="xai-test"))
    assert ok is True


def test_illustration_prompt_asks_for_the_word_sense():
    text = illustration_prompt("half", "moitié")
    assert "half" in text.lower()
    assert "moitié" in text.lower()
    assert "text" in text.lower() or "texte" in text.lower()


def test_illustration_prompt_stays_general_for_every_word():
    half = illustration_prompt("half", "moitié")
    school = illustration_prompt("school", "école")
    assert "apple" not in half.lower()
    assert "apple" not in school.lower()
    assert "school" in school.lower()
    assert "école" in school or "ecole" in school.lower()


def test_illustration_prompt_uses_settings_template():
    text = illustration_prompt(
        "school",
        "école",
        template="Photo of {lemma} meaning {french}. Sense: {sense}.",
    )
    assert text == "Photo of school meaning école. Sense: school (école)."


def test_illustration_prompt_empty_template_falls_back_to_default():
    text = illustration_prompt("house", "maison", template="   ")
    assert "house" in text.lower()
    assert "maison" in text.lower()
    assert "{lemma}" not in text


def test_illustration_prompt_includes_example_phrase():
    text = illustration_prompt(
        "mix",
        "mélanger",
        phrase="Mix the flour with the eggs carefully.",
    )
    assert "flour" in text.lower()
    assert "eggs" in text.lower()
    assert "{phrase}" not in text


def test_default_illustration_prompt_centers_the_target_and_bilingual_scene():
    text = illustration_prompt(
        "mix",
        "mélanger",
        phrase="Mix the flour with the eggs carefully.",
        phrase_fr="Mélange soigneusement la farine avec les œufs.",
        category="Verbe",
    )
    low = text.lower()
    assert "mix" in low
    assert "mélanger" in low
    assert "mélange soigneusement" in low
    assert "verbe" in low
    assert "central" in low or "principal" in low
    assert "incidental" in low or "secondaire" in low


def test_default_prompt_asks_for_the_action_not_the_result():
    text = illustration_prompt("mix", "mélanger", phrase="Mix the flour with the eggs carefully.")
    low = text.lower()
    assert "action" in low or "result" in low
    assert "apple" not in low


def test_page_title_must_be_the_word_itself():
    assert page_title_matches("dog", "Dog")
    assert page_title_matches("house", "House (building)")
    assert not page_title_matches("half", "Half-mast")
    assert not page_title_matches("half", "Half raised flag")
    assert not page_title_matches("more", "Thomas More")
    assert not page_title_matches("think", "Thought")
    assert not page_title_matches("few", "Mark Few")


def test_score_sense_prefers_bilingual_vocab_item():
    half_concept = score_sense(
        "half",
        "moitié",
        labels={"en": "half", "fr": "moitié"},
        aliases={"en": ["one-half", "½"]},
        instance_of=["Q66055"],
    )
    fraction = score_sense(
        "half",
        "moitié",
        labels={"en": "½", "fr": "demi"},
        aliases={"en": ["half", "one half", "1/2"]},
        instance_of=["Q263865"],
    )
    assert half_concept > 0
    assert fraction > 0
    assert half_concept > fraction


def test_score_sense_rejects_homonyms_and_people():
    assert score_sense(
        "half",
        "moitié",
        labels={"en": "Half-Life", "fr": "Half-Life"},
        aliases={"en": ["Half-Life series"]},
        instance_of=["Q7889"],
    ) == 0
    assert score_sense(
        "half",
        "moitié",
        labels={"en": "Willem Kalf", "fr": "Willem Kalf"},
        aliases={"en": ["Half"]},
        instance_of=["Q5"],
    ) == 0
    assert score_sense(
        "more",
        "plus",
        labels={"en": "Thomas More", "fr": "Thomas More"},
        aliases={"en": ["More", "Sir Thomas More"]},
        instance_of=["Q5"],
    ) == 0
    assert score_sense(
        "house",
        "maison",
        labels={"en": "House", "fr": "Dr House"},
        aliases={"en": ["House M.D."]},
        instance_of=["Q5398426"],
    ) == 0


def test_score_sense_requires_the_source_word_when_french_differs():
    assert score_sense(
        "more",
        "plus",
        labels={"en": "PLUS", "fr": "PLUS"},
        aliases={},
        instance_of=["Q18043413"],
    ) == 0
    assert score_sense(
        "more",
        "plus",
        labels={"en": "plus sign", "fr": "signe plus"},
        aliases={"en": ["plus"], "fr": ["plus", "+"]},
        instance_of=[],
    ) == 0


def test_score_sense_rejects_brand_matching_french_only():
    assert score_sense(
        "more",
        "plus",
        labels={"en": "Plus", "fr": "Plus", "nl": "Plus"},
        aliases={},
        instance_of=["Q4830453"],
    ) == 0


def test_score_sense_rejects_a_place_named_like_the_word():
    assert score_sense(
        "more",
        "plus",
        labels={"en": "More", "fr": "More"},
        aliases={},
        instance_of=["Q532"],
        located=True,
    ) == 0


def test_score_sense_accepts_house_not_house_music():
    building = score_sense(
        "house",
        "maison",
        labels={"en": "house", "fr": "maison"},
        aliases={"en": ["living house"]},
        instance_of=["Q811102"],
    )
    music = score_sense(
        "house",
        "maison",
        labels={"en": "house", "fr": "house"},
        aliases={"en": ["house music"]},
        instance_of=["Q188451"],
    )
    assert building > 0
    assert building > music


class FakeResponse:
    def __init__(self, code, payload):
        self.status_code = code
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")


class FakeClient:
    last_post = None

    def __init__(self, routes):
        self.routes = routes
        type(self).last_post = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, url, params=None, headers=None, json=None):
        payload = {"url": url, "json": json or {}}
        type(self).last_post = payload
        self.last_post = payload
        if "images/generations" in url:
            payload = self.routes.get("generate") or {}
            return FakeResponse(payload.get("status", 200), payload.get("body", {"data": []}))
        return FakeResponse(200, {})

    def get(self, url, params=None, headers=None):
        params = params or {}
        if "wikidata.org" in url and params.get("action") == "wbsearchentities":
            hits = self.routes.get(("search", params.get("search"), params.get("language")), [])
            return FakeResponse(200, {"search": [{"id": qid} for qid in hits]})
        if "wikidata.org" in url and params.get("action") == "wbgetentities":
            wanted = (params.get("ids") or "").split("|")
            entities = {qid: self.routes["entities"][qid] for qid in wanted if qid in self.routes.get("entities", {})}
            return FakeResponse(200, {"entities": entities})
        if "page/summary" in url:
            title = url.rsplit("/", 1)[-1]
            return FakeResponse(200, self.routes.get(("summary", title), {
                "type": "standard",
                "title": title.replace("_", " "),
                "originalimage": None,
                "thumbnail": None,
            }))
        if params.get("generator") == "search" and "wikipedia.org/w/api.php" in url:
            return FakeResponse(200, {"query": {"pages": self.routes.get("wiki_search", {})}})
        if "api.unsplash.com" in url:
            return FakeResponse(200, {"results": self.routes.get("unsplash", [])})
        if "api.pexels.com" in url:
            return FakeResponse(200, {"photos": self.routes.get("pexels", [])})
        return FakeResponse(200, {})


def test_fetch_image_skips_unrelated_search_hit(monkeypatch):
    routes = {
        "wiki_search": {
            "1": {
                "title": "Half-mast",
                "thumbnail": {"source": "https://example.com/flag.jpg"},
            }
        },
        ("summary", "half"): {
            "type": "standard",
            "title": "One half",
            "originalimage": None,
            "thumbnail": None,
        },
        ("summary", "Half"): {
            "type": "standard",
            "title": "One half",
            "originalimage": None,
            "thumbnail": None,
        },
    }

    monkeypatch.setattr("app.services.images.httpx.Client", lambda *a, **k: FakeClient(routes))
    card = Card(id="c1", lemma="half", surface="half", language="en", french="moitié")
    url, source = fetch_image(card, "wikimedia", _settings())
    assert url == ""
    assert source == ""


def test_fetch_image_generates_when_wikidata_only_has_a_diagram(monkeypatch, tmp_path):
    routes = {
        ("search", "half", "en"): ["Q193581", "Q2114394"],
        ("search", "moitié", "fr"): ["Q39373172"],
        "entities": {
            "Q193581": {
                "labels": {"en": {"value": "Half-Life 2"}, "fr": {"value": "Half-Life 2"}},
                "aliases": {},
                "claims": {
                    "P31": [{"mainsnak": {"datavalue": {"value": {"id": "Q7889"}}}}],
                    "P18": [{"mainsnak": {"datavalue": {"value": "Half-Life_2_cover.jpg"}}}],
                },
                "sitelinks": {},
            },
            "Q39373172": {
                "labels": {"en": {"value": "half"}, "fr": {"value": "moitié"}},
                "aliases": {"en": [{"value": "one-half"}]},
                "claims": {"P31": [{"mainsnak": {"datavalue": {"value": {"id": "Q66055"}}}}]},
                "sitelinks": {"enwiki": {"title": "Half"}, "frwiki": {"title": "Moitié"}, "commonswiki": {"title": "Category:One-half"}},
            },
            "Q2114394": {
                "labels": {"en": {"value": "½"}, "fr": {"value": "demi"}},
                "aliases": {"en": [{"value": "half"}, {"value": "one half"}, {"value": "1/2"}]},
                "claims": {
                    "P31": [{"mainsnak": {"datavalue": {"value": {"id": "Q263865"}}}}],
                    "P18": [
                        {"mainsnak": {"datavalue": {"value": "Ireland-Stamp-1940-HalfCentPostageDue.jpg"}}},
                        {"mainsnak": {"datavalue": {"value": "2-4.svg"}}},
                    ],
                },
                "sitelinks": {"enwiki": {"title": "One half"}},
            },
        },
        "wiki_search": {
            "1": {
                "title": "Half-mast",
                "thumbnail": {"source": "https://example.com/flag.jpg"},
            }
        },
        ("summary", "Half"): {"type": "standard", "title": "One half", "thumbnail": None},
        ("summary", "Moitié"): {
            "type": "standard",
            "title": "Moitié",
            "thumbnail": {"source": "https://upload.example/2-4.svg/langfr-40px-2-4.svg.png", "width": 40},
        },
        ("summary", "One_half"): {"type": "standard", "title": "One half", "thumbnail": None},
    }
    png = base64.b64encode(b"\x89PNG\r\n\x1a\nfake-half").decode("ascii")
    routes["generate"] = {"status": 200, "body": {"data": [{"b64_json": png}]}}
    monkeypatch.setattr("app.services.images.httpx.Client", lambda *a, **k: FakeClient(routes))
    monkeypatch.setattr(
        "app.services.images.resolve_for_provider",
        lambda *_a, **_k: type("S", (), {"value": "test-key", "extra_headers": {}})(),
    )
    media = tmp_path / "media"
    monkeypatch.setattr("app.services.images.MEDIA_DIR", media)
    card = Card(id="c1", lemma="half", surface="half", language="en", french="moitié")
    url, source = fetch_image(card, "wikimedia", _settings(), deck_id="deck-half", allow_generate=True)
    assert source == "grok"
    assert url.startswith("/api/media/deck-half/half-") and url.endswith(".jpg")
    assert (media / "deck-half" / url.rsplit("/", 1)[-1]).read_bytes().startswith(b"\x89PNG")
    assert "half" in (FakeClient.last_post["json"].get("prompt") or "").lower()
    assert "2-4.svg" not in url
    assert "flag.jpg" not in url


def test_fetch_image_does_not_generate_during_mass_fill(monkeypatch, tmp_path):
    routes = {
        ("search", "half", "en"): [],
        ("search", "moitié", "fr"): [],
        "generate": {"status": 200, "body": {"data": [{"b64_json": "aaaa"}]}},
    }
    monkeypatch.setattr("app.services.images.httpx.Client", lambda *a, **k: FakeClient(routes))
    monkeypatch.setattr(
        "app.services.images.resolve_for_provider",
        lambda *_a, **_k: type("S", (), {"value": "test-key", "extra_headers": {}})(),
    )
    monkeypatch.setattr("app.services.images.MEDIA_DIR", tmp_path / "media")
    card = Card(id="c1", lemma="half", surface="half", language="en", french="moitié")
    url, source = fetch_image(card, "wikimedia", _settings(), deck_id="deck-half", allow_generate=False)
    assert url == ""
    assert source == ""
    assert FakeClient.last_post is None


def test_fetch_image_grok_bank_skips_wikimedia(monkeypatch):
    called = {"wiki": 0, "gen": 0}

    def fake_wiki(_card):
        called["wiki"] += 1
        return (("https://example.com/house.jpg", "wikimedia"), {})

    def fake_gen(_card, _settings, deck_id):
        called["gen"] += 1
        return f"/api/media/{deck_id}/house.jpg", "grok"

    monkeypatch.setattr("app.services.images._wikimedia", fake_wiki)
    monkeypatch.setattr("app.services.images._generate", fake_gen)
    card = Card(id="c1", lemma="house", surface="house", language="en", french="maison")
    url, source = fetch_image(
        card,
        "grok",
        Settings(xai_api_key="xai-test"),
        deck_id="d1",
        allow_generate=True,
    )
    assert called["wiki"] == 0
    assert called["gen"] == 1
    assert source == "grok"
    assert url == "/api/media/d1/house.jpg"


def test_wikimedia_url_needs_replacement():
    from app.services.images import image_is_stock

    assert image_is_stock(
        "https://commons.wikimedia.org/wiki/Special:FilePath/House.JPG",
        "wikimedia",
    )
    assert not image_is_stock("/api/media/d/house.jpg", "generated")


def test_image_is_generated_detects_local_ai_files():
    from app.services.images import image_is_generated

    assert image_is_generated("/api/media/d/half.jpg", "generated")
    assert image_is_generated("/api/cache-media/concepts/half.jpg", "cache")
    assert not image_is_generated(
        "https://commons.wikimedia.org/wiki/Special:FilePath/Ranch%20style%20home.JPG",
        "wikimedia",
    )
    assert not image_is_generated("https://example.com/house.jpg", "")


def test_fetch_image_force_generate_skips_wikimedia(monkeypatch):
    called = {"wiki": 0, "gen": 0}

    def fake_wiki(_card):
        called["wiki"] += 1
        return (("https://example.com/house.jpg", "wikimedia"), {})

    def fake_gen(_card, _settings, deck_id):
        called["gen"] += 1
        return f"/api/media/{deck_id}/house.jpg", "grok"

    monkeypatch.setattr("app.services.images._wikimedia", fake_wiki)
    monkeypatch.setattr("app.services.images._generate", fake_gen)
    card = Card(id="c1", lemma="house", surface="house", language="en", french="maison")
    url, source = fetch_image(
        card,
        "wikimedia",
        Settings(xai_api_key="xai-test"),
        deck_id="deck-house",
        allow_generate=True,
        force_generate=True,
    )
    assert called["wiki"] == 0
    assert called["gen"] == 1
    assert source == "grok"
    assert url == "/api/media/deck-house/house.jpg"


def test_fetch_image_force_generate_ignores_generated_cache(monkeypatch):
    called = {"gen": 0}
    monkeypatch.setattr(
        "app.image_library.lookup",
        lambda *_a, **_k: {"url": "/api/cache-media/concepts/old.jpg", "source": "grok", "id": "old", "metadata": {}},
    )

    def fake_gen(_card, _settings, deck_id):
        called["gen"] += 1
        return f"/api/media/{deck_id}/new.jpg", "grok"

    monkeypatch.setattr("app.services.images._generate", fake_gen)
    card = Card(id="c1", lemma="house", surface="house", language="en", french="maison")

    url, source = fetch_image(
        card,
        "grok",
        Settings(xai_api_key="xai-test"),
        deck_id="deck-house",
        allow_generate=True,
        force_generate=True,
    )

    assert called["gen"] == 1
    assert source == "grok"
    assert url.endswith("/new.jpg")


def test_fetch_image_retries_transient_grok_rate_limits(monkeypatch, tmp_path):
    png = base64.b64encode(b"\x89PNG\r\n\x1a\nretry-ok").decode("ascii")

    class RetryingClient:
        attempts = 0

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def post(self, *_args, **_kwargs):
            type(self).attempts += 1
            if self.attempts < 3:
                return FakeResponse(429, {"error": "rate limit"})
            return FakeResponse(200, {"data": [{"b64_json": png}]})

    sleeps: list[float] = []
    monkeypatch.setattr("app.services.images.httpx.Client", lambda *a, **k: RetryingClient())
    monkeypatch.setattr("app.services.images.time.sleep", sleeps.append)
    monkeypatch.setattr(
        "app.services.images.resolve_for_provider",
        lambda *_a, **_k: type("S", (), {"value": "test-key", "extra_headers": {}})(),
    )
    monkeypatch.setattr("app.services.images.MEDIA_DIR", tmp_path / "media")
    card = Card(id="c1", lemma="house", surface="house", language="en", french="maison")

    url, source = fetch_image(
        card,
        "grok",
        Settings(xai_api_key="xai-test"),
        deck_id="retry-deck",
        allow_generate=True,
    )

    assert source == "grok"
    assert url.startswith("/api/media/retry-deck/house-") and url.endswith(".jpg")
    assert RetryingClient.attempts == 3
    assert sleeps == [1.0, 2.0]


def test_fetch_image_uses_wikidata_photo_for_house(monkeypatch):
    routes = {
        ("search", "house", "en"): ["Q3947", "Q23558"],
        ("search", "maison", "fr"): ["Q3947"],
        "entities": {
            "Q3947": {
                "labels": {"en": {"value": "house"}, "fr": {"value": "maison"}},
                "aliases": {},
                "claims": {
                    "P31": [{"mainsnak": {"datavalue": {"value": {"id": "Q811102"}}}}],
                    "P18": [{"mainsnak": {"datavalue": {"value": "Ranch style home in Salinas, California.JPG"}}}],
                },
                "sitelinks": {"enwiki": {"title": "House"}},
            },
            "Q23558": {
                "labels": {"en": {"value": "House"}, "fr": {"value": "Dr House"}},
                "aliases": {"en": [{"value": "House M.D."}]},
                "claims": {
                    "P31": [{"mainsnak": {"datavalue": {"value": {"id": "Q5398426"}}}}],
                    "P18": [{"mainsnak": {"datavalue": {"value": "House_MD.jpg"}}}],
                },
                "sitelinks": {},
            },
        },
    }
    monkeypatch.setattr("app.services.images.httpx.Client", lambda *a, **k: FakeClient(routes))
    card = Card(id="c1", lemma="house", surface="house", language="en", french="maison")
    url, source = fetch_image(card, "wikimedia", _settings())
    assert source == "wikimedia"
    assert "Ranch" in url
    assert "House_MD" not in url


def test_fetch_image_skips_a_village_named_more(monkeypatch):
    routes = {
        ("search", "more", "en"): ["Q1972463"],
        ("search", "plus", "fr"): [],
        "entities": {
            "Q1972463": {
                "labels": {"en": {"value": "More"}, "fr": {"value": "More"}},
                "aliases": {},
                "claims": {
                    "P31": [{"mainsnak": {"datavalue": {"value": {"id": "Q532"}}}}],
                    "P625": [{"mainsnak": {"datavalue": {"value": {"latitude": 52.5, "longitude": -2.9}}}}],
                    "P18": [{"mainsnak": {"datavalue": {"value": "More, parish church of St. Peter.jpg"}}}],
                },
                "sitelinks": {"enwiki": {"title": "More, Shropshire"}},
            }
        },
        "wiki_search": {
            "1": {
                "title": "Thomas More",
                "thumbnail": {"source": "https://example.com/thomas-more.jpg"},
            }
        },
    }
    monkeypatch.setattr("app.services.images.httpx.Client", lambda *a, **k: FakeClient(routes))
    card = Card(id="c1", lemma="more", surface="more", language="en", french="plus")
    url, source = fetch_image(card, "wikimedia", _settings())
    assert url == ""
    assert source == ""


def test_unsplash_picks_photo_that_mentions_the_word(monkeypatch):
    routes = {
        "unsplash": [
            {
                "alt_description": "a flag flying at half-mast",
                "description": "mourning flag",
                "urls": {"regular": "https://example.com/flag.jpg"},
            },
            {
                "alt_description": "half an apple on a plate",
                "description": "sliced fruit showing one half",
                "urls": {"regular": "https://example.com/apple-half.jpg"},
            },
        ]
    }
    monkeypatch.setattr("app.services.images.httpx.Client", lambda *a, **k: FakeClient(routes))
    card = Card(id="c1", lemma="half", surface="half", language="en", french="moitié")
    url, source = fetch_image(
        card,
        "unsplash",
        Settings(unsplash_access_key="test-key"),
    )
    assert source == "unsplash"
    assert url == "https://example.com/apple-half.jpg"

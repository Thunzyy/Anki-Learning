import copy
import json
from pathlib import Path

import pytest

from app import paths, reviewed_corrections as rc, image_library


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    monkeypatch.setattr(paths, "DECKS_DIR", tmp_path / "decks")
    monkeypatch.setattr(paths, "MEDIA_DIR", tmp_path / "media")
    paths.DECKS_DIR.mkdir()
    paths.MEDIA_DIR.mkdir()
    (paths.MEDIA_DIR / "d").mkdir()
    (paths.MEDIA_DIR / "d" / "a.jpg").write_bytes(b"image a")
    (paths.MEDIA_DIR / "d" / "b.jpg").write_bytes(b"image b")
    row = dict(id="c", lemma="cat", surface="cat", language="en", french="chat", category="Nom",
               phrase="A cat sleeps.", phrase_fr="Un chat dort.", ipa="/kæt/", audio_url="/cat.mp3",
               image_url="/api/media/d/a.jpg", image_source="generated", image_concept="cat", image_scene="sleep",
               custom={"keep": True}, image_history=[{"reason": "original"}])
    deck = {"id": "en", "features": {"phrase": True, "phonetic": True}, "cards": [row]}
    path = paths.DECKS_DIR / "en.json"
    path.write_text(json.dumps(deck), encoding="utf-8")
    def manifest(fields=None):
        raw = path.read_bytes()
        current = json.loads(raw)["cards"][0]
        fields = fields or {"phrase": "The cat sleeps.", "phrase_fr": "Le chat dort."}
        new = {**current, **fields}
        image = image_library.local_path(new.get("image_url", ""))
        return {"version": 1, "decks": [{"id": "en", "expected_sha256": rc.sha(raw), "updates": [{
            "id": "c", "before_sha256": rc.hash_card(current), "fields": fields,
            "text_review": {"approved": True, "independent": True, "reviewer": "independent-qa", "after_sha256": rc.text_hash(new)},
            "image_review": {"compatible": True, "method": "visual-qa", "sha256": rc.sha(image.read_bytes()) if image else "", "context_sha256": rc.context_hash(new)},
            "ipa_review": {"before": current.get("ipa", ""), "after": new.get("ipa", ""), "reason": "pronunciation checked", "audio_url": current["audio_url"]}
        }]}]}
    return path, manifest


def test_preview_and_roundtrip_preserve_unknown_audio(setup):
    path, manifest = setup
    original = path.read_bytes()
    assert rc.apply_manifest(manifest())["applied"] is False
    assert path.read_bytes() == original
    assert not (paths.DATA_DIR / "image-library.sqlite3").exists()
    result = rc.apply_manifest(manifest(), apply=True)
    row = json.loads(path.read_bytes())["cards"][0]
    assert row["custom"] == {"keep": True} and row["audio_url"] == "/cat.mp3"
    assert row["image_history"][0] == {"reason": "original"}
    assert row["image_provenance"]["context_status"] == "verified"
    rc.rollback(Path(result["backup"]))
    assert path.read_bytes() == original
    assert not (paths.DATA_DIR / "image-library.sqlite3").exists()


@pytest.mark.parametrize("change", ["deck", "card", "text", "image", "context", "audio", "independent"])
def test_reject_invalid_evidence(setup, change):
    path, factory = setup
    m = factory()
    u = m["decks"][0]["updates"][0]
    if change == "deck":
        path.write_bytes(path.read_bytes() + b" ")
    elif change == "card":
        u["before_sha256"] = "wrong"
    elif change == "text":
        u["text_review"]["after_sha256"] = "wrong"
    elif change == "image":
        u["image_review"]["sha256"] = "wrong"
    elif change == "context":
        u["image_review"]["context_sha256"] = "wrong"
    elif change == "independent":
        u["text_review"]["independent"] = False
    else:
        u["fields"]["audio_url"] = "/new.mp3"
    with pytest.raises(ValueError):
        rc.apply_manifest(m, apply=True)
    assert not (paths.DATA_DIR / "image-library.sqlite3").exists()


def test_ipa_requires_explicit_review_but_not_image_review(setup):
    path, factory = setup
    m = factory({"ipa": "/kɑt/"})
    u = m["decks"][0]["updates"][0]
    u.pop("image_review")
    good = copy.deepcopy(m)
    u["ipa_review"]["audio_url"] = "wrong"
    with pytest.raises(ValueError, match="IPA"):
        rc.apply_manifest(m)
    rc.apply_manifest(good, apply=True)
    row = json.loads(path.read_bytes())["cards"][0]
    assert row["image_history"] == [{"reason": "original"}]
    assert row["audio_url"] == "/cat.mp3"


def test_phrase_disabled_and_grammar_cards(setup):
    path, factory = setup
    deck = json.loads(path.read_bytes())
    deck["features"]["phrase"] = False
    deck["cards"][0].update(phrase="", phrase_fr="")
    path.write_text(json.dumps(deck))
    rc.apply_manifest(factory({"category": "Nom commun"}), apply=True)
    m = factory({"category": "Grammaire", "image_url": "", "skip_image": True})
    rc.apply_manifest(m, apply=True)
    assert json.loads(path.read_bytes())["cards"][0]["image_url"] == ""


@pytest.mark.parametrize("fields", [{"category": "Grammaire"}, {"skip_image": True}])
def test_reject_images_for_existing_grammar_or_skip_policy(setup, fields):
    path, factory = setup
    before = path.read_bytes()
    with pytest.raises(ValueError, match="existing card policy"):
        rc.apply_manifest(factory(fields), apply=True)
    assert path.read_bytes() == before
    assert not (paths.DATA_DIR / "image-library.sqlite3").exists()


def test_reject_conflicting_unchanged_scene(setup):
    path, factory = setup
    other = json.loads(path.read_bytes())
    other["id"] = "pt"
    other["cards"][0]["image_url"] = "/api/media/d/b.jpg"
    (paths.DECKS_DIR / "pt.json").write_text(json.dumps(other))
    with pytest.raises(ValueError, match="conflicting"):
        rc.apply_manifest(factory())


def test_reject_nested_local_image_not_served_by_api(setup):
    path, factory = setup
    nested = paths.MEDIA_DIR / "d" / "nested"
    nested.mkdir()
    (nested / "a.jpg").write_bytes(b"nested valid local image")
    m = factory({"image_url": "/api/media/d/nested/a.jpg"})
    before = path.read_bytes()
    with pytest.raises(ValueError, match="served media route"):
        rc.apply_manifest(m, apply=True)
    assert path.read_bytes() == before


@pytest.mark.parametrize("tamper", ["deck", "backup", "registry"])
def test_rollback_revision_guards(setup, tamper):
    path, factory = setup
    result = rc.apply_manifest(factory(), apply=True)
    backup = Path(result["backup"])
    if tamper == "deck":
        path.write_bytes(path.read_bytes() + b" ")
    elif tamper == "backup":
        (backup / "en.json").write_bytes(b"{}");
    else:
        with image_library.database() as db:
            db.execute("INSERT INTO policy VALUES('wikimedia')")
    with pytest.raises(ValueError):
        rc.rollback(backup)
    assert json.loads((backup / "journal.json").read_bytes())["status"] == "complete"


def test_failure_restores_registry_and_written_decks(setup, monkeypatch):
    path, factory = setup
    original = path.read_bytes()
    # Failure after replacement, during the final journal write.
    real = rc._json_write
    def fail_complete(p, obj):
        if p.name == "journal.json" and obj["status"] == "complete":
            raise OSError("simulated disk failure")
        return real(p, obj)
    monkeypatch.setattr(rc, "_json_write", fail_complete)
    with pytest.raises(OSError):
        rc.apply_manifest(factory(), apply=True)
    assert path.read_bytes() == original
    assert not (paths.DATA_DIR / "image-library.sqlite3").exists()
    assert not (paths.DATA_DIR / ".reviewed-corrections.lock").exists()


def test_registry_backup_roundtrip(setup):
    path, factory = setup
    with image_library.database() as db:
        db.execute("INSERT INTO policy VALUES('wikimedia')")
    before = rc.registry_hash(paths.DATA_DIR / "image-library.sqlite3")
    result = rc.apply_manifest(factory(), apply=True)
    rc.rollback(Path(result["backup"]))
    assert rc.registry_hash(paths.DATA_DIR / "image-library.sqlite3") == before


def test_failed_rollback_restores_applied_state(setup, monkeypatch):
    path, factory = setup
    result = rc.apply_manifest(factory(), apply=True)
    after = path.read_bytes()
    registry_after = rc.registry_hash(paths.DATA_DIR / "image-library.sqlite3")
    real = rc._json_write
    def fail_rollback(p, obj):
        if p.name == "journal.json" and obj["status"] == "rolled_back":
            raise OSError("rollback journal failed")
        return real(p, obj)
    monkeypatch.setattr(rc, "_json_write", fail_rollback)
    with pytest.raises(OSError):
        rc.rollback(Path(result["backup"]))
    assert path.read_bytes() == after
    assert rc.registry_hash(paths.DATA_DIR / "image-library.sqlite3") == registry_after
    assert json.loads((Path(result["backup"]) / "journal.json").read_bytes())["status"] == "complete"


def test_blocked_source_is_rejected_without_registry_changes(setup):
    _, factory = setup
    with image_library.database() as db:
        db.execute("INSERT INTO policy VALUES('generated')")
    before = rc.registry_hash(paths.DATA_DIR / "image-library.sqlite3")
    with pytest.raises(ValueError, match="Blocked"):
        rc.apply_manifest(factory(), apply=True)
    assert rc.registry_hash(paths.DATA_DIR / "image-library.sqlite3") == before


def test_concurrent_deck_edit_is_preserved(setup, monkeypatch):
    path, factory = setup
    real = image_library.register
    concurrent = path.read_bytes() + b" "
    def concurrent_register(*args, **kwargs):
        value = real(*args, **kwargs)
        path.write_bytes(concurrent)
        return value
    monkeypatch.setattr(image_library, "register", concurrent_register)
    with pytest.raises(ValueError, match="Concurrent"):
        rc.apply_manifest(factory(), apply=True)
    assert path.read_bytes() == concurrent
    assert not (paths.DATA_DIR / "image-library.sqlite3").exists()


def orthographic_manifest(factory, path):
    deck = json.loads(path.read_bytes())
    deck["cards"][0].update(lemma="catt", surface="catt")
    path.write_text(json.dumps(deck), encoding="utf-8")
    m = factory({"phrase": "The cat sleeps."})
    u = m["decks"][0]["updates"][0]
    old = json.loads(path.read_bytes())["cards"][0]
    # Fixture intentionally contains a lexical typo; dual proof binds exact word.
    before = {"lemma": old["lemma"], "surface": old["surface"]}
    after = {"lemma": "cat", "surface": "cat"}
    u["word_correction"] = {
        "kind": "orthography", "same_lexeme": True, "reason": "Fixture spelling proof",
        "before_lemma": before["lemma"], "before_surface": before["surface"], **after,
        "author_review": {"approved": True, "same_lexeme": True, "reviewer": "author",
                          "before_sha256": rc.hash_card(before), "after_sha256": rc.hash_card(after), "evidence": "author.json"},
        "independent_review": {"approved": True, "same_lexeme": True, "reviewer": "reviewer",
                               "before_sha256": rc.hash_card(before), "after_sha256": rc.hash_card(after), "evidence": "review.json"},
        "audio_review": {"mode": "preservation_only", "pronunciation_unchanged": True,
                         "expected_audio_url": old["audio_url"], "expected_ipa": old["ipa"], "reason": "Same intended word; audio not listened."}}
    new = {**old, **u["fields"], **after}
    u["text_review"]["after_sha256"] = rc.text_hash(new)
    u["image_review"]["context_sha256"] = rc.context_hash(new)
    return m


def test_dual_reviewed_word_correction_preserves_id_language_audio(setup):
    path, factory = setup
    result = rc.apply_manifest(orthographic_manifest(factory, path), apply=True)
    row = json.loads(path.read_bytes())["cards"][0]
    assert row["lemma"] == row["surface"] == "cat"
    assert row["id"] == "c" and row["language"] == "en" and row["audio_url"] == "/cat.mp3"
    rc.rollback(Path(result["backup"]))
    assert json.loads(path.read_bytes())["cards"][0]["lemma"] == "catt"


@pytest.mark.parametrize("bad", ["different_lexeme", "stale_before", "same_reviewer", "wrong_after", "audio", "no_review"])
def test_reject_unreviewed_word_changes(setup, bad):
    path, factory = setup
    m = orthographic_manifest(factory, path)
    c = m["decks"][0]["updates"][0]["word_correction"]
    if bad == "different_lexeme":c["same_lexeme"] = False
    elif bad == "stale_before":c["before_surface"] = "wrong"
    elif bad == "same_reviewer":c["independent_review"]["reviewer"] = "author"
    elif bad == "wrong_after":c["independent_review"]["after_sha256"] = "wrong"
    elif bad == "audio":c["audio_review"]["expected_audio_url"] = "wrong"
    else:c.pop("independent_review")
    with pytest.raises(ValueError):rc.apply_manifest(m, apply=True)


def imported_manifest(factory, path):
    deck = json.loads(path.read_bytes())
    deck["cards"][0].update(language="pt", lemma="us", surface="us", french="nous", category="Grammaire",
                             phrase="Ele nos viu na rua.", phrase_fr="Il nous a vus dans la rue.", ipa="/us/",
                             image_url="", image_source="", skip_image=True)
    path.write_text(json.dumps(deck), encoding="utf-8")
    m = factory({"ipa": "/nɔs/"})
    u = m["decks"][0]["updates"][0]
    old = deck["cards"][0]
    before = {k: old[k] for k in rc.WORD_FIELDS}
    after = {"lemma": "nos", "surface": "nos"}
    def proof(reviewer):
        return {"approved": True, "same_intended_sense": True, "correct_target_language": "pt",
                "reviewer": reviewer, "evidence": reviewer + ".json", "before_sha256": rc.hash_card(before),
                "after_sha256": rc.hash_card(after)}
    new = {**old, **u["fields"], **after}
    u["text_review"]["after_sha256"] = rc.text_hash(new)
    u["word_correction"] = {
        "kind": "imported_token_correction", "same_intended_sense": True, "correct_target_language": "pt",
        "before_lemma": "us", "before_surface": "us", **after, "reason": "Original PT example and French nous establish nos.",
        "confirmed_import_error_evidence": {"foreign_token": "us", "intended_language": "pt", "french_gloss": "nous", "example": old["phrase"]},
        "author_review": proof("author"), "independent_review": proof("independent"),
        "root_review": {"approved": True, "reviewer": "root", "before_sha256": rc.text_hash(old), "after_sha256": rc.text_hash(new),
                        "reason": "Specific original import error reviewed", "evidence": "root-specific-review.json"},
        "audio_review": {"mode": "preservation_only", "audible_validation": False, "possible_obsolete": True,
                         "expected_audio_url": old["audio_url"], "expected_ipa": old["ipa"], "reason": "Audio not listened; may be obsolete."}}
    return m


def test_imported_token_requires_demonstrated_original_sense_and_preserves_audio(setup):
    path, factory = setup
    m = imported_manifest(factory, path)
    original = path.read_bytes()
    result = rc.apply_manifest(m, apply=True)
    row = json.loads(path.read_bytes())["cards"][0]
    assert row["lemma"] == row["surface"] == "nos"
    assert row["language"] == "pt" and row["id"] == "c" and row["audio_url"] == "/cat.mp3"
    assert row["ipa"] == "/nɔs/" and row["french"] == "nous"
    rc.rollback(Path(result["backup"]))
    assert path.read_bytes() == original


@pytest.mark.parametrize("bad", ["root_missing", "root_hash", "language", "meaning", "phrase_evidence", "sense", "synonym", "audio_claim", "audio_obsolete"])
def test_imported_token_rejects_unreviewed_or_unsupported_replacement(setup, bad):
    path, factory = setup
    m = imported_manifest(factory, path)
    u = m["decks"][0]["updates"][0]
    c = u["word_correction"]
    if bad == "root_missing":c.pop("root_review")
    elif bad == "root_hash":c["root_review"]["after_sha256"] = "wrong"
    elif bad == "language":c["correct_target_language"] = "en"
    elif bad == "meaning":u["fields"]["french"] = "vous"
    elif bad == "phrase_evidence":c["confirmed_import_error_evidence"]["example"] = "Invented evidence"
    elif bad == "sense":c["independent_review"]["same_intended_sense"] = False
    elif bad == "synonym":c.update(lemma="você", surface="você")
    elif bad == "audio_claim":c["audio_review"]["pronunciation_unchanged"] = True
    else:c["audio_review"]["possible_obsolete"] = False
    with pytest.raises(ValueError):rc.apply_manifest(m, apply=True)

def quarantine_manifest(factory, path):
    m = factory()
    old = json.loads(path.read_bytes())["cards"][0]
    u = {"id": old["id"], "operation": "quarantine_invalid_import", "before_sha256": rc.hash_card(old),
         "quarantine_review": {"approved": True, "reviewer": "root", "kind": "invalid_import",
             "before_sha256": rc.hash_card(old), "expected_lemma": old["lemma"], "expected_surface": old["surface"],
             "reason": "Invalid imported token, no evidenced replacement.", "evidence": "root-review.json"}}
    m["decks"][0]["updates"] = [u]
    return m


def test_quarantine_archives_all_progress_and_roundtrip(setup):
    path, factory = setup
    deck = json.loads(path.read_bytes())
    deck["cards"][0]["review_progress"] = {"due": "2030-01-01", "repetitions": 42}
    deck["card_count"] = 1
    path.write_text(json.dumps(deck), encoding="utf-8")
    before = path.read_bytes()
    m = quarantine_manifest(factory, path)
    assert not rc.apply_manifest(m)["applied"] and path.read_bytes() == before
    result = rc.apply_manifest(m, apply=True)
    live = json.loads(path.read_bytes())
    assert live["cards"] == [] and live["card_count"] == 0
    backup = Path(result["backup"])
    archive = json.loads((backup / "quarantine.json").read_bytes())
    assert archive[0]["card"] == deck["cards"][0]
    assert archive[0]["original_index"] == 0
    rc.rollback(backup)
    assert path.read_bytes() == before


@pytest.mark.parametrize("bad", ["root", "hash", "word", "fields", "operation"])
def test_quarantine_rejects_unreviewed_removal(setup, bad):
    path, factory = setup
    m = quarantine_manifest(factory, path)
    u = m["decks"][0]["updates"][0]
    if bad == "root":u["quarantine_review"]["reviewer"] = "author"
    elif bad == "hash":u["quarantine_review"]["before_sha256"] = "wrong"
    elif bad == "word":u["quarantine_review"]["expected_lemma"] = "wrong"
    elif bad == "fields":u["fields"] = {"french": "replacement"}
    else:u["operation"] = "delete"
    with pytest.raises(ValueError):rc.apply_manifest(m, apply=True)
    assert len(json.loads(path.read_bytes())["cards"]) == 1


def test_quarantine_archive_tampering_blocks_rollback(setup):
    path, factory = setup
    result = rc.apply_manifest(quarantine_manifest(factory, path), apply=True)
    backup = Path(result["backup"])
    (backup / "quarantine.json").write_bytes(b"[]")
    with pytest.raises(ValueError, match="archive"):
        rc.rollback(backup)
    assert json.loads(path.read_bytes())["cards"] == []

def contaminated_manifest(factory, path):
    m = imported_manifest(factory, path)
    deck = json.loads(path.read_bytes())
    old = deck["cards"][0]
    old.update(french="incorrect imported gloss", phrase="Ele us viu na rua.")
    path.write_text(json.dumps(deck), encoding="utf-8")
    spec = m["decks"][0];spec["expected_sha256"] = rc.sha(path.read_bytes())
    u = spec["updates"][0];u["before_sha256"] = rc.hash_card(old)
    u["fields"].update(french="nous", phrase="Ele nos viu na rua.")
    c = u["word_correction"];c["evidence_mode"] = "root_reviewed_contaminated_import"
    c["confirmed_import_error_evidence"].update(french_gloss=old["french"], example=old["phrase"], french_example=old["phrase_fr"])
    new = {**old, **u["fields"], "lemma": "nos", "surface": "nos"}
    u["text_review"]["after_sha256"] = rc.text_hash(new)
    for key in ("author_review", "independent_review"):
        c[key].update(before_content_sha256=rc.text_hash(old), after_content_sha256=rc.text_hash(new))
    c["root_review"].update(evidence_mode=c["evidence_mode"], before_sha256=rc.text_hash(old), after_sha256=rc.text_hash(new))
    return m


def test_explicit_contaminated_import_mode_binds_full_content(setup):
    path, factory = setup
    m = contaminated_manifest(factory, path)
    result = rc.apply_manifest(m, apply=True)
    card = json.loads(path.read_bytes())["cards"][0]
    assert card["surface"] == "nos" and card["french"] == "nous" and card["phrase"] == "Ele nos viu na rua."
    assert card["audio_url"] == "/cat.mp3"
    rc.rollback(Path(result["backup"]))
    assert json.loads(path.read_bytes())["cards"][0]["phrase"] == "Ele us viu na rua."


@pytest.mark.parametrize("bad", ["legacy_mode", "author_hash", "reviewer_hash", "root_mode", "source_translation", "unknown_mode"])
def test_contaminated_import_cannot_weaken_original_or_skip_full_reviews(setup, bad):
    path, factory = setup
    m = contaminated_manifest(factory, path);c = m["decks"][0]["updates"][0]["word_correction"]
    if bad == "legacy_mode":c.pop("evidence_mode")
    elif bad == "author_hash":c["author_review"]["after_content_sha256"] = "wrong"
    elif bad == "reviewer_hash":c["independent_review"]["before_content_sha256"] = "wrong"
    elif bad == "root_mode":c["root_review"].pop("evidence_mode")
    elif bad == "source_translation":c["confirmed_import_error_evidence"]["french_example"] = "Invented support"
    else:c["evidence_mode"] = "anything"
    with pytest.raises(ValueError):rc.apply_manifest(m, apply=True)


def source_copy_manifest(setup):
    path, _ = setup
    deck = json.loads(path.read_bytes())
    old = deck['cards'][0]
    old.update(language='pt', lemma='gato', surface='gato', french='chat', phrase='Um gato dorme.', phrase_fr='Un chat dort.', ipa='/ˈgatu/', image_url='', image_source='', skip_image=True)
    source = {**copy.deepcopy(old), 'id':'source', 'phrase':'O gato dorme no sofá.', 'phrase_fr':'Le chat dort sur le canapé.'}
    dest_path = paths.DECKS_DIR/'legacy-pt-2000.json'
    source_path = paths.DECKS_DIR/'legacy-pt-1000.json'
    dest_path.write_text(json.dumps({'id':'legacy-pt-2000','cards':[old]}),encoding='utf8')
    source_path.write_text(json.dumps({'id':'legacy-pt-1000','cards':[source]}),encoding='utf8')
    path.unlink()
    fields = {k:source.get(k) for k in rc.FIELDS-{'ipa'} if source.get(k)!=old.get(k)}
    historical = {'id':'source','fields':{k:source.get(k) for k in rc.FIELDS-{'ipa'}},'image_review':{'linguistic_review':{'approved':True}}}
    hist_path = paths.DATA_DIR/'history.json'
    rc._json_write(hist_path,{'decks':[{'id':'legacy-pt-1000','updates':[historical]}]})
    audit_path = paths.DATA_DIR/'audit.json'
    rc._json_write(audit_path,{'cards':[{'deck':slug,'id':row['id'],'card_hash':rc.hash_card(row),'text_status':'compatible','text_review_scope':'complete_text','phonetic_status':'compatible'} for slug,row in [('legacy-pt-1000',source),('legacy-pt-2000',old)]]})
    plan = {'status':'staged_pending_root_rule_review','source_deck':'legacy-pt-1000','source_ids':['source'],
            'destination':{'deck':'legacy-pt-2000','id':old['id'],'before_sha256':rc.hash_card(old)},
            'source_deck_sha256':rc.sha(source_path.read_bytes()),'source_card_sha256':rc.hash_card(source),
            'destination_deck_sha256':rc.sha(dest_path.read_bytes()),'fields':fields,
            'source_proof':{'audit_report':str(audit_path),'audit_report_sha256':rc.sha(audit_path.read_bytes()),
                            'historical_manifest':str(hist_path),'historical_manifest_sha256':rc.sha(hist_path.read_bytes()),'historical_update':historical}}
    plan_path=paths.DATA_DIR/'copy.json';rc._json_write(plan_path,plan)
    scripts=[]
    for name in ['planner.py','compiler.py']:
        script=paths.DATA_DIR/name;script.write_text('# reviewed producer',encoding='utf8');scripts.append(script)
    rule_path=paths.DATA_DIR/'rule.json'
    rc._json_write(rule_path,{'approved':True,'reviewer':'root','method':'reviewed_source_copy','reason':'Exact reviewed source, gloss and POS; preserve destination pronunciation.',
                             'script_sha256':{str(p.resolve()):rc.sha(p.read_bytes()) for p in scripts}})
    u={'id':old['id'],'before_sha256':rc.hash_card(old),'fields':fields,'reviewed_source_copy':{
        'plan':str(plan_path),'plan_sha256':rc.sha(plan_path.read_bytes()),'root_rule':str(rule_path),'root_rule_sha256':rc.sha(rule_path.read_bytes()),
        'planner_path':str(scripts[0]),'compiler_path':str(scripts[1])}}
    return dest_path, {'version':1,'decks':[{'id':'legacy-pt-2000','expected_sha256':rc.sha(dest_path.read_bytes()),'updates':[u]}]}


def test_reviewed_source_copy_is_explicit_and_preserves_audio_ipa(setup):
    path,m=source_copy_manifest(setup);before=path.read_bytes()
    assert 'text_review' not in m['decks'][0]['updates'][0]
    assert rc.apply_manifest(m)['applied'] is False
    result=rc.apply_manifest(m,apply=True)
    card=json.loads(path.read_bytes())['cards'][0]
    assert card['phrase']=='O gato dorme no sofá.' and card['ipa']=='/ˈgatu/' and card['audio_url']=='/cat.mp3'
    assert card['custom']=={'keep':True}
    rc.rollback(Path(result['backup']));assert path.read_bytes()==before


@pytest.mark.parametrize('bad',['root','source','plan','producer','fields','audit','historical'])
def test_source_copy_rejects_stale_or_unreviewed_evidence(setup,bad):
    path,m=source_copy_manifest(setup);u=m['decks'][0]['updates'][0];before=path.read_bytes()
    if bad=='root':u['reviewed_source_copy']['root_rule_sha256']='wrong'
    elif bad=='source':
        p=paths.DECKS_DIR/'legacy-pt-1000.json';p.write_bytes(p.read_bytes()+b' ')
    elif bad=='plan':u['reviewed_source_copy']['plan_sha256']='wrong'
    elif bad=='producer':(paths.DATA_DIR/'planner.py').write_text('# changed')
    elif bad=='fields':u['fields']['ipa']='/changed/'
    elif bad=='audit':(paths.DATA_DIR/'audit.json').write_text('{}')
    else:(paths.DATA_DIR/'history.json').write_text('{}')
    with pytest.raises(ValueError):rc.apply_manifest(m,apply=True)
    assert path.read_bytes()==before

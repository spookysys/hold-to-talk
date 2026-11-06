from voice.engine import foreign


def test_latin_text_with_umlauts_and_accents_is_not_foreign():
    for text in ["Hallo, wie geht es?", "Grüße aus Köln", "naïve café", "10 Minuten.", ""]:
        assert not foreign(text)


def test_other_scripts_are_foreign():
    for text in ["हेलो", "Привет", "こんにちは", "Hello हेलो"]:
        assert foreign(text)


def test_speech_is_loud_against_its_quiet_parts():
    import numpy as np
    from voice.engine import RATE, speechlike

    rng = np.random.default_rng(1)
    room = rng.normal(0, 0.001, RATE)
    voice = 0.2 * np.sin(2 * np.pi * 220 * np.arange(RATE) / RATE)
    assert speechlike(np.concatenate([room, voice, room]))
    assert not speechlike(np.concatenate([room, room]))
    assert not speechlike(np.zeros(100))
